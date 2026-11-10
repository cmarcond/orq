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

**No celular**: `http://<ip>:8765/m` é a mesma página com outro arranjo (`static/mobile.html` +
`mobile.css`, mesmo `app.js`): abas numa linha com rolagem lateral, cota recolhida numa faixa com o TOTAL
(toque abre as contas), chat com a tela inteira, botões só com ícone e o painel lateral como gaveta. O
login volta para `/m` quando a entrada foi por ela.

Para desenvolver, `pip install -e ".[dev]"` (ou `uv pip install -e ".[dev]"`) instala o pacote em
modo editável com as ferramentas de teste.

Na página: **+** abre um projeto (lista de todas as máquinas, ou `maquina:/caminho`); `⌘1`–`⌘9`
trocam de aba; `Enter` envia, `Shift+Enter` quebra linha, `Esc` interrompe; `✕` duas vezes
encerra o agente (a conversa fica salva). **📎** ou arrastar arquivos para o chat envia-os para
`_inbox/` dentro da pasta do projeto, na máquina do agente (por ssh quando remota), e a mensagem
sai com os caminhos, para o Claude ler o PDF, a planilha etc. direto de lá.

## Vínculos entre agentes (🔗)

**🔗 vincular** na barra do chat escolhe outro agente da página, em qualquer máquina: os dois passam a se
ver no `ListAgents` e a pedir coisas um ao outro pelo `SendMessage`, sem papel imposto (são pares).
Cada um recebe uma nota `[orq] Vínculo criado com o agente …` dizendo o nome do outro e como falar com
ele. O painel lateral ganha a aba **vínculos**: um cartão por agente ligado (clique abre a aba dele, ✕
duas vezes desfaz) e os fios, cada pedido e cada resposta pelo `SendMessage`. A aba mostra `🔗n`.
`POST /api/agent/<x>/link {to}` e `/unlink {to}`; `/api/state` traz `links`.

**Um professor, vários executores**: com vínculos feitos, **⇅ subir nível** oferece a receita
`professor → executores (este + vinculados)`: sobe um professor acima do agente da aba e o vincula
também a cada agente vinculado a ela (em qualquer máquina). O papel dele lista os executores
(`{executores}`: nome de sessão, máquina e pasta) e manda distribuir o trabalho pelo que cada um
tem à mão, intermediando quando um precisa do resultado do outro. **↓ desfazer** desfaz os vínculos
do professor e encerra-o; os vínculos entre os executores ficam.

Por baixo, `orq-agent link A B` na máquina de `A`. Na mesma máquina não há túnel, mas o `ListAgents`
só lê a pasta `sessions/` da própria conta: agentes de contas diferentes ganham o cartão e a chave um
do outro espelhados (mesmo socket). Entre máquinas, o túnel ssh abaixo. Os níveis (professor →
executor) continuam uma receita por cima do mesmo mecanismo.

## Vínculo entre máquinas (`orq-agent link`)

Os níveis usam o `SendMessage`/`ListAgents` nativos do Claude Code, que só enxergam sessões da mesma
máquina: cada claude vivo tem em `<config_dir>/sessions/` um cartão `<pid>.json` (nome, pasta, estado e
o socket Unix em que recebe mensagens) e um `<pid>.<hash>.key` ao lado. `orq-agent link A@aqui B@lá`
estica isso por ssh: sobe um `ssh -N` com o socket de cada agente encaminhado até a outra máquina
(`-L`/`-R` com caminhos Unix) e espelha o cartão e a chave de cada um na pasta `sessions/` do outro,
apontando para o socket encaminhado. Daí `A` e `B` se listam e se falam como se fossem locais. O
vínculo fica numa sessão tmux `orql-<a>--<b>`; `orq-agent unlink` (ou um dos agentes fechar, ou o túnel
cair) fecha o túnel e apaga os espelhos nas duas máquinas, menos os que outro vínculo vivo ainda usa: o
agente da aba e o professor acima dele, ligados ao mesmo executor remoto, dividem o mesmo cartão e o
mesmo socket encaminhado (`orq-<pid>.sock`, nome que nenhuma sessão de verdade usa), e quem sobra assume
o encaminhamento. Na mesma máquina, o espelho entre contas segue a mesma regra, recolhido quando um dos
agentes fecha. `orq-agent links` lista; `orq-agent peer` é o lado
remoto (imprime o cartão, grava ou apaga um espelho). Testado em 2026-10-12 entre laptop (OpenSSH 10) e
server0 (OpenSSH 8.2), sem mexer no sshd. É a base dos vínculos entre chats na página (a fazer).

## Arquivos que o agente compartilha (`_outbox/`)

O caminho inverso do 📎: todo agente da página sobe com uma nota no system prompt dizendo que, para
compartilhar um arquivo com você (PDF, imagem, vídeo, planilha…), basta copiá-lo para `_outbox/` na
raiz do projeto. **📂** na barra do chat abre a lista no painel lateral (mesmo painel dos níveis, com
abinhas no topo): nome, tamanho, idade; clique mostra a prévia (imagem, PDF no visualizador do
navegador, vídeo, áudio, texto e Markdown) e ⬇ baixa. A lista é relida ao fim de cada rodada e a cada
30 s; arquivo novo vira toast e destaque. A pasta fica na máquina do agente: o servidor lista com
`orq-agent ls` e serve com `orq-agent cat` (por ssh quando remota; faixas `Range` para o vídeo pular no
tempo). `GET /api/agent/<x>/files` e `GET /api/agent/<x>/file?name=…[&dl=1]`. Quem escreve na pasta
é o agente; a página nunca apaga nada dela.

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

## Conjuntos (⊞): vários agentes de uma vez

**⊞ conjuntos** no topo lista JSONs salvos em `~/.config/orq-web/conjuntos/` (`ORQ_SETS_DIR`): cada um
descreve agentes para abrir juntos (máquina, pasta, `modo`, `conta` opcional, `prompt` próprio no system
prompt, `tarefa` inicial), um `permissao` para todos e, opcionalmente, um `lider`: o professor sobe acima do
primeiro agente e os demais são vinculados a ele como executores (receita `professor-executores`, papel
substituível por `lider.prompt`). Sem `lider`, agentes independentes. A página recusa abrir se algum
`maquina:pasta` já tiver agente vivo. **📄 enviar JSON** recebe um pronto; **📷 salvar abas abertas**
fotografa o que está aberto num conjunto novo. O servidor abre numa thread (`handlers.open_set_job`) e o
andamento vai em `conjuntos_abrindo` do `/api/state`. Formato e detalhes: `docs/04-pagina-orq-web.md`, 4.13.

## Cota acabou: troca de conta sozinha

Quando o claude avisa que a conta esgotou (`rate_limit_event` com `status: rejected`, ou um turno que
termina em erro de limite), o supervisor marca `state: quota` e guarda a última mensagem da página em
`retry`. Na consulta seguinte às máquinas, o servidor reabre o agente na conta com mais folga
(`orq-agent spawn --mode resume --restart`; o `claude-last --as` copia a conversa para a conta nova) e
reenvia a mensagem. A página avisa com um toast e a aba continua na mesma conversa; `/api/state` traz
`migrations`. Desligue com `"auto_switch": false` no `accounts.json`.

## Notificações

O 🔔 no topo pede permissão ao navegador uma vez. Depois, com a página fora de vista, cada agente que
pede permissão, faz uma pergunta ou termina um turno gera uma notificação e um bipe; o título da aba mostra
quantos agentes esperam você, por exemplo `(2) orq`, até você abrir cada um. Navegadores só dão
notificação em `https` ou `localhost`: pela rede em `http://<ip>` o sino avisa isso e fica só o bipe (o
clique nele destrava o áudio) e a contagem no título. Para ter notificação no laptop, abra a página por um
túnel: `ssh -N -L 8765:<ip zerotier>:8765 server0` e `http://127.0.0.1:8765/`.

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
| `orqweb/agent/files.py` | `_outbox/`: lista a pasta e manda bytes com tamanho na frente (`orq-agent ls`/`cat`) |
| `orqweb/agent/link.py` | vínculo entre máquinas: registro de sessões, espelho, túnel ssh e ciclo de vida (`orq-agent link`) |
| `orqweb/agent/state.py` | `state.json` atômico, lido pelo `orq --status-json` |
| `orqweb/agent/convert.py` | fecha um `claude` interativo de tmux em escalada e reabre a conversa como agente |
| `orqweb/convert.py` | CLI `converte-web`: lista e converte em qualquer máquina |
| `orqweb/server/external.py` | chamadas aos utilitários e às máquinas (ssh), por um `Runner` injetável |
| `orqweb/server/assign.py` | escolha da conta pela folga no 7d e rótulo `pasta@maquina` |
| `orqweb/server/state.py` | cache de cota/agentes/projetos; laços de fundo só com a página aberta |
| `orqweb/server/bridge.py` | uma ponte por aba; ponte passageira para entregar sem aba |
| `orqweb/server/migrate.py` | troca de conta quando a cota acaba: escolhe a conta com folga, reabre com `--restart`, reenvia a mensagem |
| `orqweb/server/pipelines.py` | receitas de níveis (`orqweb/pipelines/*.toml`), rótulos `base~nível@host`, papéis com os vizinhos resolvidos |
| `orqweb/server/sets.py` | conjuntos (⊞): formato JSON, validação, catálogo, conflitos, plano e fotografia das abas abertas |
| `orqweb/server/handlers.py` | rotas, SSE, token, CSP, estáticos, níveis (`/api/pipelines`, `upgrade`, `downgrade`), conjuntos (`/api/conjuntos`, `open_set_job`) |
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
do agente é `0600`. Arquivo da `_outbox/` sai com `Content-Security-Policy: sandbox` e `nosniff`, e
HTML nunca é servido inline (um `.html` escrito pelo agente não roda na origem da página); só nomes
sem caminho, dentro da `_outbox/` do agente.

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
- conjuntos: andamento no painel (hoje só toasts), abrir "só os que faltam" quando parte já está aberta,
  `lider.prompt` em receitas com mais de um nível.
