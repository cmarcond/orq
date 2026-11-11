# 4. The page (orq-web): user guide

Address: `http://<zerotier ip>:8765/` (password) or, with a local `orq-web`,
`http://127.0.0.1:8765/?t=<token>`. On a phone, use `http://<zerotier ip>:8765/m`: the same page with
a layout for a narrow screen (tabs in one row with sideways scrolling, the quota collapsed into a
strip with the TOTAL that opens the accounts on tap, the chat using the whole screen, the side panel
as a drawer). Through ZeroTier on the phone the access is direct, with no tunnel. Once you are in, the
page requests the state (`/api/state`) every 5 s and opens one stream per open tab.

The interface is in Portuguese; captions and status texts are quoted here as they appear on the page,
with a translation in parentheses the first time.

## 4.1 The top: quota

On the right of the top bar, one card per account with the `5h` and `7d` bars in the same format as
`usage-all` (bar = used, `┃` = pace, on the right the time until the reset; green/yellow/red according
to the slack) and, above them, the **TOTAL · semana** (week) card: the fleet's average bar, "quando
zera" (when it runs out) or `∞`, `queima N×` (burn) and `% livre` (% free). The quota is read every
150 s (the API complains about frequent queries) and only while the page is open. The 🔔 asks for
notification permission (see 4.11).

With the usage history running (claude-multiconta's `usage-hist`, collector every 5 min), the quota
comes from its database and the cards gain two more lines, read every 60 s
(`usage-hist painel --json`, in `/api/state` as `history`):

- **bars for the last 24 h**: one per hour, in the account's colour, with the same scale on every
  card; hovering over a bar shows the hour and the points spent in it (1 point = 1% of the account's
  weekly quota);
- **summary**: `hoje +N` (today: points of the week since midnight), the pace in points per hour over
  the last 6 h and `100% em …` (100% in …: when the account runs out at that pace); in red, with ⚠, if
  that comes before the reset. `*` marks what is still computed since the start of the collection.

The TOTAL card shows the points spent by the fleet in 24 h and warns if the collection stopped more
than 15 min ago. The collector's **alerts** (rise caused by a script, rise with no consumption on the
machines, quota that runs out before the reset, collector stopped) appear as a toast, each one once
every 6 h, and also as a **badge** on the card for as long as they last: `⚠ script`, `⚠ semana`,
`⚠ 5h`, `⚠ fora das máquinas` (outside the machines) in red (`⚠ 2` with more than one), and notices
such as `· processo parado` (idle process) in grey; the badge's title carries the full text, and the
TOTAL shows `⚠ N alertas` for the whole fleet (it includes collector stopped and machine not
answering). Without `usage-hist`, the page carries on with the quota only.

**Clicking an account's card** opens its panel (in the file-preview window; Esc or ✕ closes it):

- five numbers: % of the week and of the 5-hour window with the time until the reset, `hoje`, pace and
  the forecast (the time until 100%, in red when it comes before the reset);
- **week until the reset**: the % used since the start of the weekly window (solid line), the ideal
  pace of the window (grey dashed line, from 0% at the start to 100% at the reset), the mark for now
  and the projection at the current pace (dashed line in the account's colour, up to the reset or, in
  red, up to where it hits 100%);
- **last 24 h** (points per hour) and **last 7 days** (points per day, with the value on top);
- **what spent the most**, in 24 h or 7 days: machine, folder, type, sessions and ≈pts;
- the account's alerts and the calibration (how many tokens are worth 1 point).

The data comes from `GET /api/conta/<name>` (`usage-hist conta NOME --json`, with a 30 s cache).

**📊 uso** (usage), next to ⊞ conjuntos (sets), opens the same window with **who is spending each
account** in the last 5 h, 24 h or 7 days, on all machines: one block per account (with the quota) and
one line per machine, folder and type, with the runs of a script grouped (`n`), the time span, the
share of the account's consumption, ≈pts, `●` if it responded in the last 10 min and the live process
(tmux session or pid); forgotten processes are listed as `parado`. The lines of agents open on the
page are highlighted and take you to their tab with one click. It is the `usage-sessoes` table, built
from the database (`GET /api/uso?horas=5|24|168`, `usage-hist uso --json`, 30 s cache). On a phone
only machine, folder, share, ≈pts and `●` remain.

## 4.2 Tabs: one row per machine

Each machine has a row with the tabs of its agents and a **+** to open a project there. The tab shows
the folder name, a dot with the state (pulsing while it works; yellow when asking for permission; red
on error or out of quota), the account's colour, and suffixes: `⇅n` levels above, `🔗n` links,
`⌘1`…`⌘9` to switch with the keyboard, `✕` to stop it (twice; the conversation stays saved). A dashed
tab with *terminal* is an interactive claude in tmux, which can only be converted (4.12).

The order and the set of tabs come from the machines (`orq --status-json` every 15 s). A newly opened
tab appears right away, as provisional, until the machine confirms it; one that was stopped disappears
within seconds. A level above does not take up a tab: it lives inside the tab of the base agent.

## 4.3 Opening a project

**+** opens the picker: the most recent conversation of each folder, on all machines (machine, folder,
account, age, subject), most recent first. A click opens it with the conversation resumed
(`--mode resume`, with the conversation copied if the chosen account is a different one). The
`maquina:/caminho` (`machine:/path`) field opens a new folder (`--mode new`). The account is chosen by
the slack in the 7d window (3.6); the toast says which one and why. If there is already a terminal
claude open in that folder, the page refuses and tells you to convert it first, so as not to put two
processes on the same conversation.

## 4.4 The chat

- **Messages**: yours on the right (`me`), Claude's in sanitized Markdown, with the text arriving word
  by word (`▍` pulsing while it writes).
- **Tools**: each call becomes a collapsible chip with the name and a summary of the argument
  (command, file, pattern); ✓ or ✗ when it finishes; a click opens the result. `SendMessage` appears
  as `→ recipient` and a message from another session as `de sender` (from; see 4.7).
- **Permission**: a yellow card with the request and Permitir/Negar (Allow/Deny). **Question**
  (`AskUserQuestion`): a form with radio buttons or checkboxes, an "Outro…" (Other) field,
  Responder/Cancelar (Answer/Cancel). With `permission_mode: auto` Claude decides the routine cases
  and only asks about the sensitive ones; with `manual` it asks about everything.
- **Notices** (`sistema`): dashed chips for messages injected by the system (orq notes, hooks) and
  centred grey lines for supervisor events ("agente encerrado", agent stopped; "interrompido por
  você", interrupted by you).
- **State** in the bottom bar: account, level (if any) and `trabalhando…` (working), `esperando você`
  (waiting for you), `pede permissão` (asks for permission), `erro` (error), `abrindo…` (opening),
  `sem cota: trocando de conta…` (out of quota: switching account), `fechado` (closed).
- **Keyboard**: `Enter` sends, `Shift+Enter` breaks the line, `Esc` interrupts the turn (`■ parar`,
  stop, does too), `⌘1`…`⌘9` switch tabs, `Esc` closes the file preview.

Reopening the page, or another browser tab, recovers the whole conversation: the stream starts from
event `N` that the page already had, or from zero with the old history (up to 400 entries from the
session's `.jsonl`).

## 4.5 Attachments: `_inbox/`

**📎**, or dragging files into the chat, queues them as chips; on sending, each one is written to
`<project folder>/_inbox/<name>` on the agent's machine (over ssh when it is remote; up to 200 MB
each), and the message is sent with the paths, so that Claude reads the PDF or the spreadsheet
straight from there. With no text, the default message is "Veja o arquivo enviado." ("See the file
that was sent.")

## 4.6 Files the agent shares: `_outbox/`

The opposite direction. Every agent of the page starts with a note in the system prompt: to share a
file with you, copy it to `_outbox/` at the project root. **📂** in the chat bar opens the list in the
side panel: icon by type, name, size and age; a click opens the preview (image, PDF in the browser's
viewer, video and audio with a player, text and rendered Markdown) and **⬇** downloads; **↻** re-reads
the list. The list is re-read at the end of each agent turn and every 30 s; a new file produces the
toast "📂 nome: novo arquivo …" (name: new file), the count on the button and a highlight on the line.
The page never deletes anything from the folder.

Under the hood: `GET /api/agent/<x>/files` lists (`orq-agent ls` on the machine) and
`GET /api/agent/<x>/file?name=…` serves (`orq-agent cat`, with `Range` requests so the video can
seek). Every file goes out with `Content-Security-Policy: sandbox` and `nosniff`; HTML is never served
inline.

## 4.7 The side panel: levels, links, files

To the right of the chat, a panel with small tabs at the top shows one view at a time: **⇅ níveis**
(levels, when the agent has levels), **🔗 vínculos** (links, when it has links) and **📂 arquivos**
(files, when you open it). On narrow screens the panel slides over the chat; the buttons in the chat
bar open and close it.

**Wires** appear in the levels and links views: each `SendMessage` leaving an agent becomes a wire
`sender → recipient` with the summary and the text (a click opens it), marked ✓ when sent and ✓✓ when
the other side has received it; incoming messages appear as `sender → recipient ✓✓`.

## 4.8 Levels: teacher → executor

**⇅ subir nível** (raise level) picks a recipe (`orqweb/pipelines/*.toml`):

- `professor → executor`: a teacher above the tab's agent;
- `professor → arquiteto → executor`: two levels (teacher and architect);
- `professor → executores (este + vinculados)`: a teacher that coordinates the tab's agent **and** the
  agents linked to it, on any machine (4.9 below and chapter 6).

The levels start on the same machine, folder and account as the agent, each one as a normal agent of
the page, with the recipe's role in the system prompt and the neighbours' names resolved. The tab
starts talking to the top level; the original agent becomes the executor, hidden behind it, and
receives the work through `SendMessage`. The **níveis** view shows one card per level, from the top
down (a click opens that level's raw chat; a permission pending on a level can be answered right
there). **↓ desfazer** (undo; twice) stops the levels above and the tab goes back to the executor.

An agent opened before agents started being launched with a name cannot have levels (`SendMessage`
would not find it); the picker offers **↻ reabrir com nome** (reopen with a name), which closes and
reopens the same conversation.

## 4.9 Links: one agent asks the other

**🔗 vincular** (link) picks another agent of the page, on any machine. The two start seeing each
other in `ListAgents` and asking each other for things through `SendMessage`, as peers. Each one
receives the note `[orq] Vínculo criado com o agente …` (link created with agent …) giving the other's
name (folder, machine) and how to talk to it. The **vínculos** view shows one card per linked agent (a
click opens its tab; ✕ twice undoes the link) and the wires; **+ vincular** links one more. The tab
gets `🔗n` and the button becomes `🔗 n`.

With links in place, the `professor → executores` recipe starts a teacher that gets a link to each
agent linked to the tab's agent. Its role lists the executors (session name, machine, folder) and
tells it to distribute the work according to what each one has at hand, mediating when one needs the
other's result. **↓ desfazer** undoes the teacher's links and stops it; the links between the
executors stay.

A link between different machines goes through an ssh tunnel that the server opens on the first
agent's machine (the toast says "túnel ssh", ssh tunnel); it takes a few seconds longer.

## 4.10 Load balancing: automatic failover to another account

The page balances agents across your own accounts: each new agent opens on the account with the most
slack. This section covers the failover, when an account reaches its limit during a conversation.

When claude reports that the account is exhausted, the supervisor marks the agent as `quota` and keeps
your last message. On the next poll of the machines (up to 15 s), the server reopens the agent on the
account with the most slack, in the same conversation (copied), and resends the message. The tab
stays, goes through `abrindo…` and carries on; the toast says "conta X sem cota; continua na Y"
(account X out of quota; continuing on Y). `/api/state` carries `migrations` with the history.
`"auto_switch": false` turns it off.

## 4.11 Notifications and beep

The 🔔 asks the browser for permission. With the page out of view, each agent that asks for
permission, asks a question or finishes a turn produces a notification and a short beep; the browser
tab's title becomes `(2) orq` until you open each one. Browsers only give notifications on `https` or
`localhost`: over the network at `http://<ip>` you get the beep (clicking the bell unlocks the audio)
and the count in the title. To get notifications, open the page through a tunnel:
`ssh -N -L 8765:<zerotier ip>:8765 server0` and `http://127.0.0.1:8765/`.

## 4.12 Converting a terminal claude

An interactive `claude` running in tmux (opened by `claude-gmail` etc.) appears as a dashed tab with
**⇄ converter** (convert): the page sends it `/exit` (or Ctrl-C, or SIGTERM, in that order) and
reopens the same conversation as an agent of the page. In the terminal, `converte-web` lists and
converts on any machine.

## 4.13 Sets: opening several agents at once

**⊞ conjuntos** (sets), at the top next to the bell, opens the list of saved sets. A set is a JSON
file with several agents to open together: the jobs that were in progress (each in its own folder and
machine, conversation resumed) or a group with a **lead teacher**, in which each agent starts with its
own system prompt. Click the line to open it; **⬇** downloads the JSON; **✕** (twice) deletes it. At
the top, **📄 enviar JSON** (upload JSON) takes a ready-made file and **📷 salvar abas abertas** (save
open tabs) takes a snapshot of what is open (machines, folders, accounts, prompts, teacher) into a
JSON that you check, name and save. The files are kept in `~/.config/orq-web/conjuntos/` on the server
(`ORQ_SETS_DIR`; in the container, the `~/.config/orq-web` volume).

```json
{
  "nome": "artigo-kaggle",
  "descricao": "model on server0, text on node3, teacher coordinates",
  "permissao": "auto",
  "agentes": [
    {"maquina": "server0", "pasta": "~/Projects/kaggle", "modo": "resume",
     "conta": "work3", "prompt": "You look after the model…", "tarefa": "Pick up where you left off and report."},
    {"maquina": "node3", "pasta": "~/Projects/paper", "modo": "new", "prompt": "You write the paper…"}
  ],
  "lider": {"receita": "professor-executores", "prompt": "optional, replaces the role in the TOML", "tarefa": "…"}
}
```

The keys are Portuguese: `nome` (name), `descricao` (description), `permissao` (permission), `agentes`
(agents), `maquina` (machine), `pasta` (folder), `modo` (mode), `conta` (account), `tarefa` (task),
`lider` (leader), `receita` (recipe).

- Only `nome` and `agentes` (with `maquina` and `pasta`) are required. The default `modo` is `resume`
  (the folder's most recent conversation; a new one if there is none). `conta` fixes the account;
  without it, the choice by slack is made for the whole set at once, which spreads the load across the
  accounts. `permissao` (`auto`, `manual`, `acceptEdits`, `plan`) applies to all of them; without it,
  the machine's `permission_mode`.
- `prompt` goes into the agent's system prompt (`--append-system-prompt`, like the role of a level);
  in a resumed agent it enters a conversation that already had context, so groups with a teacher tend
  to be cleaner with `modo: new`. `tarefa` is the first message, sent as soon as the agent starts.
- **Without `lider`**, the agents are independent. **With `lider`**, the teacher starts above the
  first agent in the list and the others are linked to it (🔗) and become executors, exactly like ⇅
  with the `professor-executores` recipe (4.8 and 4.9); `lider.prompt` replaces the role in the TOML
  (it accepts `{abaixo}`, `{eu}`, `{executores}`) and `lider.tarefa` goes to the teacher after the
  agents' tasks.
- If any `maquina:pasta` of the set already has a live agent (from the page or from a terminal), the
  page **refuses and says which ones**: close them or open only the others by hand. The list shows "N
  já abertos" (N already open) on the line.
- Opening takes from a few seconds to a minute: each agent starts over ssh (in parallel across
  machines, in sequence on the same one), then come the links, the teacher and the tasks. The tabs
  appear as they start and the toasts report each step; the last one says how many started and lists
  problems, if any. The status bar of an agent opened this way shows `⊞ <set>`.
- Reopening a folder whose agent was stopped without **↓ desfazer** (from outside the page, or because
  claude crashed) stops the orphan levels that still pointed to that label, both through the **+** and
  through a set; otherwise the old teacher would attach itself to the new agent and ⇅ would refuse
  with "já tem níveis" (already has levels).
- The snapshot captures all open tabs: if one of them has levels above it, it becomes the first one
  and `lider` receives the recipe (only the first teacher; the other agents become its executors);
  `prompt` only appears on agents that started with one. The JSON can be edited before saving.
