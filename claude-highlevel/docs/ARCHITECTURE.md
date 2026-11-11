# HighLevel — architecture

A web app to run, watch and talk to a **pipeline of Claude Codes at several levels of
abstraction**. The top level translates; the lower ones do the work.

```
       YOU (browser)
            │  chat
            ▼
   ┌────────────────────┐
   │ level 1: professor │  interface + narration, never does the task
   └────────┬───────────┘
            │  SendMessage / ListAgents  (Claude Code's native channel, over UDS)
            ▼
   ┌────────────────────┐
   │ level 2: executor  │  does the work, reports milestones only
   └────────────────────┘

   each session writes ~/.claude/projects/<key>/<session-id>.jsonl
                          │
                          ▼
              tailer → normalizer → bus → WebSocket → UI
```

The principle is the one from the experiment we already validated in the terminal: **the
app does not invent a communication protocol between agents**. It uses what Claude Code
already has (peer sessions that find each other with `ListAgents` and talk with
`SendMessage`) and solves what was missing: instead of three Terminal windows and a
`tail -f | jq`, a single interface.

---

## 1. What is already validated (and becomes a requirement)

From the experiment in `~/.claude/projects/-Users-user-Projects-test`:

- two interactive sessions in the **same directory** see each other with `ListAgents` and
  exchange messages with `SendMessage` (transport: `uds:/tmp/cc-socks/<pid>.sock`);
- the JSONL of the professor's session holds, **in structured form**, everything the UI
  needs — including the message that arrived from the executor, already separated from
  the wrapper text;
- filtering `type=="assistant"` + `content[].type=="text"` yields exactly the "clean
  window": the narration, without the `@ executor❯` blocks.

Two findings from this session's spike that change the design for the better:

- **`claude --session-id <uuid>`**: we choose the session id, so we know the path of the
  JSONL before starting the process. The `PROFESSOR_BOOT_…` marker + `grep -l` hack to
  find out which file belongs to whom goes away.
- **`claude -n <name>`**: the session name (what the peer uses as the address) is passed
  as a flag. The `/rename` typed through `osascript` goes away.

And three findings that become mandatory work:

- **boot gates**. `--permission-mode bypassPermissions` opens a dialog
  (`Yes, I accept`) before anything else, and a new directory opens the trust dialog.
  If this is not handled, the session never gets to write the JSONL — that is exactly
  what blocked the first version of the spike.

- **the transcript is no good as a "session ready" signal**. The JSONL is only created
  on the first turn, and a level that has not received any message yet would never have
  one — waiting for it is a deadlock: the executor only gets a transcript when the
  professor talks to it, and the professor only talks after everyone is ready. The right
  signal is the peer socket, `/tmp/cc-socks/<pid>.sock`, created with the exact pid we
  spawned, plus the `❯` of the input box drawn on the screen.

- **the levels must share the same permission mode class**. With the professor in
  `acceptEdits` and the executor in `bypassPermissions`, the first message between them
  is **held**: *"The sending session's permission mode class doesn't match this
  session's"*. The destination session shows a review dialog with the focus on **Deny**,
  and the pipeline stalls before it starts — with none of this showing up in the
  transcript. That is why the permission mode belongs to the **run**, not to the level.

---

## 2. Structural decisions

| Decision | Why |
|---|---|
| **Pipeline in TOML**, not YAML | `tomllib` has been in the standard library since 3.11; YAML would be the project's first external dependency |
| **Interactive sessions in a PTY**, not `claude -p` | Print mode is one-shot and is not registered as a peer. The pipeline needs sessions that are alive, idle and addressable. |
| **Truth = JSONL**, not the terminal output | The PTY is a TUI full of ANSI, spinners and redraws. The JSONL is an append-only log that is already structured, with `origin.kind` separating human from peer. |
| The PTY is captured anyway | It is where boot dialogs and permission prompts are answered, and where the UI types. It lives in a "raw terminal" drawer. |
| **The pipeline is data**, not code | Two levels is today's case; three (professor → arquiteto → executor) is just one more entry in the list. |
| **Python + FastAPI** backend, **plain JS** frontend | `pty` is stdlib (no `node-pty`/compilation). Zero build step on the front end. |
| One working directory per run | It is what makes the peers see each other, and it limits the damage the executor can do. |

---

## 3. Components

```
hl/
  config.py        paths, log key (cwd → -Users-user-…), defaults
  gates.py         recognizes and answers boot dialogs (trust, bypass)
  session.py       SessionProcess: PTY, spawn, stdin, ring buffer, liveness
  tailer.py        JsonlTailer: asynchronous tail -F, tolerant of partial lines
  events.py        JSONL → Event normalizer
  bus.py           fan-out to the WebSockets + buffer for replay
  orchestrator.py  Run: life cycle, start order, bootstrap, stop
  server.py        FastAPI: REST + WS + static files
web/               index.html, app.js, style.css
pipelines/         professor-executor.toml, professor-arquiteto-executor.toml
runs/<run_id>/     run.json, events.ndjson, pty-<level>.log
```

### SessionProcess

Starts `claude` in a PTY with:

```
claude -n <name>-<run tag> \
       --session-id <uuid> \
       --append-system-prompt <the level's role> \
       --permission-mode <mode> \
       [--model …] [--effort …]
```

`--append-system-prompt` is what sets the role **without spending a conversation turn** —
unlike the experiment, where the professor's role was the first user message.

### Normalizer (the heart of the viewer)

Each JSONL line becomes zero or more events:

| record | condition | `kind` | payload |
|---|---|---|---|
| `assistant` | `content[].type=="text"` | `narration` | `{text}` |
| `assistant` | `content[].type=="thinking"` | `thinking` | `{text}` |
| `assistant` | `tool_use` name `SendMessage` | `wire_out` | `{to, summary, message}` |
| `assistant` | other `tool_use` | `activity` | `{tool, brief}` |
| `user` | `origin.kind=="peer"` | `wire_in` | `{from, body, msg_id}` |
| `user` | `origin.kind=="human"` | `prompt` | `{text}` |
| `user` | `content[].type=="tool_result"` | `activity_done` | `{ok, brief}` |
| `user` | `isMeta` + `<system-reminder>` | — | discarded |
| `custom-title` | | `status` | `{name}` |
| `cost-state` | | `cost` | `{usd, tokens}` |

`origin.body` already carries the peer's text **clean**, without the "Another Claude
session sent a message… permission laundering…" wrapper. It is what feeds the panel with
the conversation between the levels, with no regex at all.

Normalized event:

```json
{"run_id":"r_a3f1","seq":128,"ts":"…","level":2,"session":"executor",
 "kind":"wire_out","payload":{"to":"professor","summary":"…","message":"…"}}
```

### Life cycle of a run

1. `POST /api/runs {pipeline, task, cwd}` → validates the cwd (does it exist? is it trusted?).
2. Generates a `run_id` and one session UUID per level.
3. Starts the levels **from the bottom up** — the executor must already be registered
   when the professor calls `ListAgents`.
4. For each level: waits for the socket in `/tmp/cc-socks` **and** the first line of the
   JSONL; `gates.py` answers the boot dialogs in the meantime.
5. Injects the task into level 1's stdin.
6. Tailers → normalizer → bus → WebSocket.
7. `POST …/stop`: SIGTERM to the process group. `runs/<id>/` stays for replay.

---

## 4. Interface

```
┌───────────┬────────────────────────────────────┬──────────────────┐
│ LEVELS    │  STAGE                             │  WIRES           │
│           │                                    │                  │
│ ● you     │  only the professor's narration.   │ professor→executor│
│ ● profes… │  large font, markdown,             │ executor→professor│
│   idle    │  no tool call, no thinking,        │ collapsed cards  │
│ ● execut… │  no @executor❯ block.              │ colored by level │
│   working…│  it is the "third window" of       │                  │
│   $0.42   │  the experiment.                   │ [ ] tool calls   │
│           │                                    │ [ ] thinking     │
├───────────┴────────────────────────────────────┴──────────────────┤
│ > talk to: (professor ▾)  ________________________________  [↵]   │
└───────────────────────────────────────────────────────────────────┘
```

- **One color per level** (professor cyan, executor amber, you white), the same in every
  panel: it is what keeps the screen "clean and colorful" without turning into a rainbow.
- **State per level**: idle / working / **blocked** (a permission prompt waiting for an
  answer) / dead.
- A **raw terminal drawer** per session, for debugging and for answering by hand.
- Everything that is not narration sits behind a toggle. The default is the clean stage.

### API

```
GET  /api/pipelines                    available presets
POST /api/runs                         {cwd, task, permission_mode} → run state
GET  /api/runs, /api/runs/{id}         state + levels
GET  /api/runs/{id}/events?since=<seq> history
GET  /api/runs/{id}/stream?since=<seq> SSE: live events, with replay
GET  /api/runs/{id}/pty?nivel=&linhas=  raw terminal of one level
POST /api/runs/{id}/message            {to, text}
POST /api/runs/{id}/keys               {to, data}  raw keys (dialogs)
POST /api/runs/{id}/allow              unblocks the stuck level
POST /api/runs/{id}/stop
```

**Live and archived runs.** `GET /api/runs` merges what is in memory with what is in
`runs/`, and a finished run still opens after the server dies: `run.json` keeps the task,
cwd and levels, and `events.ndjson` keeps the whole run. For an archived run, `stream`
delivers the history and sends a `fim` event — without it the `EventSource` would
reconnect in a loop to a source that has nothing more to say. Old runs without a
`run.json` still show up: the manifest is rebuilt from the events themselves.

**SSE instead of WebSocket** (a deviation from the original plan, decided in phase 2).
The flow is one-way — server → browser — and SSE already comes with reconnection through
`Last-Event-ID`, which is exactly our `seq`. The browser's `EventSource` reconnects by
itself and the server replays only what was missed, without a single line of reconnection
code. As a bonus, the whole project stays free of external dependencies: `http.server`
from the standard library.

---

## 5. Command channels — from the interface inward

The app owns the PTY of each level, so writing to stdin is **indistinguishable from you
typing in that terminal**. That gives three channels, all verified in a spike:

| channel | how | verified |
|---|---|---|
| **Talk** | text into the stdin of the chosen level — the top one by default, any of them by selection | yes |
| **Queue** | send text while the level is working; Claude Code absorbs it into the turn in progress | yes — the extra instruction came in mid-task and was carried out |
| **Raw keys** | `Enter`/arrows to answer a dialog, `Esc` to interrupt, `shift+tab` to cycle the permission mode | yes, in the boot and permission dialogs |
| **Slash command** | same text path: `/rename`, `/compact`, `/model` | yes — `/rename painel-web` produced a `custom-title` in the JSONL |

Implementation detail: user text goes in as a **bracketed paste**
(`ESC[200~ … ESC[201~`) before the `Enter`. Without it, multi-line text is submitted at
the first line break, and a message starting with `/` opens the autocomplete instead of
becoming a message.

### The way back: the question that comes from below

There are two cases, and they are quite different:

**1. The executor has a question.** It sends `SendMessage` to the professor, which
arrives as `wire_in`, the professor translates it on the stage, you answer in the chat
and that becomes a `wire_out`. Normal flow, everything visible.

**2. The executor gets stuck on a permission prompt.** Here is the subtlety: when this
happens, **nothing is written to the JSONL**. The `tool_use` record is already there, the
`tool_result` never arrives, and the session simply stops — silent to anyone who only
reads the transcript.

```
JSONL at this moment:   tool_use Bash {"command": "curl …"}
                        (nothing else — no tool_result)
PTY at this moment:     Do you want to proceed?
                        ❯ 1. Yes   2. Yes, and don't ask again   3. … 4. No
```

That is why the **blocked** state is derived from the PTY, not from the JSONL — it is the
reason the PTY keeps being captured even though the transcript is the source of truth.
The UI shows the level in red, displays the request and the options, and the click sends
the key back. In the spike, answering from outside released the command and the
`tool_result 200` appeared right after.

It is also worth exposing the **permission mode per level** when the run is created: an
executor in `manual` will block at every step; in `bypassPermissions` it asks nothing.
The useful middle ground is `acceptEdits` or `auto` — and the UI must make this choice
explicit, because it is the difference between an autonomous pipeline and one that keeps
waiting for you.

## 6. Known risks

| Risk | Handling |
|---|---|
| Boot dialogs block the session | `gates.py` recognizes them by text and answers; a timeout becomes a visible error in the UI |
| Permission prompt in the middle of the task | "Blocked" state on the level + terminal drawer to answer |
| Name collision between simultaneous runs | Peer name with the run suffix (`executor-a3f1`), injected into the top level's prompt |
| `ListAgents` lists other people's sessions | The top level's prompt names the exact peer; never "look for an executor" |
| Lag / partial line in the JSONL | Tailer with an offset and a buffer for the incomplete line |
| Executor loose on the disk | One cwd per run, chosen in the UI, with an explicit warning about the permission mode |
| Peer message held | A single permission mode per run; the hold dialog is recognized and becomes the blocked state |
| Peer messages are **batched** | If the recipient is in the middle of a turn, several messages arrive in a single delivery: three `wire_out` can become one `wire_in`. Nothing is lost, but there is no 1-to-1 correspondence — anyone who counts messages to prove delivery will get it wrong |
| Echo of the same message | A message appears in both transcripts (`wire_out` on the sender, `wire_in` on the receiver). The arrival order is **not guaranteed** — the transcripts are read in parallel — so the UI collapses whichever arrives second, in either direction |
| Level state stuck on "working" | The "esc to interrupt" footer stays in the buffer after the session stops. `trabalhando` (working) requires the footer **and** recent output on the PTY — the TUI repaints while it works and goes quiet when it is done |
| Lower-level speech on the stage | The stage only accepts narration from level 1. What the lower levels say already arrives translated in the milestones; repeating it on the stage recreates the noise the screen exists to remove |

## 7. Out of scope for v1

Authentication (it runs on `localhost`), remote execution, more than one simultaneous run
per directory, editing the role prompt through the UI (v1 reads it from the YAML).
