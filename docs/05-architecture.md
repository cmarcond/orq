# 5. Architecture

```
browser ──SSE/POST──► orq-web (handlers) ──Bridge (orq-agent attach, local or ssh)──► Unix socket
                              │                                                           │
                        StateCache ──► External (usage-all, claude-last, orq, orq-agent)  Supervisor
                              │                                                           │
                        Migrator, Pipelines, Auth                                claude -p stream-json
```

Three separate processes: the **browser** (ES modules, no build), the **server** (`orq-web`, pure
Python, `http.server` with one thread per connection) and, on each machine, one **supervisor per
agent** (`orq-agent start`, inside tmux). Between the server and the supervisor there is only ssh and
a Unix socket.

## 5.1 The supervisor (`orqweb/agent/`)

`orq-agent spawn --dir PASTA --account CONTA --label RÓTULO [--mode resume|new]` (folder, account,
label) makes sure a supervisor is running in a tmux session `orqs-<label>` (or with `nohup`, without
tmux) and waits for the socket to appear. The supervisor (`supervisor.py`):

1. builds the command (`claude.py`): `claude -p --input-format stream-json --output-format stream-json
   --verbose --include-partial-messages --replay-user-messages --permission-prompt-tool stdio
   --permission-mode <mode> [--resume <id>] -n <session name> [--append-system-prompt <role>]`.
   `--replay-user-messages` makes claude echo what it receives, so everything the page shows went
   through the same stream and is persisted. The system prompt always carries the `_outbox/` note
   (`OUTBOX_NOTE` in `cli.py`) and, on a level, the recipe's role after it;
2. with `--mode resume`, asks `claude-last --pick --as=<account> <folder>` for the folder's most
   recent conversation, already copied to the account if it belongs to another one, and resumes it;
   the old history (`<id>.jsonl`, up to 400 entries) is resent to whoever connects, marked as `replay`;
3. runs claude with the account's environment (`CLAUDE_CONFIG_DIR`, or nothing for the default one),
   reads stdout line by line, numbers each event, writes it to `events.jsonl` and forwards it to the
   connected clients; writes `state.json` (derived state, pid, account, folder, name, parent, level);
4. listens on `agent.sock` (mode 0600). Each client first sends `{"since": N}` and receives the events
   from N onward, then live; each JSON line the client sends goes to claude's stdin;
5. shuts down cleanly: `{"orq": "quit"}` closes claude's stdin, waits `QUIT_GRACE` (15 s) and kills it
   if needed; deletes the socket.

**Socket protocol** (`events.py`): the lines are those of the `claude -p` stream-json, plus:

| line | direction | meaning |
|---|---|---|
| `{"since": N}` | client → supervisor | first line: where to resend from |
| `{"type":"orq","level":…,"text":…}` | supervisor → client | notices ("agente encerrado", "claude saiu") |
| `{"type":"orq","subtype":"permission_answered","request_id":…,"behavior":…}` | supervisor → client | the page answered a permission; the replay closes the card |
| `user` with `orq_origin` | supervisor → client | `page` (echo of what the page sent), `agent` (came from another session through `SendMessage`: `origin.name`, `origin.body`, `origin.msg_id`), `injected` (no human mark: cron, loop, system) |
| `{"orq":"quit"}` | client → supervisor | shut down |

`stream_event` (partial text) only goes live: it is neither numbered nor written to `events.jsonl`.

**Derived state** (`derive_state`): `permission` on a permission `control_request` (with the tool and
a summary), `waiting` on a `result`, `working` on a user message, `quota` when a `rate_limit_event`
comes with `status: rejected` or the `result` ends in an error that matches `QUOTA_RE` (in that case
the supervisor keeps the page's last message in `retry`, for the migration to resend).

**Other subcommands**: `attach` (stdin/stdout bridge used by the server), `stop`, `list`, `convert`
(closes an interactive tmux claude, escalating `/exit` → Ctrl-C → SIGTERM, and reopens it as an
agent), `ls`/`cat` (the `_outbox/`), `link`/`unlink`/`links`/`link-run`/`peer` (links, chapter 6).

## 5.2 The server (`orqweb/server/`)

| module | responsibility |
|---|---|
| `app.py` | port, token, second server on the same port (reuses the link), building the `Context`, shutdown |
| `handlers.py` | routes (7.2), SSE, token/cookie, CSP, static files, upload, files, levels, links, injected notes |
| `auth.py` | password login with a session cookie; lockout by IP after repeated attempts (429 for one minute) |
| `external.py` | everything that leaves the process: `usage-all --json`, `claude-last --projects`, `orq --status-json` + `orq-agent links` (in the same ssh connection), `orq-agent spawn/convert/link/unlink/ls/cat`, writing files. An injectable `Runner` makes it testable without ssh |
| `state.py` | `StateCache`: quota, agents, links, projects, with background loops only while the page is open |
| `bridge.py` | one bridge (`orq-agent attach`, local or over ssh) per open tab; a short-lived bridge to deliver a message when there is no tab |
| `assign.py` | choice of account by slack in the 7d window (3.6) and the label `folder@machine` |
| `migrate.py` | account switch when the quota runs out |
| `pipelines.py` | level recipes (TOML) and the roles with the neighbours resolved |
| `sets.py` | sets (⊞): JSON format, validation, catalogue in `ORQ_SETS_DIR`, conflicts, plan (accounts for the whole set) and snapshot of the open tabs; the one that opens them is `handlers.open_set_job` |

**StateCache**: `refresh_usage` every 150 s; `refresh_agents` every 15 s (all machines in parallel, 12
threads, one ssh connection per machine); projects on demand, valid for 60 s; all of this only if
there was a request from the page in the last 60 s. Three mechanisms keep the list from flickering:

- **provisional** (60 s): a newly opened agent enters the list right away and stays until the machine
  returns it as alive, because a poll that started before it comes back without it;
- **closing** (30 s): an agent that was told to stop counts as `closed` even if the machine still sees
  it alive (claude takes up to 15 s to exit);
- **newly created link**: each poll records when it started; a link created from the page after that
  cannot be in the response and stays; if it was created before, the machine's response is the truth.

**Bridge**: when a tab is opened, the page requests `/stream?since=N`; the server opens `orq-agent
attach <label> --since N` (local, or `ssh host …`) and forwards each line as an SSE event. One tab per
label: the previous one is closed. `deliver(label, obj)` writes to the tab's bridge or, when there is
no tab, to a short-lived bridge that lives 1.5 s (`fresh=True` forces a new one, used by the migration
because the old one pointed to the process that died).

**Migrator**: on each new list of agents, it looks for `state == "quota"`; picks the account with the
most slack other than the current one; registers the agent as provisional on the new account
**before** running `orq-agent spawn --mode resume --restart` (otherwise the tab would disappear);
delivers the message kept in `retry` through a new bridge; keeps the history in `migrations`.

**Security**: `Host` is checked; `/api` requires the token (`X-Orq-Token`, or `?t=` on the SSE) or the
session cookie; the CSP restricts scripts to `'self'` and cdnjs (marked and DOMPurify); the model's
Markdown goes through DOMPurify; `/static` refuses path traversal; the agent's socket is 0600;
`_outbox/` files go out with CSP `sandbox` and `nosniff`, HTML never inline, only names without a
path.

## 5.3 The frontend (`orqweb/server/static/`)

No build: `index.html` loads `js/app.js` as an ES module, plus `marked` and `DOMPurify` from cdnjs.

| module | responsibility |
|---|---|
| `format.js` | pure: escaping, times (`left`, `ago`), sanitized Markdown, message with attachments, sizes, file type (`fileKind`) |
| `classify.js` | pure: internal records to ignore, machine origin, tool names and summaries |
| `events.js` | pure: reducer `reduce(event, ctx)` → interface actions (`text`, `tool`, `permission`, `ask`, `bubble`, `machine`, `wireOut/wireIn/sent`, `result`, `sys`…) |
| `app.js` | page state: tabs, one EventSource per tab (reconnects with `since`), DOM, composer, permissions and questions, attachments, side panel (levels, links, files), preview, pickers, notifications |

The first three have tests in `tests/js/*.test.mjs` (`node --test`). `app.js` is covered by the QA.

Things to watch in `app.js`: the tab is always that of the base agent; `cur` is the label of the chat
being shown (the top of the chain of levels); `tabs[label]` holds the state of each chat (stream
sequence, element, pending permissions, wires, files); `refresh()` reconciles the list from the
machines with what the tab already knows from the events (the state from the events wins, except
`abrindo…`, which only the machine confirms) and detects a reopening by the pid (account switch) in
order to recreate the tab and notify.

## 5.4 State on disk

```
~/.cache/orq/<sanitized label>/       (ORQ_STATE_DIR)
├── agent.sock          the supervisor's socket (0600)
├── state.json          label, dir, account, pid, state, msg, sid, kind, sock, name, parent, level, permission, role, set, pipeline, retry, ts
├── events.jsonl        numbered events of this life of the agent (starts over on each reopening)
├── supervisor.log      the supervisor's stderr (what it says on exit)
├── role.txt            the level's role (--role-file -), for --restart to reuse
└── links/<other>.json  one file per link (6.4) and <other>.log with the ssh stderr

~/.config/claude-multiconta/accounts.json       the accounts (copied by install.sh)
~/.config/claude-multiconta/aliases.sh          generated
~/.config/orq-web/{password,ssh_config,state/}  only on server0 (container)
~/.config/orq-web/conjuntos/<id>.json           the sets (⊞) saved from the page (ORQ_SETS_DIR)
<config_dir>/sessions/<pid>.json + <pid>.<hash>.key   Claude Code's session registry (6.1)
<project>/_inbox/, <project>/_outbox/                 attachments going in and coming back
```

## 5.5 Sequences worth drawing

**Opening a project**: page `POST /api/agent/open` → `assign` picks the account → `External.spawn`
(`orq-agent spawn` over ssh) → `add_provisional` → response → page `refresh()` + `show()` → `GET
/stream?since=0` → `attach` bridge → events → chat.

**Sending a message**: page `POST /send {text}` → `bridges.deliver` → `claude`'s stdin → `user` echo
(stamped `page`, because the supervisor saw the text go out) → `assistant` with partial `stream_event`s
→ `tool_use` / `control_request` → `result` → the page marks `esperando você`, re-reads the `_outbox/`.

**Account switch**: `rate_limit_event rejected` → `state.json: quota, retry` → `refresh_agents` →
`Migrator.check` → provisional on the new account → `spawn --restart` (copies the conversation) → new
bridge → `retry` resent → the page sees a new pid → recreates the tab → toast.

**Raising levels**: `POST /upgrade {pipeline}` → `plan_levels` (roles with `{acima}`, `{abaixo}`,
`{eu}`, `{executores}`) → `spawn` from the bottom up with `--role-file - --parent --level` →
provisionals → (multi recipe) `orq-agent link` of the bottom level with each linked agent → the page
starts talking to the top.

**Opening a set**: page `POST /api/conjuntos/<id>/open` → `sets.validate` → `sets.conflicts` (409 if
any `maquina:pasta` already has a live agent) → `sets.plan` (unique labels, `assign` for the whole
set) → response with the plan → `open_set_job` thread: `spawn --role-file - --permission-mode --set`
per agent (threads per machine; in sequence on the same machine, with `Pacer` spacing out the ssh
connections) and provisionals → with `lider`, `make_link` of the first one with each of the others
(link note to both) → `run_upgrade` of the first one with the recipe (role replaced by `lider.prompt`)
→ each agent's and the teacher's `tarefa` delivered through a short-lived bridge → `job.etapa =
pronto`; the page reads `conjuntos_abrindo` in `/api/state` every 5 s and shows each step as a toast.
