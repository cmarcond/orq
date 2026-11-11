# claude-orq

Several Claude Codes, several accounts, several machines, one page.

This is tooling for someone who pays for several Claude accounts and runs many Claude Code agents in
parallel across several machines. `claude-multiconta` keeps each account isolated
(`CLAUDE_CONFIG_DIR`), with aliases and a usage view across accounts. `orq-web` is a supervisor
(`orq-agent`, Claude Code in stream-json behind a Unix socket) plus a web page to watch and drive
every agent on every machine: usage, per-host tabs, chat, permission prompts, attachments.
`claude-highlevel` is an earlier experiment with tiered agent pipelines (teacher/executor). Python
standard library, tests and CI per project. Host names, addresses and accounts in the docs and tests
are placeholders.

> **Project history.** Development started on 9 October 2026. The commit
> history here is the original one, with host names, addresses and account
> identifiers replaced by placeholders. The project was designed and directed by
> the author and written with Claude Code as the coding agent.

| project | what it is | CI |
|---|---|---|
| [`claude-multiconta/`](claude-multiconta/) | Claude Code accounts isolated by `CLAUDE_CONFIG_DIR`, aliases, `usage-all` (quota with a pace marker), `orq` (tmux grid), `claude-last`/`claude-resume`. Shell + Python 3.8, no dependencies | `sh -n`, `py_compile`, pytest for `usage-all` |
| [`orq-web/`](orq-web/) | the `orq-agent` supervisor (Claude Code in stream-json behind a Unix socket) and `orq-web` (the page: quota, per-machine tabs, chat, permissions, attachments, converting terminal sessions). Python 3.8+ stdlib; frontend in ES modules | ruff, pytest 3.8–3.12, `node --test`, Docker image on GHCR |
| [`claude-highlevel/`](claude-highlevel/) | a pipeline of Claude Codes in levels (teacher/executor) with a PTY and its own screen. Historical reference: the idea is being reimplemented inside orq-web (see `orq-web/docs/PLANO.md`, phase 3) | fast pytest |

**Full manual in [`docs/`](docs/README.md)**: overview, installation and deploy, accounts and quota, a guide to the page, architecture, links and levels, command and API reference, operations. There is also an architecture and security overview in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Installation order on a new machine: `claude-multiconta/install.sh` (accounts and utilities), then
`orq-web/install.sh` (or `orq-web/deploy.sh` for all machines). The server for the page over the
network runs in a container on server0: `orq-web/deploy/push-server0.sh`.

## License

MIT. See [LICENSE](LICENSE).
