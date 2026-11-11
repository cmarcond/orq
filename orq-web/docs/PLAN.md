# Plan: deploy on server0, monorepo with CI/CD and levels (highlevel) in orq-web

Decisions taken on 2026-10-11:

| topic | decision |
|---|---|
| container | only the `orq-web` server; it talks to **all** the machines over ssh, including server0 itself. No `claude` inside |
| access over the network | login page with cookie; a single password in a file on server0; plain HTTP, since ZeroTier is a private network |
| GitHub | **private monorepo** with `orq-web`, `claude-multiconta` and `claude-highlevel`; CI per project; Docker image on GHCR |
| highlevel | **reimplement** the idea inside orq-web: levels as agents of the page, compatible with tabs, socket and replay |

Feasibility test done before the plan: two `claude -p` sessions in stream-json, named with `-n`,
talk through the native channel. The teacher found the executor with `ListAgents`, delegated with `SendMessage`,
the executor did the work and replied, and the teacher reported. So the levels do not need a PTY.

Order: each phase starts with the tests (unit and integration with `fake_claude`), then the code,
then the real verification on the machines. Nothing goes to server0 without the suite green.

---

## Phase 1 — access over the network and container on server0  ✅ done on 2026-10-11

Live at `http://10.0.0.10:8765/` (password: `ssh server0 cat ~/.config/orq-web/password`).
The container sees server0, node3 and laptop over ssh; the quota comes from server0; `tmux run-shell` solves the
laptop's Keychain. Differences from what was planned: there is no `ORQ_BIN` in the container (nothing runs locally);
`push-server0.sh` sends the code over a single ssh connection, because scp/sftp kept dropping on the unstable network.

### 1.1 Login with cookie (`orqweb/server/auth.py`)

- `ORQ_PASSWORD_FILE` (default `~/.config/orq-web/password`): the password, one line. Without the file, the
  server continues with only the link's token, as today.
- `GET /login` minimal page; `POST /login` compares in constant time, creates a random session in
  memory and returns `Set-Cookie: orq=…; HttpOnly; SameSite=Strict; Path=/`. A failure sleeps 1 s and counts;
  five failures in a row from the same IP block for 1 min.
- `authed()` accepts cookie **or** token (the token remains valid for local use and for the `?t=` of the SSE).
- `POST /logout` deletes the session. Sessions expire after 30 days without use.
- The page, without cookie and without token, redirects to `/login`.

Tests (`tests/test_auth.py`): the right password gives a cookie and access to `/api/state` and to the SSE; a wrong password
gives 403 and no cookie; without a password file nothing changes; block after five failures; logout invalidates;
the cookie is not valid with a `Host` other than the allowed one.

### 1.2 Listening outside 127.0.0.1

- `ORQ_BIND` (default `127.0.0.1`). With any other value, **the password file becomes
  mandatory**: the server refuses to start without it.
- `ok_host()` now accepts the IP/name in `ORQ_BIND` besides localhost.
- `ORQ_LOCAL_HOST` (default `laptop`): the name the server gives to the machine where it runs. In the container it
  is `nenhum`: there are no local agents, everything is ssh, including `server0`.

Tests: external bind without a password refuses; the ZeroTier `Host` accepted; without a local host the list of projects
and of agents does not try to run anything locally.

### 1.3 Container

- `Dockerfile`: `python:3.12-slim`, `openssh-client`, no `tmux` (it runs no agent), non-root user,
  `COPY orqweb/`, `CMD orq-web`. Small image.
- `docker-compose.yml` for server0: port `10.0.0.10:8765:8765`; read-only volumes:
  `~/.ssh` (key and `known_hosts`), `~/.config/claude-multiconta/accounts.json`, `~/.config/orq-web/password`;
  `restart: unless-stopped`.
- In the container, `~/.local/bin` does not exist: where does `ORQ_BIN` point? The utilities (`usage-all`,
  `claude-last`, `orq --status-json`) run **over ssh on server0**, not inside the container. The quota
  (`usage-all --json`) also now comes over ssh from a machine with the credentials: `ORQ_USAGE_HOST`
  (default: the local host; in the container, `server0`).
- `deploy/server0.sh`: `docker compose pull && docker compose up -d`, plus the creation of the password
  file if it does not exist (`openssl rand -base64 18`), with permission `600`. You read the password over ssh:
  `ssh server0 cat ~/.config/orq-web/password`.

Tests: `docker build` in the CI; a smoke test that starts the container with a fake `accounts.json` and a
password file, checks `GET /login` 200 and that `/api/state` without a cookie gives 403.

Real verification: bring it up on server0, open `http://10.0.0.10:8765/` from the laptop and from the phone through
ZeroTier, log in, open a tab for `kaggle@server0` and for `dev@node3`.

## Phase 2 — private monorepo and CI/CD  ✅ done on 2026-10-11

`https://github.com/cmarcond/orq`. CI per project green; image `ghcr.io/cmarcond/orq-web:latest`.
The monorepo is the source of truth from here on; the folders `~/Projects/orq-web` and `~/Projects/claude-multiconta` are
old copies. The server0 deploy still goes through `push-server0.sh`; to pull from GHCR, `docker login ghcr.io`
there with a `read:packages` PAT.

### 2.1 Layout

```
claude-orq/                     (name to be confirmed)
├── orq-web/                    history preserved (git subtree add of the current repo)
├── claude-multiconta/          current snapshot (it had no git)
├── claude-highlevel/           snapshot from server0 (31/08, the newest), reference only until phase 3
├── .github/workflows/
│   ├── orq-web.yml             py 3.8–3.12 + node tests; image build; push to GHCR on main and tags
│   ├── claude-multiconta.yml   `sh -n install.sh`, `py_compile bin/*`, usage-all tests (to be created)
│   └── highlevel.yml           `pytest -m "not slow"`
└── README.md                   map of the three
```

- Workflows with `paths:` to run only what changed.
- Image: `ghcr.io/<user>/orq-web:latest` and `:<sha>`; `GITHUB_TOKEN` is enough to publish.
- server0 does `docker login ghcr.io` once with a `read:packages` PAT, kept only there.
- No automatic deploy: the CI publishes, you (or I, over ssh) run `deploy/server0.sh`.

Tests: the CI itself green on the first push is the test; plus a `tests/test_packaging.py` that checks
that `orqweb/server/static` goes into the package and that the `Dockerfile` copies what `orq-web` imports.

### 2.2 Tests of claude-multiconta that do not exist yet

`usage-all` gets tests with recorded API responses (JSON fixtures): windows, pace, ordering,
`--json`. `install.sh` gets a shell test that runs in a temporary `HOME` and checks aliases and
`settings.json`. Small, but it takes the project off zero coverage.

## Phase 3 — levels (highlevel) inside orq-web  ✅ done on 2026-10-11

What changed from what was planned, discovered in the real test with two `claude -p` sessions:
- `SendMessage` refuses a name with `@` ("to must be a bare teammate name"), so `-n` receives the sanitised
  label (`config.session_name`: `kaggle@server0` → `kaggle-server0`); `{acima}`/`{abaixo}` in the roles
  become those names, not the labels;
- the message arrives as `user` with `isReplay`/`isSynthetic` and `origin = {kind: "peer", name, body, msg_id}`;
  the supervisor stamps `orq_origin: "agent"`. The `tool_result` of SendMessage carries the `msg_id`, which the page
  uses to mark the wire as delivered;
- the executor usually replies to the address `uds:/tmp/cc-socks/<pid>.sock` instead of the name; the page
  shows it as "reply";
- each peer message reopens the turn with a new `system init` in the stream (harmless);
- the recipes list only the levels that are added ABOVE the tab's agent (it is the executor, with no new role);
  there is no `bootstrap`: the person's first message already goes to the teacher, whose role tells it to delegate;
- the role travels through the stdin of `orq-agent spawn --role-file -` (over ssh) and stays in `~/.cache/orq/<name>/role.txt`;
- Python < 3.11 on the server uses a minimal TOML reader (`pipelines._parse_minimal`), tested against `tomllib`.

### 3.1 What changes in the supervisor

- Every agent of the page starts with `-n <label>` (e.g. `kaggle@server0`), to be addressable by
  `SendMessage`. Agents already open need to be reopened once.
- `orq-agent spawn` gains `--role-file` (text of the role, via `--append-system-prompt`) and `--parent`
  (label of the agent below). The state records `level` and `parent`.
- `stamp_origin` gains `agent`: a `user` message that arrived through another agent's `SendMessage` (the
  JSONL marks the origin; confirm the field in the test).

### 3.2 Recipes

`orqweb/pipelines/*.toml`, ported from highlevel (`professor-executor`, `professor-arquiteto-executor`).
`{acima}`/`{abaixo}` become the real labels. Read by the server (Python 3.11+ has `tomllib`); the
agent's machine only receives the text of the role.

### 3.3 Buttons

- **Upgrade** on a tab: picks the recipe (or the default one), starts the level(s) above on the same machine and
  folder, with `--parent` pointing to the current agent. The tab starts talking to the level above; the
  original agent becomes the executor, hidden behind.
- **Downgrade**: closes the levels above; the tab goes back to talking to the executor.
- The tab gains the **wires** panel, as in highlevel: `SendMessage` from the level above (output), arrival at the
  one below (delivery), reply back. It comes from the events that already pass through the socket: `tool_use
  SendMessage` in one stream and `user` with `orq_origin=agent` in the other.
- **Level** cards: state of each one (idle, working, asking permission), clickable to see
  that level's raw chat.

### 3.4 Tests

- `fake_claude` learns `SendMessage`/`ListAgents`: on receiving "delegue X", it emits `tool_use SendMessage`
  and, in a second fake process, the message arrives as `user`. With that the integration test starts
  fake executor and teacher through the real supervisor and checks the wires end to end.
- Reducer (`events.js`): actions `wireOut`, `wireIn`, `levelState`; tests in Node.
- Handlers: `POST /api/agent/<name>/upgrade {pipeline}` and `/downgrade`; refuses upgrade on a terminal
  agent and on an agent that is already a level above.
- Real verification: upgrade on `kaggle@server0`, ask for something, watch the executor work in the wires,
  downgrade.

### 3.5 What remains of claude-highlevel

The concept, the `.toml` recipes and the design of the screen (stage, wires, levels). The PTY, tailer and
gates code is not ported: the supervisor already delivers structured events and permissions without scraping a terminal.
The `claude-highlevel/` directory stays in the monorepo as a historical reference.

## Phase 4 — finishing touches and account switch  ✅ done on 2026-10-11

From the list drawn up after phase 3, these were done: wires show the level above when the executor
replies to the `uds:` address (and the teacher's role asks for a reply by the name `{eu}`); **↻ reopen with a
name** in the level picker (`reopen` + `spawn --restart`); a level's pending permission answered
on its own card in the panel; **automatic account switch** when the quota runs out (`state: quota` in the
supervisor, `Migrator` in the server, `retry` of the last message, `auto_switch` in accounts.json);
browser **notifications** and a count in the tab title.

Left for later: history of rounds and a timeline of milestones; model/effort per level; recipes
editable from the page; one teacher for several executors; search in the list of projects; a better levels
panel on the phone; levels in the grid of the terminal's `orq`; `orq-web --no-open`.

## Phase 5 — sets: opening several agents at once, with or without a teacher  ✅ done on 2026-10-13

What changed from what was planned: an agent's prompt stays in `state.json` itself (`role`), not only in
`role.txt`, so the photograph does not need to read a file on the machines; `snapshot` returns the JSON without saving
(the page shows it for editing and `POST /api/conjuntos` saves); `lider.prompt` only applies to recipes with one
level (those with two or more use the roles from the TOML); the photograph's recipe comes from the `pipeline` recorded in the
level's state by the upgrade (inferred from the level names and from the links only for levels from before this version),
because the list of links fluctuates when the machine's `orq-agent links` fails under load. Found in the real QA: a teacher whose base was
closed without ↓ undo survives and sticks to the next agent with the same label (the folder is the same), and ⇅ refuses
with "already has levels"; now opening a label (through + or through a set) closes those orphan levels
(`handlers.close_orphan_levels`). Tests: `tests/test_sets.py`,
`tests/test_handlers.py` (sets), `tests/test_supervisor.py` (permission/role/set in the state) and
`qa/tests/test_conjuntos.py`.

A **set** is a JSON that describes agents to open together: the jobs that were in progress
(each one in its folder and machine, conversation resumed) or a group with a leading teacher, in which each agent
starts with its own system prompt. A **⊞ sets** button next to the tabs lists the saved ones, opens one,
takes a ready-made JSON, or photographs the open tabs into a new set. It also works on `/m`.

Decisions taken with the author on 2026-10-13: sets live in a **folder on the server** editable from the page
(`~/.config/orq-web/conjuntos/*.json`, `ORQ_SETS_DIR`; in the container, a writable volume); **two modes**, without a leader
(independent agents) and with a leader (teacher over the first agent, the others linked to it, recipe
`professor-executores`, as `upgrade` already does); the JSON can carry an **initial task**, a **fixed account** and a **permission
mode**; if any agent of the set **is already open** (same machine and folder), the page **refuses and
says** which ones, without opening anything.

### 5.1 The format

```json
{
  "nome": "artigo-kaggle",
  "descricao": "model on server0, text on node3, teacher coordinates",
  "permissao": "auto",
  "agentes": [
    {"maquina": "server0", "pasta": "~/Projects/kaggle", "modo": "resume",
     "conta": "work3", "prompt": "You look after the model…", "tarefa": "Resume from where you stopped and report."},
    {"maquina": "node3", "pasta": "~/Projects/paper", "modo": "new",
     "prompt": "You write the paper…"}
  ],
  "lider": {
    "receita": "professor-executores",
    "prompt": "optional: replaces the role from the TOML; accepts {executores}, {abaixo}, {eu}",
    "tarefa": "optional: first message to the teacher"
  }
}
```

- `nome` is the id (sanitised file name). `descricao`, `permissao`, `conta`, `prompt`, `tarefa` and `lider` are
  optional. Default `modo`: `resume` (the most recent conversation of the folder; `new` if there is none).
- `permissao` applies to the whole set (`auto` | `acceptEdits` | `manual`); without it, the machine's
  `permission_mode`. All the agents and levels start in the same mode (different modes hold messages between levels).
- `conta` absent: `assign` chooses by the slack, for the whole set at once, which already spreads the load across
  the accounts (`PER_AGENT`) instead of piling them all on the same one.
- An agent's `prompt` goes through `--append-system-prompt`, like a level's role. The `_outbox/` note continues.
- Without `lider`, each agent is independent: no link is made. With `lider`, all the others are linked to the
  first and the teacher is added above it; `lider.prompt` replaces `papel` from the TOML (with the same markers).
- In `tarefa`, the text is delivered as the person's message as soon as the agent confirms that it started; with `lider`, the
  agents' `tarefa` is delivered before the teacher's.

### 5.2 Supervisor (`orq-agent spawn`)

- `--role-file` now applies to a base agent, not only to a level: without `--parent`/`--level`, it is just the prompt. The
  `state.json` records `role: true`, and `--restart` (account switch, reopen with name) reuses `role.txt`
  whenever it exists, not only when there is `parent`/`level`.
- New `--permission-mode`, recorded in `state.json` and reused on `--restart`. Without it, the machine's.
- `state.json` gains `conjunto: <id>` (which set the agent came from), for the photograph (5.4) and for the tab
  to show where it came from.

### 5.3 Server (`orqweb/server/sets.py` and routes)

| route | what it does |
|---|---|
| `GET /api/conjuntos` | catalogue: id, nome, descricao, number of agents, leader or not, machines, `aberto` (how many of its agents are already open) |
| `POST /api/conjuntos` `{json}` | validates and saves (creates or replaces by `nome`); it is the "upload JSON" and the "save as set" |
| `GET /api/conjuntos/<id>` | the JSON, to download or edit elsewhere |
| `POST /api/conjuntos/<id>/delete` | deletes the file (the button asks for confirmation) |
| `POST /api/conjuntos/<id>/open` | opens: refuses (409, list) if any `maquina:pasta` already has a live agent or a terminal claude; otherwise starts everything |
| `POST /api/conjuntos/snapshot` `{nome}` | photographs the open tabs (5.4) and saves |

Opening (`/open`): the response comes back at once with the plan (labels, accounts, reasons), and the work runs in a thread
of the server, because opening N agents over ssh takes 5 to 15 s each. The agents enter as provisional as they start,
so the tabs appear on the page through the normal `/api/state`. Order: `spawn` of the agents (in parallel between different
machines, **in sequence on the same machine**, because ssh in a burst to the same machine drops); then, with a leader,
`link` of each one with the first and `upgrade` with the recipe and the leader's `prompt` (the same routine as today, factored out of the
handler into a function); finally, `tarefa`s delivered when each agent leaves `opening…` (waits up to 90 s). The
progress goes in `/api/state.conjuntos_abrindo` (`{id, etapa, feitos, erros}`) and the page shows it as a toast; a failure in one
agent does not undo the others, it is only listed.

Validation (`sets.validate`): known fields, `maquina` among the configured ones, `conta` among the accounts, `permissao`
among the modes, `modo` in `resume|new`, `lider.receita` in the catalogue, at least one agent, `nome` sanitisable and no
collision of `maquina:pasta` within the set itself.

### 5.4 Photograph of the open tabs

`snapshot` builds a set from the current state: one agent per tab (machine, folder, current account, `modo:
resume`, `prompt` if the agent started with `role.txt` — the server reads it through `orq-agent cat` — and `permissao` from
`state.json`); if the current tab has levels above, it becomes `lider` with the recipe registered in `level` and those linked to it
enter as agents; the rest of the tabs enter as independent. The page asks for the name, shows the resulting JSON
in an editable preview and only saves on confirmation.

### 5.5 Page

- **⊞ sets** button in the tab strip (next to the quota) and, on `/m`, in the drawer. It opens the existing picker
  (`picker`) with one row per set: name, description, `N agents · machines · 👨‍🏫 leader`, and on the right **open**,
  **⬇ JSON**, **✕**. Top row: **📄 upload JSON** (file input, validates on the server and lists the error with the
  line) and **📷 save open tabs**.
- Opening with some agent already open: red toast "already open: kaggle@server0, paper@node3; close them or open
  only the others by hand", without opening anything.
- During the opening, progress toasts (`conjuntos_abrindo`); when it finishes, `show()` of the first agent (or of the
  leader's top).
- The tab of an agent that came from a set shows `⊞ name` in the state of the bottom bar.

### 5.6 Tests

- `tests/test_sets.py`: validation (each error), catalogue with a broken file skipped, `plan` (labels, `assign`
  of the whole set, the leader's markers), refusal because of an agent already open, snapshot from an assembled state.
- `tests/test_handlers.py` + `fake_claude`: opening a set of two without a leader → two `spawn` with `--role-file`
  and `--permission-mode`, no `link`; with a leader → two `spawn`, one `link`, one `upgrade` with the role
  replaced, `tarefa`s delivered in order; `delete` and `POST` with invalid JSON (400 with the reason).
- `tests/test_supervisor.py`: `spawn --role-file` without a level records `role: true`; `--restart` reuses the role
  and `--permission-mode`.
- QA (`qa/tests/test_conjuntos.py`): upload JSON, open, see tabs, save open tabs, delete.
- Real verification: a set with `kaggle@server0` + a folder on node3 and a teacher; the initial task arrives.

### 5.7 Docs

`docs/04-orq-web-page.md` gains 4.12 "Sets"; `docs/07-reference.md` the routes and the schema of the JSON;
`docs/05-architecture.md` the sequence "opening a set" in 5.5 and `ORQ_SETS_DIR` in 5.4.

Estimated size: ~900 lines (sets.py ~200, handlers ~150, supervisor ~60, app.js ~200, tests ~300), plus docs.
Suggested order: supervisor (5.2) → sets.py and routes without a leader → page → leader and tasks → snapshot → QA and docs.

## Phase 6 — usage history: collector every 5 min, SQLite database and commands that compute over it  ✅ done on 2026-10-22

**Why.** The quota API (`/api/oauth/usage`, undocumented) only gives the percentage *right now* of each window. Without
keeping samples there is no "how much I spent today", "at what pace", "when it runs out" or "how much a quota point is worth
in tokens". And `usage-all`, the orq page and the automatic account switch each query the API on their own, which
leads to 429 (`rate_limit_error`) in bursts. A single collector, every 5 minutes, solves both things.

Decisions (2026-10-22, defaults assumed; the author reviews after using it): database **only on node4** (always on; the
laptop queries over ssh); it keeps **everything** (messages and quota; snapshots of processes for 14 days); the orq page
keeps reading `usage-all --json`, which now comes from the database (its own integration is left for 6.8).

### 6.1 Parts

| part | where | what it does |
|---|---|---|
| `bin/usage-hist` (new, claude-multiconta) | node4 | collector (`coletar`), installer of the timer (`instalar`/`remover`/`estado`) and all the reports (`painel`, `dia`, `semana`, `calibrar`, `alertas`); also the library of database and calculations that `usage-all` and `usage-sessoes` load |
| `usage-coletor.timer` (user systemd) | node4 | runs `usage-hist coletar` every 5 min, survives reboot (linger), log in the journal |
| `~/.local/share/claude-multiconta/usage.db` (`USAGE_DB`) | node4 | SQLite in WAL; one writer (the collector, with a lock), several readers |
| `usage-sessoes --agente --desde T` | each machine | agent mode that returns the responses (one line per message) since T, plus prompts and processes |
| `usage-all`, `usage-sessoes` | any machine | read the database when it is less than 10 min old (`--ao-vivo` forces the API/scan); from the laptop, over ssh to node4 (`usage_db_host` in accounts.json) |

### 6.2 Collection (every 5 min)

1. **Quota**: `get_windows` of `usage-all` for each account, in parallel; each window (`session`, `weekly_all`,
   `weekly_scoped` per model) becomes a sample `(ts, account, kind, scope, %, resets_at)`. An error (429, token) goes
   to `quota_err` and does not interrupt the other accounts. With 429, the next round waits twice as long (up to 30 min).
2. **Consumption**: on all the machines, in parallel, one ssh connection each (the script goes through stdin, like
   `usage-sessoes`), the agent returns the responses since that machine's last successful collection minus 15 min
   (no state on the machine: the overlap is deduplicated by the message key). First collection: 7 days
   back (`--desde-horas`), so that the calibration already has data. Machine down: when it comes back, the window covers the gap,
   because the conversations stay recorded on it. The quota, on the contrary, cannot be recovered: a quota gap stays marked.
3. **Processes**: the snapshot of the live claude processes on each machine (account, folder, kind, tmux, who launched it).
4. **Health**: one row per machine per round (`runs`: ok/error, duration), for `estado` and the alerts.

### 6.3 Schema

```
quota(ts, account, kind, scope, pct, resets_at, severity)        PK (ts, account, kind, scope)
quota_err(ts, account, err)
messages(key PK, ts, host, account, session, project, cwd, entrypoint, model,
         input, output, cache_new, cache_read, sub)              key = message id | requestId
sessions(account, session PK, host, project, cwd, entrypoint, prompt, first_ts, last_ts)
processes(ts, host, pid, account, kind, cwd, tmux, parent, session_arg, seconds, last_activity)
runs(ts, host, ok, err, seconds)
meta(key PK, value)                                              schema version, last 429
```
No text of the conversations: only numbers, session, folder, model and the beginning of the sessions' prompt (which identifies a
script). Expected size: a few MB per month.

### 6.4 Calculations

- **Consumption in the interval** (`consumed`): sum of the rises between consecutive samples of the same window; if
  `resets_at` changed or the % fell, there was a reset and the new % counts from zero. It applies to 5h and 7d.
- **Today** per account: points of the week (7d) consumed since local midnight, and points of the 5h window.
- **Pace**: points/h in the last hour and in the last 6 h; **forecast**: hours until 100% at the 6 h pace, compared with the
  reset ("runs out before the reset" becomes an alert); **fleet**: free points added up and in how many days they run out at the
  24 h pace.
- **Calibration tokens → points**: per account, in the last 7 days, `weight of the tokens / 7d points consumed`
  (the weight counts input 1, new cache 1.25, read cache 0.1, output 5). With that `usage-sessoes` and `dia`
  show **estimated points per session and per project**, not only the relative share. Only with 5+ points of sample.
- **Profile**: consumption per hour of the day and per day of the week (when you use it).
- **Alerts** (`usage-hist alertas` and in the panel):
  - *rose because of a script*: 5+ points of 5h in 30 min, 80%+ coming from `sdk/-p` sessions (the case of `claude-diario`);
  - *rose without consumption on the machines*: the quota rose and the tokens seen explain less than 20% (claude.ai, phone
    or a machine down);
  - *runs out before the reset*: forecast of 7d or 5h before the reset time;
  - *forgotten process*: claude process idle for more than 1 day;
  - *collector stopped*: last collection more than 15 min ago, or a machine without response for more than 1 h.

### 6.5 Commands

| command | what it shows |
|---|---|
| `usage-hist` | panel: per account, 5h and 7d now, **today**, pace, forecast, **bars of the last 24 h**; fleet; alerts; age of the last collection |
| `usage-hist dia [YYYY-MM-DD]` | the day: points per account, per hour (bars) and the projects/sessions that spent the most, in estimated points |
| `usage-hist semana` | 7 days × accounts in points, total per day, profile per hour of the day |
| `usage-hist calibrar` | tokens per point of each account and the size of the sample |
| `usage-hist alertas` | only the alerts (for a warning cron, later) |
| `usage-hist coletar` / `instalar` / `remover` / `estado` | collects once; installs/removes the timer; health of the collector and of the database |
| `usage-all` | the usual, now from the database (instant, no 429), with "from the database · N min ago" in the footer; `--ao-vivo` |
| `usage-sessoes` | the usual, now from the database, with **estimated points** per row; `--ao-vivo` |

All with `--json`. On the laptop, with no local database, the commands run on node4 over ssh (`usage_db_host`), and
`usage-all` falls back to the API if ssh does not respond in 8 s.

### 6.6 Tests

- **Unit** (`tests/test_usage_hist.py`): schema and idempotent insertion; `consumed` with rises, reset by
  `resets_at` and fall of %; today with local time zone; pace, forecast and fleet; calibration; each alert; agent
  `--desde` (window, dedupe, subagent, prompt).
- **Interface**: each report (`painel`, `dia`, `semana`, `calibrar`, `alertas`, `estado`) generated over a sample
  database, checking the rows of the table; `coletar` end to end with the fake API and ssh; `usage-all` and
  `usage-sessoes` reading the fresh database, falling back to live with an old database, and with `--ao-vivo`.
- **Real**: timer installed on node4, 3 rounds, commands on node4 and on the laptop.

### 6.7 Deploy

`usage-hist` in both places (`~/.local/bin`), new `usage-all` and `usage-sessoes`; `usage_db_host: "node4"` in
accounts.json (repository and laptop); `usage-hist instalar` on node4 with the first collection of 7 days.

### 6.8 The orq page reading the database  ✅ done on 2026-10-22

The server runs `usage-hist painel --json` every 60 s with the page open (on the quota machine, over ssh in the
container; `External.history`, `StateCache.refresh_history`) and sends the result in `/api/state` as `history`.
The account cards gain the bars of the last 24 h (SVG, one per hour, common scale, tooltip per hour) and the
summary today · pace · forecast (red with ⚠ when it runs out before the reset); the TOTAL, the 24 h points and the
warning of a stopped collection; the ⚠ alerts become toasts, one by one every 6 h (remembered in the browser). Pure functions in
`format.js` (`sparkSvg`, `histSummary`, `freshAlerts`) with tests in Node. As a bonus, on the phone the sets
button stopped covering the title of the TOTAL.

### 6.9 Account panel  ✅ done on 2026-10-23

Clicking on an account's card opens its panel: five numbers (week, 5 h, today, pace, forecast), the curve of the
week until the reset with the ideal pace and the projection at the current pace, the 24 h per hour, the 7 days and what spent the most
(24 h or 7 days, with ≈pts). `usage-hist conta NAME [--json]`, `GET /api/conta/<name>` (30 s cache), charts in
`static/js/charts.js` (pure SVG, tests in Node). As a bonus: the server ignores a second SIGTERM during
shutdown (`uv run` passes its own on, and the second interruption landed in the middle of the cleanup).

### 6.10 Alert badge on the card  ✅ done on 2026-10-23

`usage-hist` now delivers the alerts structured (`alert_items`: account, kind, `warn`/`info`, short label,
text); the page puts a badge on the account's card while the alert lasts (red for ⚠, grey for a notice; title
with the text) and the count of ⚠ of the fleet on the TOTAL. The toast continues. `badgeFor`/`fleetBadge` in `format.js`, with
tests in Node.

### 6.11 "usage" tab  ✅ done on 2026-10-23

📊 usage opens, in the panel's window, the `usage-sessoes` table from the database: 5 h, 24 h or 7 days; per account, the
quota and one row per machine + folder + kind (share, ≈pts, now, live process) and the forgotten processes; rows
of open agents lead to their tab. The grouping moved out of `render_brief` into `usage-sessoes.brief_groups`, which the
terminal and the page use (`usage-hist uso`, `GET /api/uso`, `static/js/usage.js` with tests in Node).

### 6.12 Later (not in this round)

The automatic account switch using the forecast to switch *before* it runs out; warning by notification (ntfy/e-mail)
of the alerts; automatic weekly report.

---

## Estimate and order

| phase | size | dependencies |
|---|---|---|
| 1 login + bind + container + deploy | ~500 lines + Dockerfile/compose | none |
| 2 monorepo + CI/CD | little code, a lot of configuration | phase 1 (for the image to be worth it) |
| 3 levels | ~800 lines (supervisor, server, page, fake_claude, recipes) | phase 1 (reopen agents with `-n`) |

Known risks: in the container, the `laptop` is only reachable if it is on and with ssh through ZeroTier;
messages between levels in different permission classes are held (highlevel already documents it),
which is why all the levels start in the same `permission_mode`.
