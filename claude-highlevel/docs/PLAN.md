# Implementation plan — 5 phases

> Translation note: this plan was written in Portuguese and the runs recorded here used
> the original Portuguese prompts. In those runs the professor narrated in Portuguese,
> the greeting was `Olá, <nome>!` and the milestone keywords were `PLANEJANDO`,
> `IMPLEMENTANDO`, `PROBLEMA`, `VALIDANDO`, `CONCLUÍDO`. The pipelines now ship in
> English, so the text below uses the English keywords.

The acceptance exercise is always the same, the one from the original experiment:

> **Task T:** create in `<the run's cwd>` a `hello.py` that takes a name as an argument
> and prints `Olá, <nome>!`, with at least 3 automated tests, and run the tests.
>
> The **professor** never writes code: it delegates to the **executor** and narrates in
> natural language. The executor reports milestones only (`PLANNING`, `IMPLEMENTING`,
> `PROBLEM`, `VALIDATING`, `DONE`).

Each phase repeats task T and raises the bar one step. Nothing counts as done without T
passing end to end in that phase.

---

## Phase 1 — Core: start, tail, normalize  ✅ done

**Deliverable:** `hl/session.py`, `hl/gates.py`, `hl/tailer.py`, `hl/events.py`,
`hl/config.py` and an `hl/cli.py` with `hl run --task "…"` that prints colored events in
the terminal. No web yet.

Three concrete problems, already mapped in the spike, have to die here:

1. **The child process's env.** If the app is started from inside a Claude Code session,
   the child inherits `CLAUDE_CODE_CHILD_SESSION` and **the transcript is not written** —
   the JSONL never appears and the viewer is blind. The spawner clears `CLAUDECODE`,
   `AI_AGENT` and every `CLAUDE_*` before starting.
2. **The log directory key.** `/tmp/x` becomes `-private-tmp-x`: resolve the `realpath`
   of the cwd before building `~/.claude/projects/<key>`.
3. **Boot gates.** The directory trust dialog and the `bypassPermissions` warning are
   answered by `gates.py`, with a timeout that becomes an error.

**Test:** `hl run --cwd /tmp/hl-t1 --task T`. It passes when:
- both JSONL files appear in `~/.claude/projects/-private-tmp-hl-t1/` with the UUIDs
  that **we** chose;
- the terminal shows, in different colors, `wire_out` (professor→executor),
  `wire_in` (executor→professor) and `narration`;
- `hello.py` exists and the tests run;
- `hl run` kills both processes on exit, leaving no orphan (`pgrep -f claude`).

**Result (28/08):** passed. `python3 -m hl run --cwd /private/tmp/hl-t1 --task "…"
--permission-mode auto` delivered `hello.py` + `test_hello.py` with 12 green tests, both
transcripts were created with the chosen UUIDs, the PTY of each level was saved in
`runs/<id>/`, and the shutdown left no process or socket behind.

Three defects found and fixed along the way: readiness measured by the transcript
(deadlock), different permission classes between levels (message held) and cost records
restarting the silence timer (the run never ended by itself).

---

## Phase 2 — Orchestrator and server  ✅ done

**Deliverable:** `hl/orchestrator.py` (run life cycle, bottom-up start order, task
injection, stop), `hl/bus.py` (fan-out + buffer with `seq`), `hl/server.py` (FastAPI:
`POST /api/runs`, `GET /api/runs/{id}`, `POST /api/runs/{id}/message`,
`POST /api/runs/{id}/stop`, `WS /ws/runs/{id}`). Persistence in
`runs/<id>/events.ndjson`.

**Test:** no browser, with `curl` + a command-line WS client:
- `POST /api/runs` with task T returns a `run_id` and both levels start;
- the WS delivers the same sequence of events that phase 1 printed;
- reconnecting with `?since=<seq>` **neither loses nor duplicates** an event;
- `POST /stop` brings everything down; `runs/<id>/events.ndjson` reproduces the whole run.

**Result (28/08):** passed. 32 events read over two connections (a deliberate cut at
`seq` 12, a reconnection asking for `since=12`, resuming at 13): **zero duplicates, zero
gaps**. Contiguous history `1..40`, persisted in `events.ndjson`, and the message sent
through `POST /message` reached the professor as a `prompt` event.

**Deviation from the plan:** WebSocket became **SSE**. The flow is one-way and SSE's
`Last-Event-ID` is our `seq`, so gap-free reconnection is native — and the server fits in
the standard library, with no FastAPI or uvicorn.

---

## Phase 3 — Interface: the clean stage  ✅ done

**Deliverable:** `web/` (plain HTML/CSS/JS): a rail of levels with color and state, a
central **stage** with only the professor's narration (markdown, large font), a **wires**
panel with the messages between the levels, toggles for `tool calls` and `thinking`, a
raw terminal drawer per session.

**Test:** run task T from the browser. It passes when:
- the stage contains **no** `@ executor❯` block, no tool call, no thinking — only the
  narration (this is the criterion that defined the 9/10 of the experiment);
- the executor's 5 milestones appear in the wires panel, each with the color of its
  origin;
- the state of each level visibly changes between idle and working;
- closing and reopening the tab in the middle of the run rebuilds the whole screen with
  no hole.

**Result (28/08):** passed, in a real browser. The stage held only the professor's
narration; the executor's milestones went to the wires with the color of their origin;
the state chips alternated between idle, working and dead; and reloading the page in the
middle of the work rebuilt the whole stage and wires (`?since=0` on the `EventSource`).

Three defects found by looking at the screen, not at the code: `display:grid` beat the
`hidden` attribute and the run screen showed underneath the form; the **executor's**
narration invaded the stage (the very criterion of the clean screen); and the echo
collapse assumed that the outgoing message arrives before the incoming one — the two
transcripts are read in parallel, so the order is not guaranteed.

A piece of phase 4 came along for free: I sent through the chat *"peça pro executor
adicionar um teste para nome com emoji"* (ask the executor to add a test for a name with
an emoji), the professor passed it on through `SendMessage` and the executor changed
`test_hello.py` — the whole cycle, from the browser to the disk.

---

## Phase 4 — Interaction and control  ✅ done

**Deliverable:** a working chat (sends to level 1 by default, a selector to talk directly
to any level, text as a **bracketed paste** so it is not submitted at a line break and
does not open the autocomplete on `/`), detection of a **blocked** level with an answer
from the UI (`POST /keys`), stop and cleanup, a list of previous runs with replay.

The **blocked** state does not come from the JSONL: when a level stops on a permission
prompt, the `tool_use` has already been written and the `tool_result` never arrives — the
transcript goes silent. The one that knows is the PTY. The rule is: a pending `tool_use`
**and** a recognized dialog on the screen.

**Test:** during task T,
- ask in the chat "has it run the tests yet?" and the professor's answer appears on the
  stage without breaking the executor's flow;
- send "ask it to add a test for an empty name" and the request appears as a `wire_out`
  and then becomes real work in the executor;
- run a level in `manual` mode on purpose, ask for something that needs approval (a
  network call, for example), see the level become **blocked** with the request and the
  options on the screen, and unblock it with one click;
- open a finished run and replay the whole narration.

**Result (28/08):** passed, all four. The block was caused on purpose — a level in
`manual` told to make a network call — and it showed up in the rail in red with the four
real options of the dialog (`1. Yes`, `2. Yes, and don't ask again for: curl *`, …).
Clicking released it; the executor hit a **second** request, for another command, and
`POST /allow` released it again until the task finished (`HTTP 200`).

The replay was tested the honest way: the server was **restarted** first, so the run
existed only on disk. It opened from the list, rebuilt stage and wires, and the SSE closed
on the `fim` event instead of reconnecting forever.

Two readability defects in the raw terminal, both from the same root: the TUI writes the
spaces between words as **cursor movement**, not as spaces. Deleting every escape glues
everything together (`esctointerrupt`); replacing every escape with a space breaks words
in the middle. Movement became a space, the rest is dropped. And the ANSI regex did not
cover the private modes (`\x1b[>4m`, `\x1b[<u`) or save/restore cursor, which leaked raw
onto the screen.

---

## Phase 5 — Pipelines as data, hardening, E2E  ✅ done

**Deliverable:** `pipelines/*.yaml` (roles, model, permission mode, color per level),
support for **3 levels** (professor → arquiteto → executor) just by editing YAML, a run
suffix in the peer name for simultaneous runs, `README.md`, and an automated E2E test
that runs task T and validates the events.

**Test:**
- `pytest` starts the 2-level pipeline with task T, and asserts on `events.ndjson`: there
  is ≥1 `wire_out` from level 1, ≥3 `wire_in` from level 2, ≥3 `narration` from level 1,
  zero `narration` from level 2 on the stage, and `hello.py` with green tests in the cwd;
- the same test with the 3-level pipeline passes without touching Python code;
- two simultaneous runs in different directories do not get mixed up (names with a
  suffix);
- hygiene checklist: no orphan `claude`, no leaked socket in `/tmp/cc-socks`.

**Result (28/08):** the **three-level** pipeline passed without a line of Python touched
— just one more `.toml`. The two-level one failed on the first run, and the failure was
mine: I required three milestones from the executor, and in that execution it did the
whole task in one go and reported only **once**, with eight green tests. How many
milestones the executor sends is its own judgment, not a guarantee of the system.

The assertion was changed to check delivery instead of quantity — and then the test
revealed something I did not know about the mechanism. Requiring one `wire_in` for each
`wire_out` failed on the three-level pipeline, but **nothing had been lost**: the
executor sent three milestones while the arquiteto was in the middle of a turn, and
Claude Code **batched** the three into a single delivery. The arquiteto answered all
three. In other words: peer messages that arrive while the recipient is busy are
coalesced, and there is no 1-to-1 correspondence.

What the test asserts now is weaker and truer: for every message sent, either the body
appears as a `wire_in` at the destination, or **the destination produced some event
after it** — that is, it woke up. This catches a real hole in the channel without
depending on a behavior the system never promised. Validated against the 15 runs
recorded so far.

**Deviation from the plan:** YAML became **TOML**. `tomllib` has been in the standard
library since 3.11, and YAML would require the project's first external dependency.

**Two bugs found by the test itself.** `stop()` stopped the tailers **before** bringing
the sessions down, so the end of the transcript was lost — now it brings the sessions
down first and does one last drain before closing. And the second: the `trabalhando`
(working) state never went back to `ocioso` (idle). I was looking for the "esc to
interrupt" footer on the screen, but it stays stuck in the buffer after the session
stops — and the idle footer is no good as a cross-check, because it changes with the
permission mode. The honest signal is the rhythm of the terminal: the TUI repaints
non-stop while a turn is in progress and goes quiet when it ends. Now `trabalhando`
requires the footer on the screen **and** recent output on the PTY.

---

## Order of risk

All the risk is in phase 1 — that is where everything that already broke in the spike
lives (inherited env, path key, boot dialog). Phases 2–5 are building on known ground.
