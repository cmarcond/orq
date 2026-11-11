# claude-orq

> **English summary.** Tooling for someone who pays for several Claude accounts
> and runs many Claude Code agents in parallel across several machines.
> `claude-multiconta` keeps each account isolated (`CLAUDE_CONFIG_DIR`), with
> aliases and a usage view across accounts. `orq-web` is a supervisor
> (`orq-agent`, Claude Code in stream-json behind a Unix socket) plus a web page
> to watch and drive every agent on every machine: usage, per-host tabs, chat,
> permission prompts, attachments. `claude-highlevel` is an earlier experiment
> with tiered agent pipelines (teacher/executor). Python standard library, tests
> and CI per project. Host names, addresses and accounts in the docs and tests
> are placeholders. The rest of the documentation is in Portuguese.
>
> English architecture and security overview: [`docs/ARCHITECTURE.en.md`](docs/ARCHITECTURE.en.md).
>
> **Project history.** The project was developed in a private repository from
> 9 October to 5 November 2026 (83 commits). This public repository is a
> sanitized export of that code, with placeholder host names, addresses and
> accounts, which is why its history starts with a single commit. It was designed
> and directed by the author and written with Claude Code as the coding agent.

Vários Claude Codes, várias contas, várias máquinas, uma página.

| projeto | o que é | CI |
|---|---|---|
| [`claude-multiconta/`](claude-multiconta/) | contas do Claude Code isoladas por `CLAUDE_CONFIG_DIR`, aliases, `usage-all` (cota com marcador de ritmo), `orq` (grade tmux), `claude-last`/`claude-resume`. Shell + Python 3.8, sem dependências | `sh -n`, `py_compile`, pytest do `usage-all` |
| [`orq-web/`](orq-web/) | supervisor `orq-agent` (Claude Code em stream-json atrás de um socket Unix) e `orq-web` (página: cota, abas por máquina, chat, permissões, anexos, converter sessões de terminal). Python 3.8+ stdlib; frontend em módulos ES | ruff, pytest 3.8–3.12, `node --test`, imagem Docker no GHCR |
| [`claude-highlevel/`](claude-highlevel/) | pipeline de Claude Codes em níveis (professor/executor) com PTY e tela própria. Referência histórica: a ideia está sendo reimplementada dentro do orq-web (ver `orq-web/docs/PLANO.md`, fase 3) | pytest rápido |

**Manual completo em [`docs/`](docs/README.md)**: visão geral, instalação e deploy, contas e cota, guia da página, arquitetura, vínculos e níveis, referência de comandos e API, operação.

Ordem de instalação numa máquina nova: `claude-multiconta/install.sh` (contas e utilitários), depois
`orq-web/install.sh` (ou `orq-web/deploy.sh` para todas as máquinas). O servidor da página pela rede
roda em container no server0: `orq-web/deploy/push-server0.sh`.

## Licença

MIT. Veja [LICENSE](LICENSE).
