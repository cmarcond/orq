# claude-multiconta

A setup for using **several Claude Code accounts** (and Codex) on the same machine, each
one isolated, with aliases to open and log in to each account and a `usage-all` that shows
the usage of all of them with a pace marker. Made to be carried from machine to machine:
Mac (zsh) and Linux (bash).

## Commands at a glance

| command | what it does |
|---|---|
| `claude` | Claude Code on the default account (`default_account`, today Work), in a new tmux session `claude-<folder>` |
| `claude-work`, `claude-gmail`, … | opens Claude Code on that account, in a new tmux session (`claude-<folder>` for the default one, `claude-<account>-<folder>` for the others) |
| `claude-work-login`, `claude-gmail-login`, … | logs in to that account (prints a link, you paste the code) |
| `claude-usage-work`, `claude-usage-gmail`, … | usage of that account, with bar and pace |
| `claude-whoami` | which e-mail/plan is in each account and which of them is the plain `claude` |
| `codex-login` | Codex login by *device auth* (no local browser) |
| `codex-usage` | Codex usage (its own command; not part of `usage-all`) |
| `usage-all` | usage of all the Claude accounts, **brief**: one line per account and, per window, % used, a mini-bar with a pace marker and the time until the reset |
| `usage-all -v` | the same, **full**: a wide bar per window, points above/below the pace and the reset time |
| `usage-all gmail` | only the accounts named (names from `accounts.json`); combines with `-v` |
| `usage-hist` | **history**: per account, today, pace, 100% forecast, bars for the last 24 h and alerts; `dia`, `semana`, `calibrar`, `estado`; the collector runs every 5 min on node4 |
| `usage-sessoes` | **who is spending each account**, on all the machines: per account, the quota and one line per folder and type (the executions of a script together), with each one's share of the account's weight and the open processes idle for more than 1 h; `-v` shows each session with process, tmux, who launched it and prompt; `--horas N`, `--conta C`, `--json` |
| `TOTAL` (last line) | the week consolidated across the accounts: % free, the equivalent in whole accounts, pace, how long until everything runs out at the current pace and the next weekly reset; in `--json` it comes under the `_total` key |
| `orq` | **orchestrator**: picks up to 4 projects from any machine and opens a window with 4 panes, each one on the right ssh, tmux and account |
| `orq web` | shortcut for `orq-web` (the `orq-web` project): a local page with quota, tabs and chat with the agents |
| `claude-last` | lists, for the current folder, the most recent conversation of each account, newest first |
| `claude-resume` | resumes the most recent conversation of the current folder: on its own account if that one is in good shape, otherwise on the best account right now; `claude-resume 2` opens line 2 of `claude-last` |
| `usage-all --melhor` | prints the best account to open now (the quota that expires first); `-v` shows the order and the reason; it is what `claude` and `claude-resume` use |
| `claude-resume --as gmail` | resumes the same conversation **using another account** (e.g. the original one ran out of quota) |
| `tmux ls`, `tmux attach -t claude-<folder>` | go back to a Claude session (attach is manual) |
| `./add-account.sh name email "label"` | creates a new account and generates its three aliases |
| `./add-account.sh --remove name` | takes the account out of the aliases (without deleting anything) |
| `./install.sh` | installs or updates everything on this machine, downloading `claude` and `codex` if they are missing (`--no-install` skips that) |
| `./install.sh --uninstall` | removes scripts and aliases (the accounts stay) |
| `./make-tarball.sh` | packages the project to send to another machine |

The aliases of each account are generated from `accounts.json`, so a new account gets its
three aliases and joins `usage-all` automatically.

## How it works

### 1. One account per directory

Claude Code keeps login, history and configuration in the directory pointed to by the
`CLAUDE_CONFIG_DIR` variable. Without the variable, it uses `~/.claude`. The whole trick is
to give each account its own directory:

| account | directory | how the alias opens it |
|---|---|---|
| `work` | `~/.claude` (default) | `env -u CLAUDE_CONFIG_DIR claude` |
| `gmail` | `~/.claude-gmail` | `CLAUDE_CONFIG_DIR=~/.claude-gmail claude` |

Each terminal can be on a different account at the same time, and the login of one never
overwrites the other. Note that the default account is opened by **removing** the variable
instead of pointing it at `~/.claude`: if the variable points explicitly at `~/.claude`,
Claude Code looks for the credential under another name and shows up as logged out.

### 2. Plain `claude` and the tmux sessions

`claude` with nothing else opens **the best account right now**, chosen by `usage-all --melhor` (it uses first the quota that expires
before the reset; details below); with no readable quota, the default account (`default_account`, today `work`).
`CLAUDE_CONTA=gmail claude` forces an account, and `claude-<account>` always opens that one.
And both it and `claude-<account>` open Claude **in a new tmux session**, so you can close
the terminal, lose the connection, and later come back exactly where you were:

```sh
cd ~/Projects/foo
claude                  # tmux session "claude-foo" with the Work account
claude-gmail           # tmux session "claude-gmail-foo" with the Gmail account
claude                  # again in the same folder: "claude-foo-2"
tmux ls                 # lists the sessions
tmux attach -t claude-foo
```

- The name is `<prefix>-<current folder>`, with `-2`, `-3`… if it already exists. Odd
  characters become `-`. The default account uses the prefix `claude`, the others
  `claude-<account>`, so `tmux attach -t claude-<folder>` works the same as in the old scheme.
- Attach is manual, on purpose: `tmux ls` shows what is open.
- Already **inside** a tmux session, or on a machine without tmux, the command runs directly.
- Arguments pass through normally: `claude --resume`, `claude-gmail -p "question"`.
- Quick subcommands do **not** open tmux: `auth`, `mcp`, `config`, `doctor`, `update`, `install`,
  `plugin`, `agents`, `-p`/`--print`, `--version`, `--help`. So `claude auth status` answers
  right away, in the current terminal.

To find out which account plain `claude` is using, and what is in each directory:

```sh
claude-whoami
#   claude-work  ~/.claude          ✓ you@example.org  (team, Work)  ← plain `claude`
#   claude-gmail   ~/.claude-gmail   ✓ you@example.com  (max, ...)
claude auth status           # the same, only for plain `claude` (JSON with email and configDirectory)
```

Inside Claude Code, `/status` also shows the account.
- To turn tmux off on some machine, `"tmux": false` in `accounts.json` and `./install.sh`.
- `install.sh` also writes `"tui": "default"` to the `settings.json` of **each account**: it is
  the renderer that writes the conversation to the native scrollback, so tmux (`prefix + [`, PgUp,
  mouse wheel) sees the whole history. The other mode, `fullscreen`, keeps the conversation in a
  buffer of its own and it is gone after one screen. It is the same as typing `/tui default` on each account.
  For another value, `"tui": "fullscreen"` in `accounts.json`; for the installer to leave this alone,
  `"tui": false`. A session that is already open only changes after `claude` is restarted.

All the aliases call the binary through `env`, which resolves `claude` from the `PATH` and
ignores the function, so there is no recursion and no risk of `claude-gmail` falling back to the default account.

### 3. Login without a browser

`claude auth login` normally opens the default browser, and the browser authorizes the account
that is **already logged in** to claude.ai, whatever e-mail is pre-filled. That is how one
account already overwrote the other once.

That is why the login aliases pass `BROWSER=~/.local/bin/claude-login-url`, a script that
opens nothing: it only prints the URL. You open the URL wherever you want, logged in to the
right account (or in a private window), authorize, and paste the code that appears back into
the terminal. It works the same on a machine with a desktop and on a server over SSH.

```sh
claude-gmail-login
# >>> Open this URL logged in to the right account (or in a private window) and paste the code here:
#     https://claude.ai/oauth/authorize?...
# Paste code here if prompted > ...
```

Codex has the same flow natively: `codex-login` runs `codex login --device-auth`, which shows
a code for you to confirm on the site.

To check which account each directory is on: `claude-gmail auth status`.

**Onboarding is skipped on purpose.** `claude auth login` writes the credential, but does not mark
the first-run wizard (theme, tips, login) as completed. Without that mark, the first
`claude-<account>` opens the wizard, which ends in a **second** login through the browser, with a callback
on localhost. That login is redundant and breaks with `Redirect URI ... not supported`. That is why
`install.sh` writes `hasCompletedOnboarding: true` (and `theme: dark`, if there is no theme) to the
`.claude.json` of each account: `~/.claude.json` for the account in `~/.claude`, `<config_dir>/.claude.json`
for the others. The change is idempotent and does not touch anyone who already has the mark. If this error shows up on an
account, run `./install.sh` again.

### 4. Usage with a pace marker

`usage-all -v` queries Anthropic's usage API with the credential of each account and
draws, for each limit window, a bar of 20 cells, the points above (+) or below (−) the
pace and the reset time (only the hour when it is today):

```
→ Gmail  you@example.com
  session 5h   2% ░░░░░░░┃░░░░░░░░░░░░ -37  18:09
  week 7d     62% ████████████░░░░░░░┃ -34  20:59
  Fable 7d    82% ████████████████░░░┃ -14  20:59 ⚠
```

- The filled part (`█`) is the **usage** the API reports. The script does not estimate it.
- The `┃` is the **time**: the fraction of the window that has already passed. To "stay on pace", the filled
  bar should reach exactly up to it. Example: if in 30 min of a 5 h window you
  spent 50%, 10% of the time has passed, the `┃` sits at 10% and the bar (50%) goes far past it.
- The number next to it is the deviation, in the color of the bar: green below the pace, yellow above, red at
  15 points or more above. `⚠` marks a window the API says is close to the limit.
- How the time is computed: for Claude the API only gives the reset time, so the script assumes
  fixed windows of 5 h and 7 days and counts backwards. If the window is not active (the reset has already passed)
  the marker disappears.
- The colors only come out when the output is a terminal; in a pipe or a file it is plain text.

With no argument, `usage-all` goes through **all** the accounts in `accounts.json`; with one or more
names, it shows only those. The full format (`-v`) is the one with the bars above; the default is the brief one
described next. `--json` is the output that `orq` and `orq-web` consume. Codex is another
service and has its own command, `codex-usage` (`bin/codex-usage-fast`), with the same modes.

```
usage-all                 # all the Claude accounts, brief
usage-all work         # only that account (you can list several)
usage-all -v              # full: wide bar, deviation from the pace and reset time
usage-all -v gmail        # combines the two
usage-all --json          # for scripts
```

The brief format is the default (`-v`/`--verbose` gives the full one). In the brief format,
each account becomes one table row. Per window: the % used, a mini-bar of 10 cells
(each cell = 10%) with the same `┃` pace marker as the full mode and, on the right, the time
until the reset. Only the names of the windows are printed above the table (the legend is in `--help`); the arrow
marks the best account to open now (the one from `usage-all --melhor`) and the TOTAL sums up the fleet's week.

```
                   session 5h              week 7d               Fable 7d
  Work      5% ░░░░░░░░┃░ 00h35   42% ████░░░░┃░ 1d04h   65% ██████░░┃░ 1d04h
→ Gmail        0% ░░┃░░░░░░░ 03h45   62% ██████░░░┃ 06h35   82% ████████░┃ 06h35
  Work 3    0% ░┃░░░░░░░░ 04h05   19% ██░░░┃░░░░ 3d07h   35% ████░┃░░░░ 3d07h
  Work 2    2% ┃░░░░░░░░░ 04h35    5% ░┃░░░░░░░░ 5d20h    0% ░┃░░░░░░░░ 5d20h

TOTAL  32% ███░░░┃░░░  burn 0,5×  runs out ∞  reset Gmail 06h21
```

The `┃` is the **time**: how much of the window has already passed. A bar that does not reach the `┃` is below the
pace (green); a bar that goes past it is above (yellow; red at 15 points or more of
difference). The % and the bar have the same color. When the `┃` falls inside the filled part, it
becomes a notch in the color of the bar, as in `███┃░░░░░░` (37% used with 34% of the session elapsed:
on pace). A window that has not started yet appears with the `┃` in the first cell and the full
duration in gray, because the clock only starts on the first use.

The Claude accounts come out sorted by the **slack in the weekly window** (`week 7d`, all models):
the difference between what was used and the pace. Whoever is furthest below the pace comes first; whoever is
furthest above, last. In the example, Work 3 spent only 8% of the week, but with 6% of it
elapsed it is above the pace, so it comes after Work (19% with 37% elapsed).
An account with an error goes to the end.

`session 5h` is the 5 h window, `week 7d` the one of 7 consecutive days starting from the first use
(which is why each account resets on a different day and time); a model name (e.g. `Fable 7d`)
is the weekly limit for that model only.

### 4b. Resuming the right conversation with several accounts

Each account keeps its history in its own directory (`<config_dir>/projects/<folder>/`), so
the `--resume` of one account does not see the conversations of the others. `claude-last` looks at all the
accounts and shows, for the current folder, the most recent conversation of each one:

```
$ claude-last -a ~
~   (claude-resume N opens conversation N)
 1  Work      20h  MacBook wifi problems
 2  Work      20h  usage-all command with formatting
 3  Work       1d  Dropbox log through the API
    = claude-work --resume 00000000-0000-0000-0000-000000000000
```

`claude-resume` opens line 1 **on its own account, if that one is in good shape** (5 h window below 70% and 15+ points
left in the week); otherwise, on the best account right now (`usage-all --melhor`), copying the conversation the way `--as` does.
It prints nothing. `claude-resume 2` opens line 2, and extra
arguments go to `claude`. With `-a`, both commands use the 15 most recent conversations of the
folder, from any account. Empty conversations and the token-renewal ones are left out.

**Resuming with another account.** When the conversation's account has run out of quota, `claude-resume --as gmail`
(or `--as gmail 2`, `--as gmail -a 3`) copies the conversation to the `gmail` account and opens it there. The
conversation is just a local file, so the other account continues from where it stopped. The conversation's
attachments go along, and so do the project's memory files that the destination account does not have yet. The original
stays intact on the source account; if you continue on both, they become separate branches. If the destination
account already had a different copy of this conversation, it is kept as `<id>.jsonl.bak-<date>`.
The first message on the new account costs more: it rereads the whole conversation, without the other one's cache.

### 4c. Orchestrator: 4 projects at once (`orq`)

```
orq            # picks the projects and opens the grid (if it already exists, just goes back to it)
orq --new      # closes the grid and picks again (the remote agents keep running)
orq --list     # only shows the list
```

`orq` asks all the machines in `orq_hosts` (in `accounts.json`) and the laptop which
folders have recent conversations, and shows a single list. You type up to 4 numbers, or
`machine:/path` for a new folder. Conversations from `claude -p` (scripts and cron)
and the ones in `/tmp` are left out.

```
  #  machine    folder                             account     ago  subject
  1  laptop     ~/Projects/claude-multiconta       work    0min  Usage-all account and brief parameters
  2  server0  ~/Projects/cluster    work    0min  HANDOVER-2026-10-04
  3  node0    ~/acervo                           work3   8min  USB disk copy to the NAS
  4  server0  ~/Projects/myproject               gmail        1d  Project update
```

**Account.** The projects are spread over the accounts with the most slack in the 7d window (used − max). Each agent
already running on an account, including the ones from earlier grids, weighs 20 points against it; staying on the conversation's
account has a 10-point preference, so conversations are not copied for nothing. An account with no quota (5h or 7d
at 95%) is never chosen. Before opening, `orq` shows the account of each project and the reason. If the
account changes, the conversation goes along (the same `claude-resume --as`).

**Grid.** A tmux window of its own (server `tmux -L orq`, separate from your normal tmux) with up to 4
panes. The border of each pane shows project, machine, account and the current subject. A click or
`Ctrl-b` + arrow changes pane, `Ctrl-b z` zooms, `Ctrl-b d` leaves without killing anything.

**Agents that survive.** On the remote machines each project runs in a tmux session
`orq-<folder>` over there. Closing the laptop or losing the ssh does not kill the agent: the pane offers
to reconnect, and `orq` goes back to the same session. In the remote tmux the prefix is `Ctrl-a` (rarely
needed) and scrolling is with the mouse wheel. A machine without tmux (node1) runs directly, without this
protection.

**Alerts.** When an agent finishes or asks for permission, an iTerm2 notification goes out with the name
of the project. It goes through the terminal itself (OSC 9 crossing both tmux), with no external service.
The hooks go only into the `orq` agents, through `claude --settings`, without touching the `settings.json`
of the accounts. Run `orq` directly in iTerm2, not inside another tmux, otherwise the alert does not arrive.

**Web page (`orq web`).** The chat with the agents became a project of its own,
[`orq-web`](../orq-web) (`~/Projects/orq-web`): a Python package with tests and CI, two commands:

- **`orq-web`**, on the laptop: a local page (`127.0.0.1` only, token in the link) with the accounts' quota
  at the top, one tab per agent from any machine and a chat in the style of Remote Control:
  Markdown, collapsible tools, permissions by button and a form for `AskUserQuestion`.
  `orq web` here is just a shortcut for it.
- **`orq-agent`**, on each machine: the supervisor that runs `claude -p` in stream-json in the right folder and
  account, behind a Unix socket, inside a tmux session `orqs-<project>`. It is what the
  page opens on the machines; it survives the ssh dropping and the conversation is a normal Claude Code session.

Installation: `cd ~/Projects/orq-web && ./install.sh` on this machine, or `./deploy.sh` for all
the ones in `orq_hosts` (it copies the package, no pip). The `permission_mode` in `accounts.json` applies to
the page's agents. Details in the orq-web README.

**Quota in the middle of the work.** When you leave claude in a pane (`/exit`), `orq-run` checks the account:
if it has run out, it resumes the conversation on the account with the most slack in 5 s. Otherwise, it shows
`[Enter] resume  [c] another account  [n] new conversation  [q] close`.

### 5. Where each system keeps the credential (the script finds out by itself)

- **macOS**: in the Keychain. For `~/.claude` the item is called `Claude Code-credentials`; for
  any other directory, `Claude Code-credentials-` followed by the first 8 characters of the
  SHA-256 of the absolute path. The script computes that suffix, so it works with any
  user and any directory.
- **Linux**: in the file `$CLAUDE_CONFIG_DIR/.credentials.json`.
- **Codex**: `~/.codex/auth.json` on both systems.

The tokens are never printed. The access token expires every ~8 h; when that happens
`usage-all` asks Claude Code itself to renew it, firing a minimal request
(`claude -p "ok" --model haiku`, negligible cost) on that account, and carries on. This has nothing to do with the subscription: "token expired" never means the plan
has lapsed. Only if the renewal fails does it ask for `claude-<account>-login`.

### 6. Aliases generated, not written by hand

`install.sh` reads `accounts.json` and writes `~/.config/claude-multiconta/aliases.sh` with
all the aliases, plus the line that makes sure `~/.local/bin` is on the `PATH` and the hint shown when
the terminal opens. A single `source` line goes into `.zshrc` (Mac) or `.bashrc` (Linux), marked
with `# >>> claude-multiconta >>>` so the installer recognizes it and does not duplicate it. In zsh with
Powerlevel10k the line goes **before** the *instant prompt* block, because the hint is printed
at startup and p10k complains about output after that block.

Running `./install.sh` again is safe: it overwrites the scripts, regenerates the aliases and leaves
`.zshrc`/`.bashrc` as it is.

### 7. Adding an account

```sh
cd ~/Projects/claude-multiconta        # or the folder where the tarball was extracted
./add-account.sh account3 someone@example.com "Someone (example.com)"
# in a new terminal (or: . ~/.config/claude-multiconta/aliases.sh):
claude-account3-login    # opens a link, paste the code
claude-account3          # use it
claude-usage-account3    # usage of this account
usage-all                # already includes the new account
```

The command does three things: it registers the account in `accounts.json` with `config_dir` equal to
`~/.claude-account3`, creates that directory and runs `install.sh`, which regenerates the aliases. Since
`usage-all` goes through the JSON, it starts including the account with no editing at all.

Rules: the name can only have `[a-z0-9_-]`; a repeated name or directory is refused. The third
argument is the label that appears in the usage header; if omitted, it becomes `name (email)`.

To take an account out of the aliases, keeping the directory and the login:

```sh
./add-account.sh --remove account3
```

Once that is done on one machine, run `./make-tarball.sh` and reinstall on the others so that `accounts.json`
is the same on all of them. Each machine does its own login for the new account.

### 8. Taking it to another machine

```sh
./make-tarball.sh                                   # generates ~/Projects/claude-multiconta-YYYYMMDD.tar.gz
scp ~/Projects/claude-multiconta-*.tar.gz user@machine:
ssh user@machine
tar xzf claude-multiconta-*.tar.gz && ./claude-multiconta/install.sh
exec bash                                           # or open a new terminal
claude-work-login ; claude-gmail-login ; codex-login
usage-all
```

If the machine's `.bashrc` already had its own `claude()` function (the old tmux scheme),
`install.sh` warns you: remove that block, otherwise the two definitions fight.

`install.sh` **installs `claude` and `codex` if they do not exist**: Claude through the official
script (`claude.ai/install.sh`, it goes to `~/.local/bin`), Codex through `npm` if there is one, otherwise
by downloading the official binary from GitHub (~100 MB) to `~/.local/bin/codex`. To skip this step,
`./install.sh --no-install`. The only real prerequisites are `python3` (3.9 or later to
show times in São Paulo; with 3.8 it uses the machine's local time zone) and `curl`.

The tarball carries scripts, aliases and the list of accounts, **never credentials**: each machine does
its own login.

## Project files

| file | what it is |
|---|---|
| `accounts.json` | single source of truth: name, e-mail, label and `config_dir` of each account; also `default_account` (what plain `claude` opens), `tmux`, `tui`, `permission_mode` (the page's agents), the time zone, `orq_hosts` and `codex` (install Codex and create `codex-login`/`codex-usage`) |
| `install.sh` | installs `claude`/`codex` if they are missing, copies `bin/*` to `~/.local/bin`, generates `~/.config/claude-multiconta/aliases.sh` and adds the source line to `.zshrc` or `.bashrc` |
| `add-account.sh` | adds/removes an account in `accounts.json`, creates the directory and reruns `install.sh` |
| `bin/usage-all` | Claude usage per account, straight from the OAuth API: brief, `-v` with bars and `--json` |
| `bin/codex-usage-fast` | the same for Codex (alias `codex-usage`); separate from `usage-all` |
| `bin/usage-hist` | usage history in an SQLite database (collector every 5 min by a systemd timer), with dashboard, day, week, tokens → points calibration and alerts; `usage-all` and `usage-sessoes` read from it |
| `bin/usage-sessoes` | sessions and consumption per account on all the machines: reads the conversations in `~/.claude*/projects/` and the live processes, over ssh (the script itself goes through stdin, it does not need to be installed there) |
| `bin/orq` | orchestrator: lists projects from all the machines and builds the tmux grid; `orq web` calls `orq-web` |
| `bin/orq-run` | runs in each pane: opens claude on the right account, switches account when the quota runs out |
| `bin/orq-hook` | hook of the orq agents: iTerm2 notification when they stop |
| `bin/claude-last` | most recent conversation of each account in a folder; the basis of the `claude-resume` function |
| `bin/claude-login-url` | used as `$BROWSER` at login: opens no browser, only prints the URL |
| `bin/claude-whoami` | runs `claude auth status` on each account and shows e-mail, plan and which one is the plain `claude` |
| `make-tarball.sh` | packages everything into `~/Projects/claude-multiconta-YYYYMMDD.tar.gz` |

## Uninstalling

```sh
./install.sh --uninstall     # removes scripts, aliases and the source line; the account directories stay
```
