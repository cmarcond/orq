# 7. Reference

## 7.1 Command lines

The synopses are shown as the tools print them, so the argument placeholders are in Portuguese:
`conta`/`CONTA`/`C` account, `pasta`/`PASTA`/`P` folder, `máquina` machine, `rótulo`/`L` label, `nome`
name, `data` date, `ARQ` file, `INI-FIM` start-end.

### claude-multiconta

| command | use |
|---|---|
| `claude`, `claude-<conta> [args]` | opens Claude Code on the account, in a tmux session `claude[-<conta>]-<pasta>` |
| `claude-<conta>-login` | login without a browser (prints the URL; paste the code) |
| `claude-usage-<conta>` | `usage-all <conta>` |
| `claude-whoami` | e-mail and plan of each directory; which one is plain `claude` |
| `usage-all [-v] [--json] [--ao-vivo] [conta…]` | quota of all accounts + TOTAL, from the `usage-hist` database when it is fresh (`--ao-vivo` = API); `--json` for scripts |
| `usage-hist [painel\|conta NOME\|uso\|dia [data]\|semana\|calibrar\|alertas\|estado\|coletar\|instalar\|remover] [--conta C] [--json]` | usage history in the SQLite database on node4: today, pace, forecast, per hour, per project (≈pts), alerts; `coletar` is what the timer runs every 5 min |
| `usage-sessoes [-v] [--horas N] [--conta C] [--maquinas a,b] [--json] [--tudo]` | who spends each account on all machines, with the quota alongside: summarized by folder and type; `-v` each session and process |
| `codex-usage [-v]` | Codex quota |
| `claude-last [-a] [pasta]`, `--pick [N]`, `--as CONTA`, `--projects` | conversations per account; copy between accounts; list of the machine's projects |
| `claude-resume [N]`, `claude-resume --as CONTA [N]` | resumes on the conversation's account if it is in good shape, otherwise on the best one (`usage-all --melhor --fica`); `--as` forces it |
| `claude` (function), `CLAUDE_CONTA=X claude` | opens the best account right now (`usage-all --melhor`), or the forced one |
| `usage-all --melhor [--fica CONTA] [-v]` | prints the best account: the quota that expires first (what is left ÷ hours until the reset), excluding those with <5 pts or 5 h ≥85% |
| `orq`, `orq --new`, `orq --list`, `orq --status-json`, `orq web` | tmux grid of 4 projects; state of the agents; shortcut to the page |
| `orq-run --dir P --account C [--label L] [--mode resume\|new] [--tmux] [--depth N]` | one pane of the grid (called by `orq`) |
| `./install.sh [--no-install\|--uninstall]` | installs/updates/removes |
| `./add-account.sh nome email [rótulo]`, `--remove nome` | accounts |
| `./make-tarball.sh` | package for another machine |

### orq-web

| command | use |
|---|---|
| `orq-web [--port N]` | starts the local server and opens the browser (`ORQ_NO_OPEN=1` does not open it) |
| `converte-web [rótulo@máquina…] [--all]` | lists/converts interactive tmux claudes into agents of the page |
| `./install.sh` | installs on this machine (`~/.local/lib/orq-web`, commands in `~/.local/bin`) |
| `./deploy.sh [máquina…]` | installs on the laptop and on the machines in `orq_hosts` (or on the ones named) |
| `./deploy/push-server0.sh [máquina]` | recreates the container of the network page |
| `./deploy/macos-tmux-keep.sh [--remove]` | the tmux LaunchAgent on the remote Mac |

### orq-agent

| subcommand | use |
|---|---|
| `spawn --dir P --account C --label L [--mode resume\|new] [--force] [--role-file ARQ\|-] [--parent L2] [--level N] [--permission-mode M] [--set S] [--pipeline R] [--restart]` | makes sure the supervisor is in a tmux session `orqs-<L>`; `--restart` closes what is running and starts it again (and reuses role, permission and set from `state.json`); `--role-file -` reads the role from stdin (without `--parent`/`--level` it is just the agent's prompt); `--permission-mode` changes the mode for this agent only; `--set` marks the set it came from; `--pipeline` the recipe a level came from |
| `start …` | the supervisor in the foreground (it is what `spawn` opens in tmux) |
| `attach L [--since N]` | stdin/stdout bridge to the socket (what the server uses) |
| `stop L` | stops claude and the supervisor |
| `list` | agents on this machine with their state |
| `convert L [--account C]` | closes an interactive tmux claude and reopens the conversation as an agent; prints JSON |
| `ls PASTA` | files of the folder as JSON `[{name,size,mtime}]` |
| `cat ARQ [--range INI-FIM]` | one JSON line `{size,start,length}` and the bytes |
| `link A B`, `unlink A B`, `links`, `link-run A B` | links (chapter 6) |
| `peer L [--probe PID \| --mirror --sock S --from A \| --unmirror PID --sock S]` | remote side of the link; answers in JSON, errors as `{"err": …}` with exit code 0 |

## 7.2 The server's HTTP API

Authentication: the `X-Orq-Token` header (the token in the link) or `?t=` on the SSE, or the session
cookie obtained from `POST /login` (form field `password`). The `Host` header must be in
`ORQ_ALLOWED_HOSTS`. Errors come as `{"err": "text"}` with 400/403/404/409/500.

| route | body / parameters | response |
|---|---|---|
| `GET /` | | the page (redirects to `/login` without a cookie when the password is on) |
| `GET /m` | | the phone version (same `app.js`, `mobile.css`); without a cookie, `/login?next=/m` |
| `GET /login?next=`, `POST /login`, `POST /logout` | `password=`, `next=` (`/` or `/m`) | session cookie and back to `next`; 429 after repeated attempts |
| `GET /api/state` | | `{usage, usage_ts, agents, links, agents_ts, host_errors, accounts, labels, colors, migrations, auto_switch, conjuntos_abrindo}` |
| `GET /api/projects` | | `{items: [{host, cwd, account, mtime, title, …}]}` |
| `GET /api/pipelines` | | `{items: [{id, titulo, descricao, niveis, cores, multi}], default}` |
| `GET /api/conta/<conta>` | | the account's panel (`usage-hist conta --json`): now, today, pace, forecast, `hourly_24h`, `days_7`, `week` (series of the week until the reset), projects for 24 h and 7 days, alerts, calibration; 404 unknown account, 503 no history |
| `GET /api/uso?horas=5\|24\|168` | | the usage tab (`usage-hist uso --json`): per account, the quota, `groups` (machine, folder, type, n, time span, share, ≈pts, now, live process), `rest` and `stale`; 400 invalid hours, 503 no history |
| `POST /api/agent/open` | `{host, cwd, account?, mode: "resume"\|"new"}` | `{label, acc, why, orphans_closed}`; 409 if there is a terminal claude in the folder; orphan levels from an earlier life of the label are stopped |
| `POST /api/agent/convert` | `{label}` | `{label, how, msg}` |
| `GET /api/agent/<L>/stream?since=N` | | SSE: one `data:` per agent event |
| `POST /api/agent/<L>/send` | `{text}` | `{ok}` |
| `POST /api/agent/<L>/permission` | `{request_id, allow, input?}` (`input.answers` for questions) | `{ok}` |
| `POST /api/agent/<L>/interrupt` | `{}` | `{ok}` |
| `POST /api/agent/<L>/stop` | `{}` | `{msg}` |
| `POST /api/agent/<L>/upload` | raw body, `X-Filename` | `{path, bytes}`; writes to `_inbox/` (max. 200 MB) |
| `GET /api/agent/<L>/files` | | `{dir, items: [{name,size,mtime}], err?}` of the `_outbox/` |
| `GET /api/agent/<L>/file?name=X[&dl=1]` | optional `Range` | the file (inline for image/PDF/video/audio/text, otherwise an attachment); 206 with `Range`; 416 outside the file |
| `POST /api/agent/<L>/upgrade` | `{pipeline?}` | `{pipeline, levels, top, links, link_errors}` |
| `POST /api/agent/<L>/downgrade` | `{}` | `{stopped, errors, unlinked}` |
| `POST /api/agent/<L>/reopen` | `{account?}` | `{label, acc}`; same conversation, with a name or on another account |
| `POST /api/agent/<L>/link` | `{to}` | `{ok, link, note_errors}` |
| `POST /api/agent/<L>/unlink` | `{to}` | `{ok, told}` |
| `GET /api/conjuntos` | | `{items: [{id, nome, descricao, n, maquinas, lider, permissao, abertos}], dir}` |
| `POST /api/conjuntos` | the set's JSON (4.13) | `{id, conjunto}`; 400 with all the validation problems |
| `GET /api/conjuntos/<id>` | | `{id, conjunto}` |
| `POST /api/conjuntos/<id>/delete` | `{}` | `{ok}` |
| `POST /api/conjuntos/snapshot` | `{nome?}` | `{conjunto, avisos}`: the open tabs as a set (does not save) |
| `POST /api/conjuntos/<id>/open` | `{}` | `{id, nome, agentes: [{label, host, acc, why}], lider}`; 409 `{err, abertos}` if any is already open; progress goes in `conjuntos_abrindo` of `/api/state`: `{id, nome, etapa (abrindo\|vínculos\|professor\|tarefas\|pronto), labels, feitos, erros, top, done, ts}` |

An agent in `/api/state`: `{label, host, account, dir, state, msg, pid, kind ("sdk"\|terminal), name,
parent?, level?, permission?, role?, set?, pipeline?, tmux?, ts}` (`role` is the extra prompt it
started with; `set` the set it came from; `pipeline` the recipe of a level, which the set snapshot
reuses). A link: `{local, remote, host, remote_host, kind ("local"\|"ssh"), local_name, remote_name,
up, resources?, sessions?, why?}`.

## 7.3 Labels and names

| form | example | where |
|---|---|---|
| label | `kaggle@server0`, `kaggle-2@server0`, `kaggle~professor@server0` | page, `orq-agent`, `state.json` |
| sanitized label | `kaggle-server0`, `kaggle-professor-server0` | folder in `~/.cache/orq/`, session name (`-n`), `SendMessage` |
| the agent's tmux session | `orqs-kaggle`, `orqs-kaggle-professor` | on the machines |
| a link's tmux session | `orql-kaggle--dados-node3` | on the machine of the local side |
| terminal tmux session | `claude-foo`, `claude-gmail-foo` | claude-multiconta's aliases |
| the `orq` grid | server `tmux -L orq`, session `grade`; remote ones in `orq-<pasta>` | terminal |

## 7.4 Environment variables

Server: see chapter 2 (plus `ORQ_SETS_DIR`, the folder for the sets, default
`~/.config/orq-web/conjuntos`). Agent and utilities: `ORQ_STATE_DIR`, `ORQ_BIN`,
`CLAUDE_MULTICONTA_CONF`. QA: `ORQ_QA_URL`, `ORQ_QA_PASSWORD`, `ORQ_QA_VIA` (ssh tunnel),
`ORQ_QA_PORT` (8790), `ORQ_QA_PROJECT`, `ORQ_QA_PROJECT2`, `ORQ_QA_HEADED`, `ORQ_QA_KEEP`.
`orq-run`/`orq-hook`: `ORQ_TTY`, `ORQ_DEPTH`, `ORQ_LABEL`, `ORQ_STATE`.

## 7.5 Constants that change the behaviour

| where | constant | value | effect |
|---|---|---|---|
| `server/state.py` | `USAGE_EVERY` | 150 s | quota poll |
| | `AGENTS_EVERY` | 15 s | poll of agents and links |
| | `PROJECTS_TTL` | 60 s | validity of the project list |
| | `OPEN_WINDOW` | 60 s | "page open" since the last request |
| | `CLOSING_TTL` | 30 s | an agent told to stop counts as closed |
| | `PROVISIONAL_TTL` | 60 s | a newly opened agent stays until the machine confirms it |
| `agent/supervisor.py` | `QUIT_GRACE` | 15 s | between closing stdin and killing claude |
| `agent/events.py` | `REPLAY_MAX` | 400 | entries of the old history that are resent |
| `agent/link.py` | `POLL`, `REMOTE_POLL`, `TUNNEL_WAIT`, `ENTRY_WAIT` | 5 s, 30 s, 20 s, 40 s | link cycle, remote probe, wait for the tunnel and for the card |
| `agent/client.py` | `spawn_link(wait)` | 100 s | how long `link` waits for `link-run` to come up |
| `server/external.py` | `link` timeout | 150 s | deadline for `orq-agent link` over ssh |
| `server/assign.py` | `LIMIT`, `PER_AGENT`, `STAY` | 95, 20, 10 | account out of quota; weight per agent; preference for staying |
| `server/handlers.py` | `UPLOAD_MAX` | 200 MB | attachment |
