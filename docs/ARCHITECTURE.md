# Architecture and security overview

This is a one-file overview of the architecture and the security model, drawn from the manual in
[`docs/`](README.md) and from the code as it is in this repository. The code is the reference: if
the manual and the code ever disagree, the code is right. Host names (`server0`, `node0`…), addresses and account names (`work`, `work2`…) are
placeholders.

## What it is and who it is for

`orq` is tooling for one person who runs many Claude Code agents at the same time on several
machines (a laptop and a few servers reached over ssh) and load-balances that work across four
personal, paid Claude accounts, all his own. It keeps each account in its own configuration
directory, shows the usage quota of every account, places new sessions on the account with the most
slack, and offers
one web page from which every agent on every machine can be opened, watched and driven. It is a
single-user tool: there are no user accounts, roles or tenants. Everything is Python standard library
(3.8+) on the machines, ES modules in the browser, and ssh and tmux between machines.

## Components

| Directory | What it is |
|---|---|
| `claude-multiconta/` | Shell and Python scripts, no dependencies. One Claude Code account per directory through `CLAUDE_CONFIG_DIR` (the default account uses `~/.claude` with the variable unset). `install.sh` generates aliases (`claude-<name>`, `claude-<name>-login`, `claude-usage-<name>`) from `accounts.json`. `usage-all` queries the Anthropic usage API per account and prints the 5-hour and 7-day windows with a pace marker and a fleet TOTAL; `usage-hist` keeps a SQLite history filled by a 5-minute collector; `usage-sessoes` shows which sessions consume each account; `claude-last` / `claude-resume` find and resume the latest conversation of a folder, copying it to another account when asked (`--as`); `orq` opens up to four projects in a tmux grid. `accounts.json` is also the configuration file that `orq-web` reads (`permission_mode`, `orq_hosts`, `auto_switch`). |
| `orq-web/` | Python package `orqweb` with two commands. **`orq-agent`** (`orqweb/agent/`) is a supervisor that runs on every machine, one process per agent. **`orq-web`** (`orqweb/server/` plus `static/`) is an HTTP server and a web page with no build step. |
| `claude-highlevel/` | An earlier experiment: a pipeline of Claude Code sessions in tiers (teacher → executor) driven through a PTY with its own screen. It is kept as a historical reference; the idea was reimplemented inside `orq-web` as "levels" (see `orq-web/docs/PLAN.md`, phase 3). Its CI runs only the fast tests. |

Main modules of `orq-web`:

| Module | Responsibility |
|---|---|
| `agent/claude.py` | builds the `claude -p` command line; picks the session to resume through `claude-last` |
| `agent/supervisor.py` | the Claude Code process, the Unix socket, connected clients, clean shutdown |
| `agent/events.py` | numbered event log with replay, origin stamps, derived agent state, quota detection |
| `agent/client.py` | `attach` (stdin/stdout bridge to the socket), `stop`, `spawn` in tmux |
| `agent/link.py` | links between agents: session cards, mirrors, ssh tunnel |
| `agent/files.py` | lists and streams the project's `_outbox/` (`orq-agent ls` / `cat`) |
| `server/app.py` | bind address, token, password requirement, wiring, shutdown |
| `server/auth.py` | password login, in-memory sessions, cookie, lockout |
| `server/handlers.py` | routes, SSE, host and auth checks, CSP, uploads, file serving |
| `server/bridge.py` | one `orq-agent attach` per open tab, local or over ssh |
| `server/external.py` | every call that leaves the process (helper scripts, ssh) behind an injectable `Runner` |
| `server/state.py` | cache of usage, agents, links and projects, refreshed only while a page is open |
| `server/assign.py`, `migrate.py` | choice of account by 7-day slack; automatic account switch on quota exhaustion |
| `server/pipelines.py`, `sets.py` | level recipes (TOML) and "sets" (JSON descriptions of several agents to open together) |

## How a session flows end to end

```
browser ──HTTP: SSE + POST──► orq-web (handlers) ──Bridge: `orq-agent attach`──► agent.sock (Unix, 0600)
                                    │              (local process, or `ssh host …`)        │
                              StateCache ──► External: usage-all, claude-last,        Supervisor
                                             orq --status-json, orq-agent (ssh)            │ stdin/stdout
                                                                                 claude -p (stream-json)
```

1. **Where Claude Code runs.** On the machine that holds the project folder. `orq-agent spawn`
   starts a supervisor in a tmux session `orqs-<name>` (or detached in the background when there is
   no tmux) and waits for its socket. The supervisor runs
   `claude -p --input-format stream-json --output-format stream-json --verbose
   --include-partial-messages --replay-user-messages --permission-prompt-tool stdio
   --permission-mode <mode> [--resume <id>] -n <session name> --append-system-prompt <note>`
   in the project folder with the environment of the chosen account. The conversation is an ordinary
   Claude Code session, so `claude --resume` in a terminal can continue it.
2. **Supervisor and socket.** The supervisor reads Claude's stdout line by line, numbers each event,
   appends it to `events.jsonl` and forwards it to connected clients. It listens on
   `~/.cache/orq/<label>/agent.sock`. A client first sends `{"since": N}`, receives the events from
   N onwards and then the live ones; every JSON line the client sends afterwards is written to
   Claude's stdin. `{"orq": "quit"}` closes stdin, waits 15 s and kills the process if needed.
   Partial-text events (`stream_event`) are sent live only and are not numbered. The supervisor also
   writes `state.json` (state, pid, account, folder, permission mode…), which is how machines report
   their agents. Because the supervisor lives in tmux, it survives a dropped ssh connection or a
   restart of the web server; a client that reconnects asks for "since N".
3. **How the server reaches other hosts.** Only through ssh (`BatchMode=yes`, optional `-F` config).
   For each open tab the server starts `orq-agent attach <label> --since N`, as a local process or as
   `ssh <host> ~/.local/bin/orq-agent attach …`, and relays each line as a Server-Sent Event on
   `GET /api/agent/<label>/stream`. Messages go the other way through `POST …/send`, which writes to
   the tab's bridge or, when no tab is open, to a short-lived bridge. Listing agents, projects and
   links, spawning, uploading and reading files are separate ssh commands that call `orq-agent` and
   the `claude-multiconta` scripts on the target machine.
4. **Usage.** The server runs `usage-all --json` (locally, or over ssh on `ORQ_USAGE_HOST`) every
   150 s and the agent list every 15 s, only if the page made a request in the last 60 s. The page
   shows one card per account and a TOTAL line; history panels come from `usage-hist`.
5. **Chat.** The page keeps one `EventSource` per tab and reduces stream events to interface actions
   (`static/js/events.js`). Model Markdown is rendered with `marked` and sanitized with DOMPurify.
6. **Permission prompts.** A `can_use_tool` control request from Claude puts the agent in state
   `permission` and appears as a card with Allow/Deny; `AskUserQuestion` appears as a form. The answer
   is `POST …/permission`, which the server turns into a stream-json `control_response`.
7. **Attachments.** Files dropped on the chat are uploaded (`POST …/upload`, 200 MB limit, file name
   reduced to a base name) to `<project>/_inbox/` on the agent's machine, and the message carries the
   paths. In the other direction, every agent is started with a system-prompt note telling it to copy
   files meant for the user to `<project>/_outbox/`; the page lists and previews them through
   `orq-agent ls` / `cat`.
8. **Load balancing: failover to another account.** When Claude reports the quota as exhausted (a `rate_limit_event` with status
   `rejected`, or a turn ending in a limit error) the supervisor sets `state: quota` and stores the
   last page message. On the next agent poll the server restarts the agent on the account with most
   slack (`orq-agent spawn --mode resume --restart`; `claude-last --as` copies the conversation to
   that account) and resends the message. `"auto_switch": false` turns this off.
9. **Levels and links.** A "level" is another agent started above an existing one with a role prompt
   from a recipe; a "link" makes two agents visible to each other. Both rely on Claude Code's own
   `ListAgents` / `SendMessage`. Across accounts or machines, `orq-agent link` mirrors each agent's
   session card and key into the other's `sessions/` directory and, between machines, forwards the
   messaging sockets with `ssh -N -L/-R`.

Two deployments are described in the docs: a local server on the laptop (`127.0.0.1`, token in the
link), and a container on `server0` that holds only Python and an ssh client, has no `claude` binary
and no agents (`ORQ_LOCAL_HOST=none`), and reaches every machine, including its own host, by ssh.

## Security model

Stated from the code. The design assumption is one trusted user on a private network.

**Authentication of the page.**

- *Token.* The server generates a random token (`secrets.token_urlsafe(16)`) and prints a link
  `http://127.0.0.1:<port>/?t=<token>`. Everything under `/api` requires the token, in the
  `X-Orq-Token` header or in `?t=`, or a valid session cookie. The comparison is constant-time. The
  link is saved in `<ORQ_STATE_DIR>/web-<port>.url` (mode 0600) and the token is reused when the
  server restarts on the same port. The token stays valid when password login is enabled.
- *Password.* If the password file exists (`ORQ_PASSWORD_FILE`, default
  `~/.config/orq-web/password`), `/` and `/m` redirect to `/login` unless the request is
  authenticated. The password is one line of plain text in that file, compared in constant time. A
  correct password creates a random session id kept in server memory and sets a cookie
  `orq=…; HttpOnly; SameSite=Strict; Max-Age=30 days`. Sessions expire after 30 days without use and
  are lost when the server restarts. A wrong password sleeps 1 s; five consecutive failures from the
  same client IP block that IP for 60 s (HTTP 429). `POST /logout` drops the session.
- Static files and the login page are served without authentication. Without a password file the
  page HTML itself is also served without authentication; it can do nothing without the token.

**Bind and allowed-hosts checks.**

- `ORQ_BIND` defaults to `127.0.0.1`. With any address other than `127.0.0.1`, `localhost` or `::1`
  the server refuses to start unless the password file exists and is non-empty (`check_bind` in
  `server/app.py`).
- Every GET and every authenticated request is rejected with 403 unless the `Host` header (port
  removed) is `127.0.0.1`, `localhost`, the bind address or an entry of `ORQ_ALLOWED_HOSTS`.
- The docs describe the container as published only on the private-network (ZeroTier) address of
  `server0`; that restriction is in `docker-compose.yml` (`${ORQ_ZT_IP}:8765:8765`), not in the
  Python code, which binds `0.0.0.0` inside the container.

**Browser-side containment.** Responses carry a Content-Security-Policy that limits scripts to
`'self'` and `cdnjs.cloudflare.com` (from which `marked` and DOMPurify are loaded, without
subresource-integrity attributes). `/static` rejects paths outside the static directory. Files from
`_outbox/` are addressed by base name only and are sent with `Content-Security-Policy: sandbox`
and `X-Content-Type-Options: nosniff`; HTML is always sent as a download, never inline.

**Permission prompts and permission modes.**

- Claude Code is started with `--permission-prompt-tool stdio`, so tool permission requests arrive
  on the stream instead of a terminal. Allow sends `{"behavior": "allow", "updatedInput": <input>}`;
  Deny sends `{"behavior": "deny"}`. The decision is made by whoever is authenticated on the page (or
  any process that can connect to the agent socket); the supervisor does not filter or judge requests.
- Which requests are raised at all is decided by Claude Code according to `--permission-mode`. The
  value comes from `permission_mode` in `accounts.json` (the code falls back to `manual` when the key
  is absent; the shipped `accounts.json` sets `auto`), or per agent from `--permission-mode`, or from
  a set's `permissao` field. The docs describe `auto` as "Claude decides routine actions and asks
  about sensitive ones" and `manual` as "asks everything". Only sets validate the value (`auto`,
  `manual`, `acceptEdits`, `plan`); elsewhere it is passed to `claude` unchanged.
- Levels are started in the same mode as the agent below them, because Claude Code holds messages
  between sessions in different permission classes.

**Credentials and isolation between accounts.**

- Account credentials are those of Claude Code itself: the Keychain on macOS, or
  `<config_dir>/.credentials.json` on Linux. `orq-web` does not read them. `usage-all` reads the
  OAuth access token of each account to call the usage API and does not print it. `make-tarball.sh`
  ships scripts and the account list, not credentials; each machine logs in by itself.
- The isolation between accounts is the configuration directory only. All accounts run as the same
  OS user, so there is no OS-level separation between them or between agents.
- Conversations are deliberately copied between accounts: `claude-last --as`, `claude-resume --as`
  and the automatic account switch copy the transcript (with its attachments and missing project
  memory files) into the target account's directory.
- Linking agents of different accounts or machines copies each session's card and `.key` file (the
  credential of that session's messaging socket) into the other account's `sessions/` directory.
  Between machines the key travels inside the ssh connection, as a field of the JSON record that
  `orq-agent peer` reads from standard input (`--mirror`) and writes to standard output, and it is
  written with mode 0600. So the code does read and transmit the session `.key`; it does not handle
  the account's OAuth token (`docs/06`, section 6.4).
- The agent socket has mode 0600; no further authentication happens on it.
- The container mounts `~/.ssh` and `~/.config/claude-multiconta` read-only and has no Claude
  credentials, but it holds an ssh key that can run commands as the user on every listed machine.

**What it deliberately does not protect against.**

- No TLS: the server speaks plain HTTP, and the cookie has no `Secure` attribute. Confidentiality on
  the network relies on the private network (ZeroTier in the author's deployment) or an ssh tunnel.
- No multi-user model and no authorization levels: anyone holding the token or the password can open
  an agent in any folder on any configured machine, send it messages, answer its permission prompts
  and upload files. There is no CSRF token or `Origin` check beyond the header token and the
  `SameSite=Strict` cookie.
- No sandbox: agents run with the full rights of the user on each machine (`docs/08`, section 8.4).
- Compromise of the web server host means ssh access to all machines.
- No audit log beyond the per-agent `events.jsonl` and login lines on the server's stderr.

## Testing and CI

One workflow per sub-project, triggered only by changes in its directory.

| Suite | What it covers | How to run (as CI does) |
|---|---|---|
| `claude-multiconta/tests` | alias generation, `claude-last`, `usage-all`, `usage-hist`, `usage-sessoes` | `cd claude-multiconta && pip install pytest && python3 -m pytest -q tests` (Python 3.8 and 3.12). CI also runs `python3 -m py_compile` or `sh -n` on every script and checks that `accounts.json` parses. |
| `orq-web/tests` (pytest) | config, command line, events, supervisor through a real Unix socket, HTTP handlers with a real server and supervisor, auth, files, links (real Unix sockets and a fake `ssh -N`), account choice, migration, pipelines, sets. `tests/fake_claude.py` imitates the stream-json protocol, so Claude is not needed. | `cd orq-web && pip install -e ".[dev]" && ruff check . && ruff format --check . && pytest` (Python 3.8, 3.11, 3.12) |
| `orq-web/tests/js` | the pure frontend modules: `format`, `classify`, `events`, `charts`, `usage` | `cd orq-web && node --test 'tests/js/*.test.mjs'` (Node 22) |
| Docker smoke test | the image builds; with an empty account list and a password file, `/login` returns a form and `/api/state` without credentials returns 403 | `docker build -t orq-web:ci .` in `orq-web/`, then the `docker run` and `curl` steps in `.github/workflows/orq-web.yml` |
| `claude-highlevel/tests` | fast tests only; tests marked `slow` start real Claude sessions and are excluded | `cd claude-highlevel && pip install pytest && python3 -m pytest -q -m "not slow"` (Python 3.12) |
| `orq-web/qa` (not in CI) | Playwright tests that drive the real page with real Claude Code and real accounts: chat, permissions, attachments, levels, links, sets, login, reconnection, account switch. They consume quota and need installed agents. | `make qa-smoke` (no quota) or `make qa` in `orq-web/`; see `orq-web/qa/README.md` |

`make test` and `make lint` in `orq-web/` run the same pytest, `node --test` and ruff commands.

## Limits and known gaps

From `docs/06` (6.6), `docs/08` (8.3, 8.5), `orq-web/README.md` and `orq-web/docs/PLAN.md`:

- Switching the account of a linked agent does not rebuild the link: the agent restarts with a new
  pid, the link drops and must be made again.
- The idle notification of `SendMessage` (`notify_when_idle`) may not work across a mirrored link;
  delivery works. Role prompts ask for explicit milestones instead.
- A link over ssh needs the machine of the first agent to reach the second one by that ssh name.
- `orq-agent links` keeps listing links that were undone (the page filters them).
- The `professor-executores` recipe across different machines has unit tests only.
- `GET …/files` can return 404 right after an agent is opened; the next attempt succeeds. The exact
  cause is recorded as not yet understood.
- A `Range` request for a large file on a remote machine re-reads the file over ssh on each request.
- `events.jsonl` covers only the current life of an agent; on resume, at most the last 400 entries
  of the old transcript are replayed.
- In the container deployment the laptop is reachable only while it is on and accepts ssh over the
  private network. A Mac that receives agents over ssh needs a tmux server started in the graphical
  session (`deploy/macos-tmux-keep.sh`), because ssh sessions cannot open the Keychain.
- Browser notifications need `https` or `localhost`; over plain `http://<address>` only the beep
  and the tab-title counter work.
- Not done yet: history of level runs and a per-project milestone timeline; model and effort per
  level; recipes editable from the page; set progress in the side panel; opening only the missing
  agents of a set; search in the project list; switching account before the quota runs out.
