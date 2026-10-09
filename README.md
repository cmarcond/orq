# orq-web

Chat local com agentes do Claude Code espalhados por várias máquinas e contas, no estilo do
Remote Control, sem depender do claude.ai.

- **`orq-agent`** (supervisor, roda em cada máquina): roda o `claude -p` em stream-json, o
  protocolo do Agent SDK, na pasta e na conta certas, guarda os eventos numerados e os expõe num
  socket Unix. Sobrevive à queda da conexão; quem reconecta pede "a partir do N" e recebe o que
  perdeu. A conversa é uma sessão normal do Claude Code: `claude --resume` no terminal a retoma.
- **`orq-web`** (servidor + página, roda no laptop): cota das contas em cima, uma aba por agente,
  e um chat com Markdown, ferramentas dobráveis, permissões por botão e formulários para o
  `AskUserQuestion`. Fala com os agentes por `orq-agent attach`, local ou por ssh.

Extraído do projeto [`claude-multiconta`](../claude-multiconta), que continua dono das contas
(`accounts.json`) e dos utilitários que este pacote chama: `claude-last` (sessão a retomar e
lista de projetos), `claude-usage-fast`/`codex-usage-fast --json` (cota) e `orq --status-json`
(agentes por máquina).

## Uso

```sh
./install.sh                 # esta máquina: copia o pacote para ~/.local/lib/orq-web e cria os comandos (sem pip)
./deploy.sh                  # laptop + todas as máquinas de orq_hosts (ou: ./deploy.sh server0 node1)
orq-web                      # abre http://127.0.0.1:8765/?t=<token> no navegador
orq-web --port 8766          # outra porta; um segundo orq-web na mesma porta reaproveita o link
```

Para desenvolver, `pip install -e ".[dev]"` (ou `uv pip install -e ".[dev]"`) instala o pacote em
modo editável com as ferramentas de teste.

Na página: **+** abre um projeto (lista de todas as máquinas, ou `maquina:/caminho`); `⌘1`–`⌘9`
trocam de aba; `Enter` envia, `Shift+Enter` quebra linha, `Esc` interrompe; `✕` duas vezes
encerra o agente (a conversa fica salva).

Nas máquinas, o servidor chama `~/.local/bin/orq-agent`:

```sh
orq-agent spawn --dir ~/acervo --account gmail --label acervo@node0   # o que a página roda
orq-agent list                                                          # agentes desta máquina
orq-agent stop acervo@node0                                           # encerra um
orq-agent attach acervo@node0 --since 0                               # ponte crua (debug)
```

Configuração (no `accounts.json` do claude-multiconta): `permission_mode` dos agentes da página
(`auto` deixa o Claude decidir o rotineiro; `manual` pergunta tudo), `orq_hosts`, `codex`.
Variáveis: `CLAUDE_MULTICONTA_CONF`, `ORQ_BIN` (onde estão os utilitários), `ORQ_STATE_DIR`
(padrão `~/.cache/orq`), `ORQ_NO_OPEN=1` (não abre o navegador).

## Arquitetura

```
navegador ──SSE/POST──> orq-web (handlers) ──Bridge (orq-agent attach, local ou ssh)──> socket Unix
                              │                                                           │
                        StateCache ──> External (claude-usage-fast, claude-last, orq)   Supervisor
                                                                                          │
                                                                                 claude -p stream-json
```

| módulo | responsabilidade |
|---|---|
| `orqweb/config.py` | lê o `accounts.json`; ambiente do `claude` por conta; caminhos de estado |
| `orqweb/agent/events.py` | log numerado com replay, carimbo de origem (`page`/`injected`), estado derivado, histórico antigo |
| `orqweb/agent/supervisor.py` | processo + socket + clientes; encerramento limpo |
| `orqweb/agent/client.py` | `attach` (ponte stdin/stdout), `stop`, `spawn` em tmux `orqs-<nome>` |
| `orqweb/agent/state.py` | `state.json` atômico, lido pelo `orq --status-json` |
| `orqweb/server/external.py` | chamadas aos utilitários e às máquinas (ssh), por um `Runner` injetável |
| `orqweb/server/assign.py` | escolha da conta pela folga no 7d e rótulo `pasta@maquina` |
| `orqweb/server/state.py` | cache de cota/agentes/projetos; laços de fundo só com a página aberta |
| `orqweb/server/bridge.py` | uma ponte por aba; ponte passageira para entregar sem aba |
| `orqweb/server/handlers.py` | rotas, SSE, token, CSP, estáticos |
| `orqweb/server/app.py` | porta, link com token, segundo servidor, encerramento |
| `static/js/format.js` | escape, tempos, Markdown sanitizado |
| `static/js/classify.js` | registros internos, origem de máquina, resumos de ferramenta |
| `static/js/events.js` | redutor puro: evento do stream -> ações de interface |
| `static/js/app.js` | abas, EventSource, DOM, composer, permissões, seletor de projeto |

### Protocolo do socket

Primeira linha do cliente: `{"since": N}`. Depois, linhas JSON nos dois sentidos, iguais às do
stream-json do `claude -p`, mais:

- `{"type": "orq", "level": ..., "text": ...}` avisos do supervisor;
- `{"type": "orq", "subtype": "permission_answered", "request_id": ..., "behavior": ...}` a página
  respondeu uma permissão (o replay fecha o cartão);
- mensagens `user` ganham `orq_origin`: `page` (eco do que a página mandou) ou `injected` (sem
  marca de humano: cron, loop, sistema);
- `{"orq": "quit"}` do cliente encerra o agente.

`stream_event` (texto parcial) só vai ao vivo; não entra na sequência nem no `events.jsonl`.

### Segurança

Servidor só em `127.0.0.1`, com `Host` conferido; `/api` exige o token do link (`X-Orq-Token`,
ou `?t=` no SSE, que não aceita cabeçalhos); Content-Security-Policy restringe scripts a `'self'`
e ao cdnjs; o Markdown do modelo passa pelo DOMPurify; `/static` não aceita travessia; o socket
do agente é `0600`.

## Desenvolvimento

```sh
make test       # pytest + node --test
make lint       # ruff check + format --check
make fmt
```

Os testes de integração usam `tests/fake_claude.py`, que imita o protocolo stream-json
(permissão, AskUserQuestion, interrupção, injeção, streaming), então a CI não precisa do Claude.
`tests/test_supervisor.py` cobre o supervisor pelo socket; `tests/test_handlers.py` sobe o
servidor HTTP real com o supervisor atrás da ponte. Sockets Unix exigem caminhos curtos, por isso
esses testes usam `/tmp`.

A CI (GitHub Actions) roda ruff e pytest em Python 3.8, 3.11 e 3.12, e os testes de frontend
em Node 22. O supervisor roda nas máquinas remotas, por isso o código fica compatível com 3.8.

## Por fazer

- anexar arquivos pela página;
- trocar de conta automaticamente quando a cota acaba (hoje só o `orq-run` do terminal faz);
- acesso pelo celular (servidor numa máquina sempre ligada, com Tailscale e login);
