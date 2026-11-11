# HighLevel

Run, watch and talk to a **pipeline of Claude Codes at several levels of abstraction**.
The top level translates; the lower ones do the work.

```
       YOU (browser)
            │  chat
            ▼
   level 1: professor    interface + narration, never does the task
            │  SendMessage / ListAgents   (Claude Code's native channel)
            ▼
   level 2: executor     does the work, reports milestones only
```

The idea is not new here: it is two Claude Code sessions talking over the channel they
already have. What this project solves is the rest — instead of three Terminal windows
and a `tail -f | jq`, a single screen, with the professor's clean speech in the center
and the conversation between the levels on the side.

**No external dependency.** Only the Python 3.11+ standard library and `claude` on the
PATH.

## Getting started

```bash
python3 -m hl serve            # opens at http://127.0.0.1:7333
```

Or, without a browser:

```bash
python3 -m hl run \
  --cwd /private/tmp/demo \
  --task "Create a hello.py that takes a name and prints 'Hello, <name>!', with 3 tests." \
  --show fios
```

```bash
python3 -m hl pipelines        # the available recipes
```

## The screen

| panel | what it shows |
|---|---|
| **levels** | one card per level, with its own color and state: idle, working, **blocked**, dead. Clicking opens the raw terminal of that level |
| **stage** | only the narration of the top level. No tool call, no thinking, nothing said by the lower levels |
| **wires** | the messages between the levels; the same message appears once and the second copy becomes "delivered" |
| **composer** | talks to the level you choose — by default, the top one |

When a level gets stuck asking for permission, the card turns red with the real options
of the dialog, and one click answers. This matters because **the transcript does not
record this state**: the `tool_use` has already been written, the `tool_result` never
arrives, and the session goes silent. The one that knows is the terminal — that is why
the PTY keeps being captured even though the JSONL is the source of truth.

## Pipelines are data

Each recipe is a `.toml` in `pipelines/`. Adding a level means writing one more
`[[niveis]]` — no Python:

```toml
[[niveis]]
nome = "arquiteto"
cor = "35"
papel = """
You are the ARCHITECT… Above you is `{acima}`, below is `{abaixo}`.
"""
```

`{acima}` and `{abaixo}` become the real names of the neighboring sessions, already with
the run suffix (`arquiteto-a3f1`) — that is what lets two runs go at the same time
without fighting over names.

The project ships with `professor-executor` and `professor-arquiteto-executor`.

## Permission mode

It is a choice of the **run**, not of the level. Levels in different permission classes
make Claude Code **hold** the message from one to the other waiting for human review, and
the pipeline stalls before it starts.

- `auto` — resolves the common requests by itself (the default, and what keeps the pipeline moving)
- `acceptEdits` — accepts edits, asks about the rest
- `manual` — asks about everything; useful to see the blocking at work
- `bypassPermissions` — asks nothing

The executor writes in the working directory you choose. Choose a throwaway directory
while you are experimenting.

## How it works inside

Each level is an interactive Claude Code session in a PTY that the app controls:

```
claude -n <name>-<tag> --session-id <uuid> --permission-mode <mode> \
       --append-system-prompt <the level's role>
```

Since we choose the `--session-id`, we know the path of the transcript before the process
starts: `~/.claude/projects/<cwd key>/<uuid>.jsonl`. A tailer follows that file and a
normalizer turns each record into an event — `narration`, `wire_out`, `wire_in`,
`activity`, `prompt` — which the browser receives over SSE.

## Tests

```bash
python3 -m pytest -m "not slow"    # fast, starts no session
python3 -m pytest -m slow -s       # end to end: real minutes and tokens
```

## Where things live

```
hl/          the code: session in a PTY, gates, tailer, events, server
web/         the interface (plain HTML, CSS and JS, no build)
pipelines/   the .toml recipes
runs/<id>/   per run: run.json, events.ndjson and the raw PTY of each level
docs/        ARCHITECTURE.md and PLAN.md
```
