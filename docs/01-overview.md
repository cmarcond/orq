# 1. Overview

## The problem

A single Claude Code serves one terminal, one account and one machine at a time. Someone who works
with several accounts (each with its own 5-hour and 7-day quota), several machines (a laptop, GPU
servers, a NAS) and several projects at once spends the day between terminal windows, `ssh`,
`tmux attach` and the question "which account still has quota?". claude-orq solves this in three
layers, each usable on its own:

1. **claude-multiconta**: each Claude Code account lives in its own directory (`CLAUDE_CONFIG_DIR`),
   with aliases to open it, log in and see its usage. `usage-all` shows the quota of all of them, with
   a pace marker and a TOTAL line for the fleet's week. `orq` opens up to four projects in a tmux
   grid, picking the account with the most slack for each one.
2. **orq-agent**: a supervisor that runs Claude Code without a terminal (`claude -p` in stream-json,
   the Agent SDK protocol) in the right folder and account, behind a Unix socket, in a tmux session on
   the machine. It survives an ssh drop; the conversation is a normal Claude Code session.
3. **orq-web**: a server and a page that show the quota, one tab per agent on any machine and a chat
   in the style of Remote Control. From the page you open projects, chat, answer permissions and
   questions, send and receive files, raise levels (teacher → executor), link agents to each other and
   let the account switch happen by itself when the quota runs out.

Nothing depends on an external service other than Claude Code itself: Python 3.8+ standard library on
the machines, ES modules in the browser, ssh and tmux between the machines.

## The parts and where they run

```
                 laptop (Mac)                             server0 (Linux, ZeroTier)
  ┌──────────────────────────────────┐        ┌─────────────────────────────────────────┐
  │ terminal: claude-<account>, orq, │        │ orq-web container (Python + ssh only)   │
  │ usage-all, claude-resume         │        │   page at http://<zerotier ip>:8765/    │
  │                                  │  ssh   │   talks to ALL machines over ssh,       │
  │ orq-agent (local agents)    ◄────┼────────┤   including the laptop and its own host │
  │   in tmux sessions orqs-<name>   │        │                                         │
  │                                  │        │ orq-agent (server0's agents)            │
  │ browser ─────────────────────────┼───────►│   in tmux sessions orqs-<name>          │
  └──────────────────────────────────┘        └─────────────────────────────────────────┘
                                                        │ ssh
                                              node0, node1, node2, node3 (orq-agent)
```

- The **page server** currently runs in a container on server0, published only on the ZeroTier IP,
  with a password login. It has neither `claude` nor credentials: everything that needs an account
  runs on the machines, over ssh. The quota is read over ssh on server0 (`ORQ_USAGE_HOST`). A local
  `orq-web` on the laptop also works (`127.0.0.1` only, token in the link), but by the author's
  decision it is not kept running, so as not to maintain two places.
- The **supervisor** (`orq-agent`) is installed on every machine, including the laptop, because the
  container reaches it over ssh.
- The **claude-multiconta utilities** (`usage-all`, `claude-last`, `orq`) are also on every machine:
  the server calls them there to list projects, read the quota and find out which agents exist.

## Concepts

| term | what it is |
|---|---|
| **account** | a Claude Code subscription, identified by its `name` in `accounts.json` (`work`, `gmail`, `work2`, `work3`). Each one has a `config_dir`; the default one uses `~/.claude` and is the one plain `claude` opens. |
| **machine (host)** | a name from `~/.ssh/config`: `server0`, `node0`…, listed in `orq_hosts`. The laptop is always `laptop`. |
| **project** | a folder on a machine. It is where Claude Code works and where it keeps the conversation (`<config_dir>/projects/<folder>/`). |
| **agent** | a Claude Code running under `orq-agent`, in a folder and an account. On the page it is a tab. |
| **label** | the agent's name: `<folder>@<machine>`, for example `kaggle@server0`. A level above becomes `kaggle~professor@server0`. A label repeated on the same machine gets `-2`, `-3`. |
| **session name** | the sanitized label, without `@` and `~` (`kaggle-server0`, `kaggle-professor-server0`): it is the `-n` claude starts with and the name `SendMessage` accepts. |
| **agent state** | `starting`, `working`, `waiting` (your turn), `permission` (asks for permission or asks a question), `error`, `quota` (account exhausted, switching), `closed`. |
| **quota window** | the Anthropic API reports, per account, a 5-hour window (`sessão 5h`), a 7-day window for all models (`semana 7d`) and a 7-day window per model. |
| **pace** | the fraction of the window that has already elapsed. An account is "on pace" when usage keeps up with time. |
| **level** | an agent above another (teacher, architect), created by a recipe, that talks to the one below through `SendMessage`. |
| **link** | any two agents that see each other in `ListAgents` and talk through `SendMessage`, on the same machine or not. |
| **_inbox / _outbox** | folders at the project root: what you send to the agent from the page lands in `_inbox/`; what it shares with you goes in `_outbox/`. |

## A typical session

1. You open the network page and see, at the top, the quota of each account and the TOTAL line for
   the week. Below, the tabs of the agents that already exist, one row per machine.
2. **+** on the server0 row: the list shows the folders with a recent conversation on all machines.
   You pick `~/Projects/kaggle`. The server picks the account with the most slack, starts `orq-agent`
   there over ssh (in a tmux session `orqs-kaggle`), and the tab `kaggle@server0` appears.
3. You type. The text goes through the bridge to the supervisor's socket, which hands it to
   `claude -p`. The answer arrives word by word; tools appear as collapsible chips; a permission
   becomes a card with Permitir/Negar (Allow/Deny); an `AskUserQuestion` question becomes a form.
4. You drag a PDF into the chat: it goes to the folder's `_inbox/`, on the machine, and the message is
   sent with the path. The agent generates a report and copies it to `_outbox/`: the 📂 lets you know
   and you see the preview.
5. The account runs out in the middle of the work. The supervisor marks `quota`, the server reopens
   the agent on the account with the most slack, copying the conversation, and resends your last
   message. The tab stays; a toast tells you.
6. You raise a teacher above the agent (**⇅ subir nível**, "raise level"): the tab now talks to it; it
   delegates to the executor through `SendMessage` and explains the milestones. Or you link the agent
   to another one, on another machine (**🔗**), and each asks the other for things.
7. You close the laptop. The agents keep running in the tmux sessions on the machines; tomorrow the
   page shows everything where it was.

## History, one line per stage

claude-multiconta (accounts and `usage-all`) → terminal `orq` (tmux grid) → `orq web` (local page)
→ `orq-web` as its own project, with a supervisor and tests → phases 1 to 4 (`orq-web/docs/PLANO.md`:
network and container, monorepo and CI, levels coming from claude-highlevel, automatic account
switch) → QA with Playwright → shared files (`_outbox/`) → links between agents and between machines →
teacher with several executors → adversarial review of the links.
