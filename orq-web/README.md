# orq-web

Local chat with Claude Code agents spread across several machines and accounts, in the style of
Remote Control, without depending on claude.ai.

- **`orq-agent`** (supervisor, runs on each machine): runs `claude -p` in stream-json, the
  Agent SDK protocol, in the right folder and account, keeps the numbered events and exposes them on a
  Unix socket. It survives a dropped connection; whoever reconnects asks "from N onwards" and receives
  what it missed. The conversation is a normal Claude Code session: `claude --resume` in the terminal resumes it.
- **`orq-web`** (server + page, runs on the laptop): account quota at the top, one tab per agent,
  and a chat with Markdown, collapsible tools, permissions by button and forms for
  `AskUserQuestion`. It talks to the agents through `orq-agent attach`, locally or over ssh.

Extracted from the [`claude-multiconta`](../claude-multiconta) project, which still owns the accounts
(`accounts.json`) and the utilities this package calls: `claude-last` (session to resume and
list of projects), `usage-all --json` (quota) and `orq --status-json`
(agents per machine).

## Usage

```sh
./install.sh                 # this machine: copies the package to ~/.local/lib/orq-web and creates the commands (no pip)
./deploy.sh                  # laptop + all the machines in orq_hosts (or: ./deploy.sh server0 node1)
orq-web                      # opens http://127.0.0.1:8765/?t=<token> in the browser
orq-web --port 8766          # another port; a second orq-web on the same port reuses the link
```

**On the phone**: `http://<ip>:8765/m` is the same page with a different layout (`static/mobile.html` +
`mobile.css`, same `app.js`): tabs in one row with sideways scrolling, quota collapsed into a strip with the TOTAL
(a tap opens the accounts), chat with the whole screen, icon-only buttons and the side panel as a drawer. The
login returns to `/m` when you came in through it.

For development, `pip install -e ".[dev]"` (or `uv pip install -e ".[dev]"`) installs the package in
editable mode with the test tools.

On the page: **+** opens a project (list from all the machines, or `machine:/path`); `⌘1`–`⌘9`
switch tabs; `Enter` sends, `Shift+Enter` breaks the line, `Esc` interrupts; `✕` twice
closes the agent (the conversation stays saved). **📎** or dragging files onto the chat sends them to
`_inbox/` inside the project folder, on the agent's machine (over ssh when remote), and the message
goes out with the paths, so that Claude reads the PDF, the spreadsheet etc. straight from there.

## Links between agents (🔗)

**🔗 link** in the chat bar picks another agent of the page, on any machine: the two start to see each
other in `ListAgents` and to ask things of each other through `SendMessage`, with no imposed role (they are peers).
Each one receives a note `[orq] Link created with the agent …` giving the other's name and how to talk to
it. The side panel gains the **links** tab: one card per linked agent (a click opens its tab, ✕
twice undoes it) and the wires, each request and each reply through `SendMessage`. The tab shows `🔗n`.
`POST /api/agent/<x>/link {to}` and `/unlink {to}`; `/api/state` carries `links`.

**One teacher, several executors**: with links made, **⇅ add level** offers the recipe
`teacher → executors (this one + linked)`: it adds a teacher above the tab's agent and also links it
to each agent linked to that tab (on any machine). Its role lists the executors
(`{executores}`: session name, machine and folder) and tells it to distribute the work according to what each one
has at hand, mediating when one needs the other's result. **↓ undo** undoes the teacher's
links and closes it; the links between the executors stay.

Underneath, `orq-agent link A B` on `A`'s machine. On the same machine there is no tunnel, but `ListAgents`
only reads the `sessions/` folder of its own account: agents of different accounts get each other's card and
key mirrored (same socket). Between machines, the ssh tunnel below. The levels (teacher →
executor) remain a recipe on top of the same mechanism.

## Link between machines (`orq-agent link`)

The levels use Claude Code's native `SendMessage`/`ListAgents`, which only see sessions on the same
machine: each live claude has in `<config_dir>/sessions/` a card `<pid>.json` (name, folder, state and
the Unix socket on which it receives messages) and a `<pid>.<hash>.key` next to it. `orq-agent link A@here B@there`
stretches this over ssh: it starts an `ssh -N` with each agent's socket forwarded to the other machine
(`-L`/`-R` with Unix paths) and mirrors each one's card and key into the other's `sessions/` folder,
pointing to the forwarded socket. From then on `A` and `B` list each other and talk as if they were local. The
link lives in a tmux session `orql-<a>--<b>`; `orq-agent unlink` (or one of the agents closing, or the tunnel
dropping) closes the tunnel and deletes the mirrors on both machines, except those another live link still uses: the
tab's agent and the teacher above it, linked to the same remote executor, share the same card and the
same forwarded socket (`orq-<pid>.sock`, a name no real session uses), and whoever remains takes over
the forwarding. On the same machine, the mirror between accounts follows the same rule, removed when one of the
agents closes. `orq-agent links` lists them; `orq-agent peer` is the remote
side (prints the card, writes or deletes a mirror). Tested on 2026-10-12 between laptop (OpenSSH 10) and
server0 (OpenSSH 8.2), without touching sshd. It is the basis of the links between chats on the page (to do).

## Files the agent shares (`_outbox/`)

The reverse path of 📎: every agent of the page starts with a note in the system prompt saying that, to
share a file with you (PDF, image, video, spreadsheet…), it is enough to copy it to `_outbox/` at the
root of the project. **📂** in the chat bar opens the list in the side panel (the same panel as the levels, with
small tabs at the top): name, size, age; a click shows the preview (image, PDF in the browser's
viewer, video, audio, text and Markdown) and ⬇ downloads. The list is reread at the end of each round and every
30 s; a new file becomes a toast and a highlight. The folder stays on the agent's machine: the server lists it with
`orq-agent ls` and serves it with `orq-agent cat` (over ssh when remote; `Range` ranges so the video can seek
in time). `GET /api/agent/<x>/files` and `GET /api/agent/<x>/file?name=…[&dl=1]`. The one who writes to the folder
is the agent; the page never deletes anything from it.

## Levels (teacher → executor)

Any agent of the page can get a **level above**: **⇅ add level** in the chat bar picks a
recipe (`orqweb/pipelines/*.toml`: `teacher → executor` or `teacher → architect → executor`) and starts
the levels on the same machine, folder and account, each one as a normal agent of the page (`orq-agent spawn
--role-file - --parent … --level …`). The tab starts talking to the level above; the original agent
becomes the executor, hidden behind, and receives the work through Claude Code's native `SendMessage` (the
sessions find each other through `ListAgents`). The panel on the right shows one card per level (state; click to
see that level's raw chat) and the **wires**: each message between levels, marked ✓✓ when it arrives on the
other side. **↓ undo** closes the levels above and the tab returns to the executor.

For this every agent starts with `claude -n <name>`, the name `SendMessage` uses: the sanitised label
(`kaggle@server0` → `kaggle-server0`; the `@` is refused). An agent opened before this version has no
name: the level picker offers **↻ reopen with a name** (`POST /api/agent/<x>/reopen`, which runs
`orq-agent spawn --mode resume --restart` and resumes the same conversation). The levels start in the same
`permission_mode`: different modes make Claude Code hold the message between them. If a level asks
permission, its card in the panel shows the request with Allow/Deny; a question (`AskUserQuestion`)
opens that level's chat.

## Sets (⊞): several agents at once

**⊞ sets** at the top lists JSONs saved in `~/.config/orq-web/conjuntos/` (`ORQ_SETS_DIR`): each one
describes agents to open together (machine, folder, `modo`, optional `conta`, its own `prompt` in the system
prompt, initial `tarefa`), one `permissao` for all and, optionally, a `lider`: the teacher is added above the
first agent and the others are linked to it as executors (recipe `professor-executores`, role
replaceable by `lider.prompt`). Without `lider`, independent agents. The page refuses to open if any
`maquina:pasta` already has a live agent. **📄 upload JSON** takes a ready-made one; **📷 save open tabs**
photographs what is open into a new set. The server opens it in a thread (`handlers.open_set_job`) and the
progress goes in `conjuntos_abrindo` of `/api/state`. Format and details: `docs/04-orq-web-page.md`, 4.13.

## Quota ran out: account switch on its own

When claude warns that the account is exhausted (`rate_limit_event` with `status: rejected`, or a turn that
ends in a limit error), the supervisor marks `state: quota` and keeps the page's last message in
`retry`. On the next query to the machines, the server reopens the agent on the account with the most slack
(`orq-agent spawn --mode resume --restart`; `claude-last --as` copies the conversation to the new account) and
resends the message. The page warns with a toast and the tab continues in the same conversation; `/api/state` carries
`migrations`. Turn it off with `"auto_switch": false` in `accounts.json`.

## Notifications

The 🔔 at the top asks the browser for permission once. After that, with the page out of sight, each agent that
asks permission, asks a question or finishes a turn produces a notification and a beep; the tab title shows
how many agents are waiting for you, for example `(2) orq`, until you open each one. Browsers only give
notifications on `https` or `localhost`: over the network on `http://<ip>` the bell warns of this and only the beep
remains (the click on it unlocks the audio) plus the count in the title. To have notifications on the laptop, open the page through a
tunnel: `ssh -N -L 8765:<ip zerotier>:8765 server0` and `http://127.0.0.1:8765/`.

An interactive `claude` that is already running in a tmux (opened by `claude-gmail` etc.) appears on the
page as a dashed tab with the button **⇄ convert**: the page gives it `/exit` (or Ctrl-C, or
SIGTERM, in that order) and reopens the same conversation as an agent of the page. In the terminal:

```sh
converte-web                     # lists the interactive claudes in tmux on all the machines
converte-web kaggle@server0    # converts one (or --all)
```

**Mac as a remote machine.** A Mac that receives agents over ssh needs a tmux server born in the
graphical session, because the Keychain (where Claude Code keeps the credential) does not open for ssh sessions.
`deploy/macos-tmux-keep.sh` installs a user LaunchAgent that keeps a `keep` session alive from
login; the agents join that server and the remote quota is read with `tmux run-shell`. It also needs
`sshd` turned on (`sudo systemsetup -setremotelogin on`) and a `~/.zshenv` with `~/.local/bin` and
`/opt/homebrew/bin` in the `PATH`, since ssh sessions do not read `.zshrc`.

On the machines, the server calls `~/.local/bin/orq-agent`:

```sh
orq-agent spawn --dir ~/acervo --account gmail --label acervo@node0   # what the page runs
orq-agent list                                                          # agents on this machine
orq-agent stop acervo@node0                                           # closes one
orq-agent attach acervo@node0 --since 0                               # raw bridge (debug)
orq-agent convert kaggle@server0                                      # what converte-web runs there
orq-agent spawn --dir ~/acervo --account gmail --label acervo~professor@node0 \
          --mode new --role-file - --parent acervo@node0 --level professor  # level (role through stdin)
```

Configuration (in claude-multiconta's `accounts.json`): `permission_mode` of the page's agents
(`auto` lets Claude decide the routine things; `manual` asks everything), `orq_hosts` and `auto_switch`
(default `true`: switch account on its own when the quota runs out).
Variables: `CLAUDE_MULTICONTA_CONF`, `ORQ_BIN` (where the utilities are), `ORQ_STATE_DIR`
(default `~/.cache/orq`), `ORQ_NO_OPEN=1` (does not open the browser), `ORQ_USAGE_HOST` (machine that
answers the quota, over ssh; for the server to run where there are no credentials).

## Over the network (container on server0)

`deploy/push-server0.sh` sends the code to server0 and runs `deploy/server0/deploy.sh` there: it generates the
password in `~/.config/orq-web/password` (if there is none), an `ssh_config` just for the container with the
machines' IPs, authorises ssh to the machine itself and brings up the compose published only on the ZeroTier IP.
The image has only Python and the ssh client: agents, quota and utilities run on the machines, over ssh
(`ORQ_LOCAL_HOST=none`). To get in: `http://<ip zerotier>:8765/`, password `ssh server0 cat
~/.config/orq-web/password`. Variables of the server on the network: `ORQ_BIND` (outside 127.0.0.1 it requires the
password), `ORQ_PASSWORD_FILE`, `ORQ_ALLOWED_HOSTS`, `ORQ_LOCAL_HOST`, `ORQ_EXTRA_HOSTS`, `ORQ_SSH_CONFIG`,
`ORQ_USAGE_HOST`.

## Architecture

```
browser ──SSE/POST──> orq-web (handlers) ──Bridge (orq-agent attach, local or ssh)──> Unix socket
                              │                                                           │
                        StateCache ──> External (usage-all, claude-last, orq)         Supervisor
                                                                                          │
                                                                                 claude -p stream-json
```

| module | responsibility |
|---|---|
| `orqweb/config.py` | reads `accounts.json`; `claude` environment per account; state paths |
| `orqweb/agent/events.py` | numbered log with replay, origin stamp (`page`/`injected`), derived state, old history |
| `orqweb/agent/supervisor.py` | process + socket + clients; clean shutdown |
| `orqweb/agent/client.py` | `attach` (stdin/stdout bridge), `stop`, `spawn` in tmux `orqs-<name>` |
| `orqweb/agent/files.py` | `_outbox/`: lists the folder and sends bytes with the size in front (`orq-agent ls`/`cat`) |
| `orqweb/agent/link.py` | link between machines: session registry, mirror, ssh tunnel and life cycle (`orq-agent link`) |
| `orqweb/agent/state.py` | atomic `state.json`, read by `orq --status-json` |
| `orqweb/agent/convert.py` | closes an interactive tmux `claude` by escalation and reopens the conversation as an agent |
| `orqweb/convert.py` | CLI `converte-web`: lists and converts on any machine |
| `orqweb/server/external.py` | calls to the utilities and to the machines (ssh), through an injectable `Runner` |
| `orqweb/server/assign.py` | choice of the account by the slack in the 7d and label `folder@machine` |
| `orqweb/server/state.py` | cache of quota/agents/projects; background loops only with the page open |
| `orqweb/server/bridge.py` | one bridge per tab; passing bridge to deliver without a tab |
| `orqweb/server/migrate.py` | account switch when the quota runs out: picks the account with slack, reopens with `--restart`, resends the message |
| `orqweb/server/pipelines.py` | level recipes (`orqweb/pipelines/*.toml`), labels `base~level@host`, roles with the neighbours resolved |
| `orqweb/server/sets.py` | sets (⊞): JSON format, validation, catalogue, conflicts, plan and photograph of the open tabs |
| `orqweb/server/handlers.py` | routes, SSE, token, CSP, static files, levels (`/api/pipelines`, `upgrade`, `downgrade`), sets (`/api/conjuntos`, `open_set_job`) |
| `orqweb/server/app.py` | port, link with token, second server, shutdown |
| `static/js/format.js` | escaping, times, sanitised Markdown |
| `static/js/classify.js` | internal records, machine origin, tool summaries |
| `static/js/events.js` | pure reducer: stream event -> interface actions |
| `static/js/app.js` | tabs, EventSource, DOM, composer, permissions, project picker |

### Socket protocol

First line from the client: `{"since": N}`. After that, JSON lines in both directions, the same as those of
the stream-json of `claude -p`, plus:

- `{"type": "orq", "level": ..., "text": ...}` notices from the supervisor;
- `{"type": "orq", "subtype": "permission_answered", "request_id": ..., "behavior": ...}` the page
  answered a permission (the replay closes the card);
- `user` messages gain `orq_origin`: `page` (echo of what the page sent), `agent` (came from another
  session through SendMessage: `origin.name` says who, `origin.body` is the text without the wrapper) or
  `injected` (no human mark: cron, loop, system);
- `{"orq": "quit"}` from the client closes the agent.

`stream_event` (partial text) only goes live; it does not enter the sequence or `events.jsonl`.

### Security

Server only on `127.0.0.1`, with `Host` checked; `/api` requires the link's token (`X-Orq-Token`,
or `?t=` in the SSE, which does not accept headers); Content-Security-Policy restricts scripts to `'self'`
and to cdnjs; the model's Markdown goes through DOMPurify; `/static` does not accept traversal; the agent's
socket is `0600`. A file from `_outbox/` goes out with `Content-Security-Policy: sandbox` and `nosniff`, and
HTML is never served inline (an `.html` written by the agent does not run in the page's origin); only names
without a path, inside the agent's `_outbox/`.

## Development

```sh
make test       # pytest + node --test
make lint       # ruff check + format --check
make fmt
```

The integration tests use `tests/fake_claude.py`, which imitates the stream-json protocol
(permission, AskUserQuestion, interruption, injection, streaming, and `SendMessage` between two fake
processes through a mailbox in `FAKE_PEER_DIR`), so the CI does not need Claude.
`tests/test_supervisor.py` covers the supervisor through the socket; `tests/test_handlers.py` starts the
real HTTP server with the supervisor behind the bridge. Unix sockets require short paths, which is why
these tests use `/tmp`.

The CI (GitHub Actions) runs ruff and pytest on Python 3.8, 3.11 and 3.12, and the frontend tests
on Node 22. The supervisor runs on the remote machines, which is why the code stays compatible with 3.8.

## To do

- history of level rounds and a timeline of milestones per project;
- model and effort per level in the recipe; recipes editable from the page;
- sets: progress in the panel (today only toasts), opening "only the missing ones" when part is already open,
  `lider.prompt` in recipes with more than one level.
