# 8. Operations: tests, diagnosis, open items

## 8.1 Tests

```sh
cd orq-web
make test          # pytest (tests/) + node --test (tests/js/)
make lint          # ruff check + ruff format --check (line length 110, target py38)
make fmt
cd ../claude-multiconta && python3 -m pytest -q tests
```

The orq-web tests do not need Claude: `tests/fake_claude.py` imitates the stream-json protocol
(permission, `AskUserQuestion`, interruption, injection, streaming, and `SendMessage` between two fake
processes through a mailbox in `FAKE_PEER_DIR`). `test_supervisor.py` covers the supervisor through
the socket; `test_handlers.py` starts the real HTTP server with the supervisor behind the bridge;
`test_link.py` uses real Unix sockets and a fake `ssh -N` that listens on them. Unix sockets require
short paths (a limit of ~104 bytes on macOS): these tests use `/tmp`.

## 8.2 QA with Playwright: the real application, with the real claude

```sh
cd orq-web
uv pip install --python .venv/bin/python -e ".[qa]" && .venv/bin/python -m playwright install chromium
make qa-smoke                          # only what spends no quota (the page opens, the picker lists)
make qa                                # everything
make qa-rede                           # smoke against the network page, through an ssh tunnel
ORQ_QA_HEADED=1 pytest qa/tests/test_arquivos.py   # with a window (records the video with full Chrome)
```

Each test starts an `orq-web` just for the QA on port 8790 (or points to `ORQ_QA_URL`), drives the
page in a Chromium and leaves in `qa/out/` the final screenshot, the console and the **video of the
session** (`<test>.mp4`; `-FALHOU` in the name when it fails). Tests: `test_smoke.py`, `test_chat.py`
(answers, interrupts), `test_niveis.py` (hello world of the teacher → executor), `test_troca_conta.py`
(forces `state: quota` in `state.json` and sees the conversation continue on another account),
`test_arquivos.py` (`_outbox/` with and without claude), `test_vinculos.py` (one agent asks the
other), `test_professor_executores.py`. `qa/orqqa.py` is also a command line for driving the page by
hand (`up`, `open`, `say`, `allow`, `levels`, `link`, `quota`, `shot`, `watch`…).

Against the network page, the laptop's Chromium and python **cannot reach** the ZeroTier IP (macOS's
local network privacy only lets Apple binaries through); `ORQ_QA_VIA=server0` opens an ssh tunnel and
the password comes from there. The tests that use real agents start the `orq-agent` **installed** in
`~/.local` (it is what the server calls): run `./install.sh` first, if the agent changed. Chromium
without a window does not render PDFs; with `ORQ_QA_HEADED=1` it does.

## 8.3 Diagnosis: what has already happened and how to fix it

| symptom | cause | what to do |
|---|---|---|
| a second ssh connection right after the first to the same machine drops ("Connection closed") | unstable network; the second handshake right after the first fails | group the commands into one connection (that is what `External.agents` does) or wait ~4 s |
| the laptop's browser/python: "No route to host" for the ZeroTier IP; `curl` and `ssh` work | macOS's local network privacy for non-Apple binaries started from launchd's tmux | ssh tunnel (`ORQ_QA_VIA`, or `ssh -N -L 8765:<ip>:8765 server0`) |
| the quota of a remote Mac does not appear; agent on the Mac "logged out" | ssh sessions cannot see the Keychain | `deploy/macos-tmux-keep.sh`; the quota is read with `tmux run-shell` |
| `claude-<conta>` exits immediately with `rgb:` garbage in zsh | the tmux server was born in a folder that was deleted | restart the tmux server (LaunchAgent on the Mac); the aliases now start in `~` |
| `usage-all` returns 429 | frequent queries to the usage API | wait; the page queries every 150 s |
| the file list says "orq-agent antigo" (old orq-agent) | the agent's machine has an `orq-agent` without `ls`/`cat`/`link` | `orq-web/deploy.sh <máquina>` |
| TOTAL does not appear on the network page's panel | the `usage-all` on the `ORQ_USAGE_HOST` machine is old | reinstall claude-multiconta there (tarball + `install.sh --no-install`) |
| a linked agent "does not appear as reachable" in `SendMessage` | `ListAgents` only reads the `sessions/` folder of its own account; the link was not made from the page | link from the page (🔗); orq mirrors the cards |
| a link shown as alive after reopening an agent | old record without the pids of the sessions | new records keep `sessions`; the old ones count as dead |
| a level starts but the page stays on the "vínculos" (links) view | fixed: when levels are raised the panel switches to the levels view | |
| `/api/agent/<x>/files` returns 404 right after opening an agent | the page asks before the server knows the agent | silent; the list arrives on the next attempt (exact cause not yet understood) |
| the page jumps to another tab during the account switch | fixed: the new agent enters as provisional before the `spawn` | |
| `/exit` does not reach the terminal claude when converting | claude did not respond | the conversion escalates to Ctrl-C and SIGTERM |
| machine down (`node0/1/2` in October 2026) | | `orq-web/deploy.sh <máquina>` and the claude-multiconta tarball when it comes back |

Useful logs: `~/.cache/orq/<agent>/supervisor.log` (what the supervisor said on exit),
`~/.cache/orq/<agent>/events.jsonl` (everything claude emitted), `~/.cache/orq/<agent>/links/*.log`
(the link and the ssh stderr), `docker logs orq-web` on server0 (logins, migrations),
`qa/out/orq-web-8790.log`.

## 8.4 Security, in short

- The local server only listens on `127.0.0.1` with the token in the link. Over the network: password,
  cookie, lockout after repeated attempts, port published only on ZeroTier, `Host` checked.
- The container has neither Claude credentials nor `claude`; it reaches the machines over ssh with the
  user's key (read-only) and its own `ssh_config`.
- Nothing the model writes runs in the page's origin: sanitized Markdown, CSP, `_outbox/` files in a
  sandbox, HTML always as a download.
- Session credentials (`.key`) only travel over ssh and are kept with mode 0600; access tokens are
  never printed.
- `permission_mode: auto` lets Claude decide the routine cases; `manual` asks about everything. The
  agents run with the rights of the machine's user.

## 8.5 Open items and ideas

Small ones, noted during development:

- in the wires of an agent without levels, the base agent appears as "executor" (`levelName`);
- `orq-agent links` lists undone links forever; prune the old ones;
- the `professor-executores` recipe across different machines only has a unit test (the QA uses two
  agents on the laptop);
- the 404 from `/files` right after opening an agent;
- account migration of an agent with a link does not rebuild the link;
- `Range` for a large video coming over ssh works, but the file is read in full on each request.

Larger ones (phase 4 of `orq-web/docs/PLANO.md`): history of level runs and a timeline of milestones
per project; model and effort per level in the recipe; recipes editable from the page; search in the
project list; a better levels panel on the phone; levels in the grid of the terminal `orq`.
