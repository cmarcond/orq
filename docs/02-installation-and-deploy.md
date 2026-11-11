# 2. Installation and deploy

## Prerequisites

| where | what |
|---|---|
| every machine | `python3` (3.8 or later; 3.9+ for times in the São Paulo time zone), `curl`, `ssh`; `tmux` recommended (without it the agents run with `nohup`, with no reconnection) |
| laptop | key-based ssh to every machine in `orq_hosts`, using the names in `~/.ssh/config`; `tmux`; iTerm2 for the alerts of the terminal `orq` |
| server0 (page server) | Docker with the compose plugin, a ZeroTier interface, a `~/.ssh/id_ed25519` key that opens the other machines and the laptop |
| a Mac that receives agents over ssh | `sshd` enabled, `~/.zshenv` with `~/.local/bin` and `/opt/homebrew/bin` in the `PATH`, a tmux server born in the graphical session (see below) |

`claude` and `codex` are installed by claude-multiconta's `install.sh` if they are missing.

## Order on a new machine

```sh
# 1. accounts and utilities
./claude-multiconta/install.sh            # installs claude/codex if missing, copies bin/*, generates the aliases
exec zsh                                  # or open a new terminal
claude-work-login; claude-gmail-login  # one login per account, pasting the code (no browser)
usage-all                                 # check: one line per account + TOTAL

# 2. the supervisor and the page
./orq-web/install.sh                      # ~/.local/lib/orq-web + the commands orq-agent, orq-web, converte-web
```

For the other machines, from the laptop:

```sh
./claude-multiconta/make-tarball.sh       # ~/Projects/claude-multiconta-YYYYMMDD.tar.gz (no credentials)
scp ~/Projects/claude-multiconta-*.tar.gz server0:
ssh server0 'tar xzf claude-multiconta-*.tar.gz && ./claude-multiconta/install.sh --no-install'
./orq-web/deploy.sh                       # laptop + all of orq_hosts; or: ./orq-web/deploy.sh server0 node3
```

`deploy.sh` packs `orqweb/` and `install.sh`, copies them with `scp` and runs `install.sh` there. A
machine that is down shows up as `✗ falhou` ("failed"); repeat when it comes back. Both installers are
idempotent.

**What has to be on each machine, and why**

| item | laptop | remote machines | container |
|---|---|---|---|
| `claude` logged in to the accounts | yes | yes (one login per machine) | no |
| `claude-multiconta` (`usage-all`, `claude-last`, `orq`) | yes | yes: the server lists projects and agents through them, and reads the quota on `ORQ_USAGE_HOST` | no |
| `orq-agent` | yes (the container opens agents here) | yes | no |
| `orq-web` (server) | installed, but not kept running | no | yes (image) |

Changing `usage-all` (e.g. the TOTAL line) requires reinstalling claude-multiconta on the machine that
answers the quota to the server (`ORQ_USAGE_HOST`, currently server0). Changing `orq-agent` (e.g.
`ls`, `cat`, `link`) requires `deploy.sh` on every machine, otherwise the page warns "orq-agent antigo"
("old orq-agent").

## Accounts

A new account:

```sh
cd claude-multiconta
./add-account.sh conta3 fulano@exemplo.com "Fulano (exemplo.com)"
# in a new terminal:
claude-conta3-login
```

This registers the account in `accounts.json` with `config_dir: ~/.claude-conta3`, creates the
directory and regenerates the aliases. `usage-all`, `orq` and the page start taking it into account.
After that, `make-tarball.sh` and a reinstall on the other machines, so that `accounts.json` is the
same on all of them; each machine does its own login. To remove it from the aliases, without deleting
anything: `./add-account.sh --remove conta3`.

The login never opens a browser: the alias passes `BROWSER=claude-login-url`, which only prints the
URL. Open it while logged in to the right account (or in a private window) and paste the code. The
reason is in chapter 3.

## The page over the network: a container on server0

```sh
./orq-web/deploy/push-server0.sh        # from the laptop
```

The script sends the code over ssh (a tarball through stdin, in a single connection) and runs
`deploy/server0/deploy.sh` there, which:

1. generates the password in `~/.config/orq-web/password` if there is none (`openssl rand`), mode 600;
2. makes sure the machine can ssh into itself (the key in `authorized_keys`), because inside the
   container server0 itself is "remote";
3. writes `~/.config/orq-web/ssh_config`, one `Host` per machine in `orq_hosts` plus `laptop` and the
   host itself, with the IPs resolved there (the container sees neither the host's `/etc/hosts` nor
   its `~/.ssh/config`), `StrictHostKeyChecking accept-new` and its own `known_hosts`; whatever the
   host's `~/.ssh/config` changes for a machine and the container also needs goes along: `ProxyJump`
   and a specific `KexAlgorithms` list (for example, to work around a machine that can only be reached
   through another one);
4. brings up `docker compose`. The image is named `ghcr.io/cmarcond/orq-web:latest`, and the script
   first tries `docker compose pull`; that only succeeds if an image exists under that name in GHCR
   and Docker on server0 can read it. No workflow in this repository pushes one (CI only builds and
   smoke-tests the image), so in practice the pull fails and the script builds the image there, from
   the code that was sent (`docker compose up -d --build`). `ORQ_BUILD_LOCAL=1` skips the pull attempt
   and always builds from the code that was sent;
5. publishes port 8765 only on the ZeroTier IP.

The image is `python:3.12-slim` with the ssh client; the user inside it has the same uid and home as
the host user, in order to mount `~/.ssh` (read-only), `~/.config/claude-multiconta` (read-only) and
`~/.config/orq-web` (password, `ssh_config`, state). Variables fixed in the image: `ORQ_BIND=0.0.0.0`,
`ORQ_LOCAL_HOST=none` (no local agents; everything over ssh), `ORQ_STATE_DIR`, `ORQ_PASSWORD_FILE`,
`ORQ_SSH_CONFIG`, `CLAUDE_MULTICONTA_CONF`. From compose: `ORQ_ALLOWED_HOSTS` (the ZeroTier IP and the
hostname), `ORQ_EXTRA_HOSTS=laptop`, `ORQ_USAGE_HOST=<hostname>`. A `HEALTHCHECK` requests `/login`
every 30 s.

To get in: `http://<zerotier ip>:8765/`, password `ssh server0 cat ~/.config/orq-web/password`. The
sessions are kept in the server's memory: a session ends when the server restarts, on logout, or
after 30 days without use (each authenticated request renews it; the cookie itself has `Max-Age` of
30 days).

To update: run `push-server0.sh` again; it sends the current code, rebuilds the image on server0 and
recreates the container (use `ORQ_BUILD_LOCAL=1` to be sure the build comes from the code sent and
not from an image pulled from a registry). `docker ps` on server0 shows
`healthy` in ~30 s.

**Large packets that vanish on ZeroTier.** If ssh to a machine hangs right at the key exchange (the
post-quantum one, `mlkem768x25519`, sends large packets) or responses of more than ~1 KB never arrive,
look at the MTU of the network interface at each end. node4 is a VM whose `ens3` has an MTU of 1442,
smaller than the 1460 bytes of the UDP packets ZeroTier sends by default; on the direct path to node3
(at home, another network) the fragments were being lost. The fix is to make ZeroTier send smaller
packets, at both ends, in `/var/lib/zerotier-one/local.conf` followed by
`sudo systemctl restart zerotier-one`:

```json
{ "physical": { "0.0.0.0/0": { "mtu": 1400 } } }
```

On node4 it applies to all destinations; on node3 the network on the other side is enough
(`"198.51.100.0/24"`). `sudo zerotier-cli -j peers` shows whether the path to a node is direct or
through a relay.

## A Mac as a remote machine

Claude Code keeps the credential in the Keychain, which does not open for ssh sessions. A Mac that
receives agents over ssh needs a tmux server born in the graphical session, so that the processes
inherit the unlocked Keychain:

```sh
./orq-web/deploy/macos-tmux-keep.sh       # user LaunchAgent that keeps a `keep` session alive from login
```

The agents join that server, and the remote quota is read with `tmux run-shell "usage-all --json"`.
You also need `sudo systemsetup -setremotelogin on` and a `~/.zshenv` with the `PATH` (ssh sessions do
not read `.zshrc`). `--remove` undoes it.

## Developing from server0

Since 2026-10-13 the development environment lives on server0, not on the laptop:

```sh
ssh server0
cd ~/Projects/claude-orq            # cloned over https with gh's credential (`gh auth setup-git`)
claude-work3                     # or another account: opens Claude Code in a tmux session claude-work3-claude-orq
cd orq-web && make test && make lint   # venv in .venv (uv, Python 3.12); the system's node 22
.venv/bin/python -m pytest qa/tests/test_smoke.py   # QA right here: Playwright 1.48's Chromium
```

- The `~/.bashrc` there exports `ORQ_LOCAL_HOST=server0`, `ORQ_EXTRA_HOSTS=laptop`,
  `ORQ_QA_PROJECT=server0:~/Projects/orq-qa` and `ORQ_QA_PROJECT2`: a local `orq-web`, or the QA one,
  on server0 treats its own machine as local and reaches the laptop over ssh (`ssh laptop` works from
  there).
- `orq-web/deploy.sh` with no arguments installs locally on the current machine and, on the others,
  over ssh; `laptop` is only local on the Mac. `deploy/push-server0.sh` run on server0 itself does an
  ssh into itself, which the container deploy has already authorized.
- The newest Playwright ships no browser for Ubuntu 20.04: the venv on server0 uses
  `playwright==1.48.0`
  (`uv pip install --python .venv/bin/python playwright==1.48.0 && .venv/bin/python -m playwright install --with-deps chromium`).
  In that version `page.wait_for_function` is only usable when the predicate is already true
  straight away: to poll, it uses `eval` inside the page and the CSP (no `unsafe-eval`) blocks it.
  That is why the harness (`qa/orqqa.py`) waits with `Orq.wait_fn`, which evaluates through CDP every
  300 ms; the QA tests use only that. To run the QA here:
  `ORQ_LOCAL_HOST=server0 ORQ_EXTRA_HOSTS=laptop ORQ_QA_PROJECT=server0:~/Projects/orq-qa
  ORQ_QA_PROJECT2=server0:~/Projects/orq-qa2 .venv/bin/python -m pytest qa/tests/test_conjuntos.py`.
- The QA leaves nothing behind: before and after each test, `Orq.purge` (in `qa/orqqa.py`) undoes the
  levels, stops the agents of the test folder (the tabs close) and deletes, on the agent's machine,
  the folder, the supervisors' state in `~/.cache/orq/<name>-*` and claude's conversations in
  `~/.claude*/projects/`. It only accepts folders named `orq-qa…` or `orq-demo…`, so that a wrong
  `ORQ_QA_PROJECT` never deletes a real project. By hand:
  `.venv/bin/python qa/orqqa.py purge server0:~/Projects/orq-qa`.
- This project's Claude Code memory was copied to each account on server0
  (`~/.claude*/projects/-home-user-Projects-claude-orq/memory/`). The clone on the laptop was left
  there, but is no longer used.
- An alternative without a terminal: open `server0:~/Projects/claude-orq` with the **+** on the page
  itself and develop through the chat, with the agent running on server0.

## Server environment variables

| variable | default | effect |
|---|---|---|
| `ORQ_BIND` | `127.0.0.1` | listening address; with anything other than `127.0.0.1`, `localhost` or `::1` the server refuses to start unless the password file exists and is not empty |
| `ORQ_PASSWORD_FILE` | `~/.config/orq-web/password` | login password; without the file, only the token in the link |
| `ORQ_ALLOWED_HOSTS` | empty | extra accepted `Host` headers (comma-separated), in addition to `127.0.0.1`, `localhost` and the `ORQ_BIND` address, which are always accepted |
| `ORQ_LOCAL_HOST` | `laptop` | this machine's name on the page; `none` = no local agents (container) |
| `ORQ_EXTRA_HOSTS` | empty | machines in addition to `orq_hosts` (the container adds `laptop`) |
| `ORQ_SSH_CONFIG` | empty | its own `ssh -F` (the container) |
| `ORQ_USAGE_HOST` | empty | machine that answers `usage-all --json` over ssh; empty = this one |
| `ORQ_STATE_DIR` | `~/.cache/orq` | state of the agents and of the links |
| `ORQ_SETS_DIR` | `~/.config/orq-web/conjuntos` | the sets (⊞) saved from the page |
| `ORQ_BIN` | `~/.local/bin` | where `usage-all`, `claude-last`, `orq`, `orq-agent` are |
| `CLAUDE_MULTICONTA_CONF` | `~/.config/claude-multiconta/accounts.json` | the accounts |
| `ORQ_NO_OPEN` | empty | `1` does not open the browser on startup |

## CI/CD

One workflow per sub-project, triggered only by changes in its folder:

- **claude-multiconta**: syntax of all the scripts (`py_compile` or `sh -n`), a valid `accounts.json`,
  pytest over the whole `claude-multiconta/tests` directory (alias generation, `claude-last`,
  `usage-all`, `usage-hist`, `usage-sessoes`), on Python 3.8 and 3.12.
- **orq-web**: `ruff check`, `ruff format --check` and pytest on 3.8, 3.11 and 3.12; `node --test` on
  Node 22; then it builds the image, runs a smoke test (starts the container with an empty
  `accounts.json` and a password file, requests `/login` and checks that `/api/state` without
  credentials returns 403). The workflow has no publish step: the image is not pushed to any
  registry, and the deploy script builds it on the target machine.
- **claude-highlevel**: fast pytest, since it is a historical reference.

The supervisor's code runs on the remote machines, some with an old Python: that is why everything in
`orqweb/` stays compatible with 3.8 (CI enforces it).
