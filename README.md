# claude-orq

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
