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
lista de projetos), `usage-all --json` (cota) e `orq --status-json`
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
encerra o agente (a conversa fica salva). **📎** ou arrastar arquivos para o chat envia-os para
`_inbox/` dentro da pasta do projeto, na máquina do agente (por ssh quando remota), e a mensagem
sai com os caminhos, para o Claude ler o PDF, a planilha etc. direto de lá.

## Níveis (professor → executor)

Qualquer agente da página pode ganhar um **nível de cima**: **⇅ subir nível** na barra do chat escolhe uma
receita (`orqweb/pipelines/*.toml`: `professor → executor` ou `professor → arquiteto → executor`) e sobe
os níveis na mesma máquina, pasta e conta, cada um como um agente normal da página (`orq-agent spawn
--role-file - --parent … --level …`). A aba passa a conversar com o nível de cima; o agente original
vira o executor, escondido atrás, e recebe o trabalho pelo `SendMessage` nativo do Claude Code (as
sessões se acham pelo `ListAgents`). O painel à direita mostra um cartão por nível (estado; clique para
ver o chat cru daquele nível) e os **fios**: cada mensagem entre níveis, marcada ✓✓ quando chega do
outro lado. **↓ desfazer** encerra os níveis de cima e a aba volta ao executor.

Para isso todo agente sobe com `claude -n <nome>`, o nome que o `SendMessage` usa: o rótulo saneado
(`kaggle@server0` → `kaggle-server0`; o `@` é recusado). Agente aberto antes desta versão não tem
nome: o seletor de níveis oferece **↻ reabrir com nome** (`POST /api/agent/<x>/reopen`, que roda
`orq-agent spawn --mode resume --restart` e retoma a mesma conversa). Os níveis sobem no mesmo
`permission_mode`: modos diferentes fazem o Claude Code reter a mensagem entre eles. Se um nível pede
permissão, o cartão dele no painel mostra o pedido com Permitir/Negar; uma pergunta (`AskUserQuestion`)
abre o chat daquele nível.

## Cota acabou: troca de conta sozinha

Quando o claude avisa que a conta esgotou (`rate_limit_event` com `status: rejected`, ou um turno que
termina em erro de limite), o supervisor marca `state: quota` e guarda a última mensagem da página em
`retry`. Na consulta seguinte às máquinas, o servidor reabre o agente na conta com mais folga
(`orq-agent spawn --mode resume --restart`; o `claude-last --as` copia a conversa para a conta nova) e
reenvia a mensagem. A página avisa com um toast e a aba continua na mesma conversa; `/api/state` traz
`migrations`. Desligue com `"auto_switch": false` no `accounts.json`.

## Notificações

O 🔔 no topo pede permissão ao navegador uma vez. Depois, com a página fora de vista, cada agente que
pede permissão, faz uma pergunta ou termina um turno gera uma notificação; o título da aba mostra
quantos agentes esperam você, por exemplo `(2) orq`, até você abrir cada um.

Um `claude` interativo que já está rodando num tmux (aberto por `claude-gmail` etc.) aparece na
página como aba tracejada com o botão **⇄ converter**: a página dá `/exit` nele (ou Ctrl-C, ou
SIGTERM, nessa ordem) e reabre a mesma conversa como agente da página. No terminal:

```sh
converte-web                     # lista os claude interativos em tmux de todas as máquinas
converte-web kaggle@server0    # converte um (ou --all)
```

**Mac como máquina remota.** Um Mac que recebe agentes por ssh precisa de um servidor tmux nascido na
sessão gráfica, porque o Keychain (onde o Claude Code guarda a credencial) não abre para sessões ssh.
`deploy/macos-tmux-keep.sh` instala um LaunchAgent de usuário que mantém uma sessão `keep` viva desde o
login; os agentes entram nesse servidor e a cota remota é lida com `tmux run-shell`. Também é preciso
`sshd` ligado (`sudo systemsetup -setremotelogin on`) e um `~/.zshenv` com `~/.local/bin` e
`/opt/homebrew/bin` no `PATH`, já que sessões ssh não leem o `.zshrc`.

Nas máquinas, o servidor chama `~/.local/bin/orq-agent`:

```sh
orq-agent spawn --dir ~/acervo --account gmail --label acervo@node0   # o que a página roda
orq-agent list                                                          # agentes desta máquina
orq-agent stop acervo@node0                                           # encerra um
orq-agent attach acervo@node0 --since 0                               # ponte crua (debug)
orq-agent convert kaggle@server0                                      # o que o converte-web roda lá
orq-agent spawn --dir ~/acervo --account gmail --label acervo~professor@node0 \
          --mode new --role-file - --parent acervo@node0 --level professor  # nível (papel pelo stdin)
```

Configuração (no `accounts.json` do claude-multiconta): `permission_mode` dos agentes da página
(`auto` deixa o Claude decidir o rotineiro; `manual` pergunta tudo), `orq_hosts` e `auto_switch`
(padrão `true`: trocar de conta sozinho quando a cota acaba).
Variáveis: `CLAUDE_MULTICONTA_CONF`, `ORQ_BIN` (onde estão os utilitários), `ORQ_STATE_DIR`
(padrão `~/.cache/orq`), `ORQ_NO_OPEN=1` (não abre o navegador), `ORQ_USAGE_HOST` (máquina que
responde a cota, por ssh; para o servidor rodar onde não há credenciais).

## Pela rede (container no server0)

`deploy/push-server0.sh` manda o código ao server0 e roda `deploy/server0/deploy.sh` lá: gera a
senha em `~/.config/orq-web/password` (se não houver), um `ssh_config` só do container com os IPs
das máquinas, autoriza ssh para a própria máquina e sobe o compose publicado só no IP do ZeroTier.
A imagem tem só Python e o cliente ssh: agentes, cota e utilitários rodam nas máquinas, por ssh
(`ORQ_LOCAL_HOST=none`). Entrar: `http://<ip zerotier>:8765/`, senha `ssh server0 cat
~/.config/orq-web/password`. Variáveis do servidor na rede: `ORQ_BIND` (fora de 127.0.0.1 exige a
senha), `ORQ_PASSWORD_FILE`, `ORQ_ALLOWED_HOSTS`, `ORQ_LOCAL_HOST`, `ORQ_EXTRA_HOSTS`, `ORQ_SSH_CONFIG`,
`ORQ_USAGE_HOST`.

## Arquitetura

```
navegador ──SSE/POST──> orq-web (handlers) ──Bridge (orq-agent attach, local ou ssh)──> socket Unix
                              │                                                           │
                        StateCache ──> External (usage-all, claude-last, orq)         Supervisor
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
| `orqweb/agent/convert.py` | fecha um `claude` interativo de tmux em escalada e reabre a conversa como agente |
| `orqweb/convert.py` | CLI `converte-web`: lista e converte em qualquer máquina |
| `orqweb/server/external.py` | chamadas aos utilitários e às máquinas (ssh), por um `Runner` injetável |
| `orqweb/server/assign.py` | escolha da conta pela folga no 7d e rótulo `pasta@maquina` |
| `orqweb/server/state.py` | cache de cota/agentes/projetos; laços de fundo só com a página aberta |
| `orqweb/server/bridge.py` | uma ponte por aba; ponte passageira para entregar sem aba |
| `orqweb/server/migrate.py` | troca de conta quando a cota acaba: escolhe a conta com folga, reabre com `--restart`, reenvia a mensagem |
| `orqweb/server/pipelines.py` | receitas de níveis (`orqweb/pipelines/*.toml`), rótulos `base~nível@host`, papéis com os vizinhos resolvidos |
| `orqweb/server/handlers.py` | rotas, SSE, token, CSP, estáticos, níveis (`/api/pipelines`, `upgrade`, `downgrade`) |
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
- mensagens `user` ganham `orq_origin`: `page` (eco do que a página mandou), `agent` (veio de outra
  sessão pelo SendMessage: `origin.name` diz quem, `origin.body` é o texto sem o embrulho) ou
  `injected` (sem marca de humano: cron, loop, sistema);
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
(permissão, AskUserQuestion, interrupção, injeção, streaming, e `SendMessage` entre dois processos
falsos por uma caixa de correio em `FAKE_PEER_DIR`), então a CI não precisa do Claude.
`tests/test_supervisor.py` cobre o supervisor pelo socket; `tests/test_handlers.py` sobe o
servidor HTTP real com o supervisor atrás da ponte. Sockets Unix exigem caminhos curtos, por isso
esses testes usam `/tmp`.

A CI (GitHub Actions) roda ruff e pytest em Python 3.8, 3.11 e 3.12, e os testes de frontend
em Node 22. O supervisor roda nas máquinas remotas, por isso o código fica compatível com 3.8.

## Por fazer

- histórico de rodadas de níveis e linha do tempo de marcos por projeto;
- modelo e esforço por nível na receita; receitas editáveis pela página;
- um professor para vários executores (máquinas diferentes).
