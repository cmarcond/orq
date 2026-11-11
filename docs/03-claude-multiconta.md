# 3. claude-multiconta: accounts, quota and the terminal orchestrator

claude-multiconta is the foundation for everything else: it is what knows which accounts exist, where
each one keeps its credential, how much quota is left and which conversation to resume. Shell + Python
3.8, no dependencies. Its `README.md` has the table of commands; this chapter explains the mechanisms.

## 3.1 One account per directory

Claude Code keeps login, history and configuration in the directory pointed to by `CLAUDE_CONFIG_DIR`;
without the variable, in `~/.claude`. Each account gets a directory:

| account | directory | how the alias opens it |
|---|---|---|
| `work` (default) | `~/.claude` | `env -u CLAUDE_CONFIG_DIR claude` |
| `gmail` | `~/.claude-gmail` | `env CLAUDE_CONFIG_DIR=~/.claude-gmail claude` |
| `work2`, `work3` | `~/.claude-work2`, `~/.claude-work3` | same |

Two details that were hard to find out:

- The default account is opened by **removing** the variable. If it points explicitly to `~/.claude`,
  Claude Code looks for the credential under another name and appears logged out.
- The normal `claude auth login` opens the browser, and the browser authorizes whichever account is
  **already logged in** to claude.ai, whatever the pre-filled e-mail is. That is how one account
  overwrote another once. That is why the login passes `BROWSER=claude-login-url`, which only prints
  the URL.

`install.sh` also writes, per account, `hasCompletedOnboarding: true` in `.claude.json` (without it
the first use opens a wizard that ends in a second login, with a callback on localhost, which fails)
and `"tui": "default"` in `settings.json` (the renderer that writes to the native scrollback, so that
tmux sees the whole history; `fullscreen` keeps the conversation in its own buffer and it disappears).

## 3.2 Generated aliases and tmux sessions

`install.sh` reads `accounts.json` and writes `~/.config/claude-multiconta/aliases.sh`; a single
`source` line goes into `.zshrc` or `.bashrc`, marked with `# >>> claude-multiconta >>>`. For each
account: `claude-<account>`, `claude-<account>-login`, `claude-usage-<account>`. Plain `claude` opens
**the best account right now** (below); if the quota cannot be read, the `default_account`.

**The best account (`usage-all --melhor`)**: plain `claude` and `claude-resume` pick the account by
themselves, without printing anything. The rule is "use first what is about to expire": the quota left
on an account close to its weekly reset is gone in hours, while that of an account that only resets
days from now stays valid; so the ordering is what is left of the week divided by the hours until the
reset (points that expire per hour). Accounts with fewer than 5 points left in the week or with the
5-hour window above 85% are left out; if none qualifies, the one with the most left is used (with a
warning). `usage-all --melhor -v` shows the ordering and the reason; the quota comes from the
`usage-hist` database (or from the API, if the database is stale). `CLAUDE_CONTA=gmail claude` forces
an account; `claude-<account>` still opens that account.

An interactive session opens in a **new** tmux called `<prefix>-<folder>` (`claude-foo` for the
default one, `claude-gmail-foo` for the others; `-2`, `-3` if it repeats), born with `cd ~` before
`new-session` so that the tmux server does not inherit a folder that may be deleted later. Attaching
is manual (`tmux ls`, `tmux attach -t claude-foo`). It runs directly, without tmux, when it is already
inside a tmux, when there is no tmux, or for quick subcommands (`auth`, `mcp`, `config`, `doctor`,
`update`, `-p`, `--version`…). `"tmux": false` in `accounts.json` turns it off.

`claude-whoami` runs `claude auth status` in each directory and shows the e-mail, the plan and which
one is plain `claude`.

## 3.3 Where the credential is

- **macOS**: Keychain. For `~/.claude` the item is `Claude Code-credentials`; for another directory,
  `Claude Code-credentials-` + the first 8 characters of the SHA-256 of the absolute path. `usage-all`
  computes the suffix. ssh sessions cannot see the Keychain, hence the LaunchAgent of chapter 2.
- **Linux**: `<config_dir>/.credentials.json`.
- **Codex**: `~/.codex/auth.json`.

The access token expires every ~8 h. When `usage-all` finds an expired one, it asks Claude Code itself
to renew it with a minimal request (`claude -p "ok" --model haiku`) on that account. "Token expired"
never means "plan expired". Tokens are never printed.

## 3.4 `usage-all`: quota with a pace marker

It queries `https://api.anthropic.com/api/oauth/usage` with each account's credential (one call per
account, ~1 s). The API returns, per window, the percentage used and the reset time; the script **does
not estimate** usage, it only draws.

```
                   sessão 5h              semana 7d              Fable 7d
  Work      5% ░░░░░░░░┃░ 00h35   42% ████░░░░┃░ 1d04h   65% ██████░░┃░ 1d04h
→ Gmail        0% ░░┃░░░░░░░ 03h45   62% ██████░░░┃ 06h35   82% ████████░┃ 06h35
  Work 3    0% ░┃░░░░░░░░ 04h05   19% ██░░░┃░░░░ 3d07h   35% ████░┃░░░░ 3d07h

TOTAL  32% ███░░░┃░░░  queima 0,5×  zera ∞  reset Gmail 06h21
```

There is no legend (it is in `--help`): `█` is what was used, `┃` the pace, and on the right the time
until the reset. The column headers are the 5-hour session (`sessão 5h`), the 7-day week
(`semana 7d`) and the 7-day window of one model. The arrow marks the best account to open right now,
the same one as `usage-all --melhor` (what plain `claude` opens). The source of the data only appears
when the database collection is 15 min old or more.

- **Windows**: `session` (5 h), `weekly_all` (7 days, all models) and `weekly_scoped` (7 days of one
  model). The size is fixed; the pace is `1 - (reset - now) / size`. A window whose reset has already
  passed has no pace; a window whose reset is further away than its size has not started yet (the
  clock starts on first use) and appears with the `┃` in the first cell.
- **Colours**: a usage − pace difference smaller than 3 points or negative is green; up to 15, yellow;
  beyond that, red.
- **Order**: accounts from the one with the most slack to the tightest in `semana 7d` (usage − pace);
  errors go to the end.
- **TOTAL** (last line): the fleet's week. Average usage and average pace in the same mini-bar; the
  **burn** (`queima`), the consumption speed: consumption divided by the fraction of the week elapsed,
  summed over the accounts; `1,0×` is exactly on pace, less is slack, more is tight (green below 0.9,
  yellow up to 1.15, red beyond); **when it runs out** (`zera`): a simulation that spends what is left
  of all accounts at the current pace and refills each account at its reset (`∞` when the resets come
  first); and the next weekly reset, and of which account. The idea comes from SLO *error budgets*:
  burn and time to exhaustion.
- Modes: `usage-all` (summary), `-v` (per account, 20-cell bars, deviation from pace and reset time;
  at the end the TOTAL, the best account and the age of the data), `usage-all gmail` (only the ones
  named), `--json` (for `orq` and the page: per account `5h`, `5h_pace`, `5h_left`, `7d`, `7d_pace`,
  `7d_left` or `{"err": …}`, plus `_total` with `7d`, `7d_pace`, `7d_free`, `7d_burn`, `7d_runs_out`,
  `7d_next_reset`, `accounts`).
- The API returns 429 if queried often; `orq` and the page query every 150 s.

Codex has its own command, `codex-usage` (`bin/codex-usage-fast`), with the same modes; it is not part
of `usage-all`.

### `usage-sessoes`: who is spending each account

`usage-all` says how much each account has spent; the quota API does not say who spent it (only
percentages per account and window). `usage-sessoes` answers that by looking at the machines: on each
one (the laptop and the `orq_hosts`, in parallel, one ssh connection per machine, with the script
itself going through stdin) it reads

- the **live claude processes**: the account (from the process's `CLAUDE_CONFIG_DIR`), the folder, the
  type (`interativo` for interactive, `orq` for the page's agents, `script` for a `claude -p` launched
  by a program, `daemon` for `claude daemon` and its reserves), the tmux session, who launched it and
  how long it has been alive;
- the **consumption of each session** in the last 5 hours (`--horas N`), from the conversations Claude
  Code writes to `~/.claude*/projects/`: responses, output tokens, new and read cache, models and, for
  sessions without a terminal, the start of the first message (that is what identifies a script).
  Subagents are added to the parent session.

The default is the **summary**: a table with one block per account. The first line of the block
carries the quota from `usage-all` (5h · 7d); each line is a machine and folder, with the time span,
the share of the account's consumption and the ≈pts (points of the week estimated from the tokens).
`●` before the folder: it responded in the last 10 min. The type only appears when it is not
interactive (`· script 2×`, `· orq`), with the runs of the same script grouped. Live processes with no
use for more than 1 hour (the forgotten ones) are listed as `parado` ("idle"). The daemon and helpers
do not appear. There is no footer: the legend is in `--help`, and the age of the data only appears
when it is over 15 min.

```
últimas 5 h · sem resposta: node1, node2
┌───────────┬─────────────┬─────────┬────────────────────────┬─────────────┬───────────────┬──────┐
│ conta     │ 5h · 7d     │ máquina │ pasta                  │ horário     │ parte         │ ≈pts │
├───────────┼─────────────┼─────────┼────────────────────────┼─────────────┼───────────────┼──────┤
│ Work 2 │   9% ·   6% │ node4 │ ● cluster │ 10:19–15:07 │ ████████ 100% │ ≈5,2 │
├───────────┼─────────────┼─────────┼────────────────────────┼─────────────┼───────────────┼──────┤
│ Gmail     │   2% ·  62% │ node0 │   acervo               │ 10:13–11:54 │ ███░░░░░  36% │ ≈2,9 │
│           │             │ node4 │ ● claude-orq           │ 12:32–15:09 │ ███░░░░░  35% │ ≈2,8 │
│           │             │ node4 │   google-email         │ 11:05–12:15 │ ██░░░░░░  29% │ ≈2,3 │
├───────────┼─────────────┼─────────┼────────────────────────┼─────────────┼───────────────┼──────┤
│ Work   │   0% ·  42% │ node0 │ ● acervo               │ 11:56–15:05 │ ████████ 100% │    ? │
└───────────┴─────────────┴─────────┴────────────────────────┴─────────────┴───────────────┴──────┘
```

The columns are account (`conta`), quota, machine (`máquina`), folder (`pasta`), time span
(`horário`), share (`parte`) and ≈pts; `sem resposta` lists the machines that did not answer.

The **full** mode (`-v`) shows each session: id, responses, output tokens, state (`● trabalhando`
working, `○ aberta` open, `encerrada` ended), the process attached to it (by `--resume` or by the
conversation's folder) with tmux and who launched it, the start of the prompt for sessions without a
terminal, and all live processes with no consumption.

```
usage-sessoes                     # summary, all machines, last 5 h
usage-sessoes -v --conta work3 # each session and process of that account
usage-sessoes --horas 24 --json   # a whole day, for scripts
```

The `parte` column is each session's slice of the account's **weight**: tokens weighted by the ratio
of Anthropic's prices (input 1, new cache 1.25, read cache 0.1, output 5). It is a relative estimate,
not the exact percentage of the quota, which Anthropic does not break down. Usage through claude.ai
(browser, phone) writes nothing on the machines and does not appear; a machine that is down appears
under "sem resposta".

The real case that motivated the script (2026-10-21): Work 3 went up 20% in minutes with no session
open on it. `usage-sessoes` showed nine `sdk/-p` sessions on the laptop, all from a project that
called `claude -p` with `CLAUDE_CONTA` fixed to Work 3, launched by an interactive session on another
account.

### `usage-hist`: history, pace, forecast and alerts

The quota API only gives the percentage right now. `usage-hist` stores samples in a SQLite database
(`~/.local/share/claude-multiconta/usage.db` on node4, `USAGE_DB`) and computes on top of them. A
systemd user timer (`usage-coletor.timer`, installed with `usage-hist instalar`) runs
`usage-hist coletar` every 5 min:

- the **quota** of each account and window, through the same API as `usage-all` (on a 429, it waits
  10 min and doubles up to 30);
- each **claude response** on all machines, through the `usage-sessoes` agent over ssh, only what is
  new since the last collection from that machine (the first one goes back 7 days; a machine that was
  down recovers the gap when it returns, because the conversations are stored on it; lost quota
  samples cannot be recovered);
- the snapshot of the live **processes** (kept for 14 days).

```
usage-hist                 # dashboard: per account, 5h and 7d now, today, pace, forecast, bars for the last 24 h; fleet; alerts
usage-hist conta NOME       # one account: now, today, pace, forecast, 24 h, 7 days and what spent the most
usage-hist uso [--horas N]  # the usage-sessoes table built from the database (the 📊 uso tab of the page)
usage-hist dia [AAAA-MM-DD] # the day's points per account, per hour, and the projects that spent the most (≈pts)
usage-hist semana          # 7 days × accounts and the profile by hour of the day
usage-hist calibrar        # tokens per point for each account
usage-hist alertas         # only the alerts
usage-hist estado          # timer, last collection, machines, database size
usage-hist instalar | remover | coletar
```

**Points**: 1 point = 1% of an account's weekly quota. "Today" adds up the rises of the 7-day window
since midnight (after a reset the new % counts from zero). The pace is in points per hour over the
last 6 h; the forecast says when the account reaches 100% at that pace and warns if that comes before
the reset. Until there are 6 h (or the whole day) of samples, the values are shown with `*`, computed
since the start of the collection.

**Calibration**: the weight of the tokens (input 1, new cache 1.25, read cache 0.1, output 5) divided
by the account's 7d points over the last 7 days gives the tokens per point. With that, `dia` and
`usage-sessoes` show **≈pts** per project and per session. It only applies from 3 points seen onward
(low confidence up to 10).

**Alerts**: a 5h rise caused by a script (5+ points in 30 min, 80%+ from `sdk/-p` sessions); a weekly
rise with no tokens on the machines to explain it (claude.ai, phone or a machine not answering); quota
that runs out before the reset; a claude process idle for more than 1 day; the collector stopped; a
machine not answering for more than 1 h.

**The other commands read the database**: `usage-all` and `usage-sessoes` use the last collection when
it is less than 10 min old (instant and with no 429; `-v` says "do banco · coleta há N min" ("from the
database · collected N min ago"), the summary only warns about a collection 15 min old or more);
`--ao-vivo` goes back to the API and the scan. The orq page gets the quota from `usage-all --json`, so
it also comes from the database now. On a machine without a database (the laptop), the three commands
run over ssh on the machine named in `usage_db_host` in accounts.json.

## 3.5 `claude-last` and `claude-resume`: the right conversation

With `claude-resume` without `--as`, the conversation stays on its own account if the account is in
good shape (5-hour window below 70% and 15+ points left in the week); otherwise it goes to the best
account right now, copied as with `--as`.

Each account keeps its conversations in `<config_dir>/projects/<encoded folder>/<id>.jsonl`. One
account's `--resume` does not see those of the others. `claude-last` looks at all of them:

```
$ claude-last
~/Projects/foo   (claude-resume N abre a conversa N)
 1  Work      20h  Wifi do MacBook com problemas
 2  Gmail         1d  Log de Dropbox por API
    = claude-work --resume 00000000-…
```

- default: the most recent conversation of each account; `-a`: the 15 most recent of any account;
- empty conversations, token-renewal ones and `claude -p` ones (scripts, cron) are left out of the
  project list; `orq-web` has an exception for its own, identified by `state.json`;
- `--pick [N]` prints `account id` (used by the `claude-resume` function);
- `--as CONTA`: copies the conversation to the other account (file, attachments and the project
  memory that the destination account does not have yet) and prints `CONTA id`. The original stays;
  if it continues on both, they become branches. A different earlier copy is kept as `.bak-<date>`.
  The first message on the new account is more expensive (it re-reads the conversation without cache);
- `--projects`: JSON with the most recent conversation of each folder on this machine, on any
  account. It is what `orq` and the page use to list projects.

`claude-resume` opens line 1 on its account; `claude-resume 2`, line 2; `claude-resume --as gmail`
resumes using another account.

## 3.6 `orq`: four projects in a tmux grid

```
orq            # pick the projects and open the grid (if it already exists, just go back to it)
orq --new      # close the grid and pick again (the remote agents keep running)
orq --list     # just the list
orq --status-json   # orq agents on this machine (used by the page)
orq web        # shortcut for orq-web
```

`orq` asks every machine in `orq_hosts` and the laptop (`claude-last --projects` over ssh) and shows a
single list; you type up to 4 numbers or `maquina:/caminho` (`machine:/path`). **Account**: the
projects are spread over the accounts with the most slack in the 7d window; each agent already running
on an account weighs 20 points against it; staying on the conversation's account is worth 10; an
account with 5 h or 7 d at 95% is never chosen (the same rules as the page's `assign.py`). **Grid**: a
window in its own tmux server (`tmux -L orq`) with up to 4 panes, with a border showing project,
machine, account and subject. **Agents that survive**: on the machines, each project runs in a tmux
session `orq-<folder>` there (prefix `Ctrl-a`).

`orq-run` runs in each pane: it opens claude on the right account (copying the conversation if needed)
and, when you leave (`/exit`), checks the account: if it is exhausted, it resumes on the account with
the most slack in 5 s; otherwise it offers
`[Enter] retomar [c] outra conta [n] conversa nova [q] fechar` (resume, another account, new
conversation, close). `orq-hook` is the Claude Code hook that orq's agents receive via
`claude --settings` (without touching the accounts' `settings.json`): it writes the state to
`~/.cache/orq/<label>/state.json` and sends an iTerm2 notification (OSC 9 passing through the tmux
layers) when the agent finishes or asks for something. Run `orq` directly in iTerm2, not inside
another tmux.

The grid and the page coexist: `orq --status-json` lists the agents of both kinds (`kind: "sdk"` for
the page's, with `tmux` for the terminal ones), and the page shows a terminal claude as a dashed tab
with **⇄ converter** ("convert").

## 3.7 `accounts.json`: reference

```json
{
  "accounts": [
    {"name": "work",  "email": "…", "label": "Work (…)",  "config_dir": "~/.claude"},
    {"name": "gmail",    "email": "…", "label": "Gmail (…)",    "config_dir": "~/.claude-gmail", "color": "#c05621"}
  ],
  "default_account": "work",
  "tmux": true,
  "tui": "default",
  "codex": true,
  "timezone": "America/Sao_Paulo",
  "tip_on_login": true,
  "orq_hosts": ["server0", "node0", "node1", "node2", "node3"],
  "permission_mode": "auto",
  "auto_switch": true
}
```

| field | used by | meaning |
|---|---|---|
| `accounts[].name` | everything | suffix of the aliases and the key everywhere (`[a-z0-9_-]`) |
| `accounts[].config_dir` | everything | the account's `CLAUDE_CONFIG_DIR`; `~/.claude` is the default one |
| `accounts[].label` | `usage-all`, page | displayed name; what comes before ` (` is the short name |
| `accounts[].color` | page | the account's colour; without it, a palette by position |
| `default_account` | aliases | the account of plain `claude` |
| `tmux`, `tui`, `tip_on_login` | `install.sh` | tmux sessions, renderer, tip when the terminal opens |
| `codex` | `install.sh` | install Codex and create `codex-login`/`codex-usage` |
| `timezone` | `usage-all`, `codex-usage` | time zone of the reset times |
| `orq_hosts` | `orq`, page, `deploy.sh` | remote machines; the laptop is always included |
| `usage_db_host` | `usage-hist`, `usage-all`, `usage-sessoes` | machine with the usage database (node4); on a machine without a database, the commands run there over ssh |
| `permission_mode` | page | the agents' mode: `auto`, `manual`, `acceptEdits`, `plan` |
| `auto_switch` | page | switch accounts automatically when the quota runs out (default `true`) |

The file in the repository is copied to `~/.config/claude-multiconta/accounts.json` by `install.sh`;
`orq-web` (and the container, through a volume) reads it from there, or from `CLAUDE_MULTICONTA_CONF`.
