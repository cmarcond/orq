# Plano: deploy no server0, monorepo com CI/CD e níveis (highlevel) no orq-web

Decisões tomadas em 2026-10-11:

| tema | decisão |
|---|---|
| container | só o servidor `orq-web`; fala com **todas** as máquinas por ssh, inclusive o próprio server0. Sem `claude` dentro |
| acesso pela rede | página de login com cookie; senha única num arquivo do server0; HTTP puro, pois o ZeroTier é rede privada |
| GitHub | **monorepo privado** com `orq-web`, `claude-multiconta` e `claude-highlevel`; CI por projeto; imagem Docker no GHCR |
| highlevel | **reimplementar** a ideia dentro do orq-web: níveis como agentes da página, compatíveis com abas, socket e replay |

Teste de viabilidade feito antes do plano: duas sessões `claude -p` em stream-json, nomeadas com `-n`,
conversam pelo canal nativo. O professor achou o executor com `ListAgents`, delegou com `SendMessage`,
o executor fez o trabalho e respondeu, e o professor relatou. Portanto os níveis não precisam de PTY.

Ordem: cada fase começa pelos testes (unitários e de integração com `fake_claude`), depois o código,
depois a verificação real nas máquinas. Nada vai para o server0 sem a suíte verde.

---

## Fase 1 — acesso pela rede e container no server0  ✅ feita em 2026-10-11

No ar em `http://10.0.0.10:8765/` (senha: `ssh server0 cat ~/.config/orq-web/password`).
O container enxerga server0, node3 e laptop por ssh; a cota vem do server0; `tmux run-shell` resolve o
Keychain do laptop. Diferenças em relação ao planejado: não existe `ORQ_BIN` no container (nada roda local);
o `push-server0.sh` manda o código por uma conexão ssh só, porque scp/sftp caíam na rede instável.

### 1.1 Login com cookie (`orqweb/server/auth.py`)

- `ORQ_PASSWORD_FILE` (padrão `~/.config/orq-web/password`): a senha, uma linha. Sem o arquivo, o
  servidor continua só com o token do link, como hoje.
- `GET /login` página mínima; `POST /login` compara em tempo constante, cria uma sessão aleatória em
  memória e devolve `Set-Cookie: orq=…; HttpOnly; SameSite=Strict; Path=/`. Falha dorme 1 s e conta;
  cinco falhas seguidas do mesmo IP bloqueiam por 1 min.
- `authed()` aceita cookie **ou** token (o token continua valendo para o uso local e para o `?t=` do SSE).
- `POST /logout` apaga a sessão. Sessões expiram em 30 dias sem uso.
- A página, sem cookie e sem token, redireciona para `/login`.

Testes (`tests/test_auth.py`): senha certa dá cookie e acesso a `/api/state` e ao SSE; senha errada
dá 403 e não dá cookie; sem arquivo de senha nada muda; bloqueio após cinco falhas; logout invalida;
cookie não vale com `Host` que não seja o permitido.

### 1.2 Escutar fora do 127.0.0.1

- `ORQ_BIND` (padrão `127.0.0.1`). Com qualquer outro valor, **o arquivo de senha passa a ser
  obrigatório**: o servidor recusa subir sem ele.
- `ok_host()` passa a aceitar o IP/nome de `ORQ_BIND` além de localhost.
- `ORQ_LOCAL_HOST` (padrão `laptop`): o nome que o servidor dá à máquina onde roda. No container ele
  vale `nenhum`: não há agentes locais, tudo é ssh, inclusive `server0`.

Testes: bind externo sem senha recusa; `Host` do ZeroTier aceito; sem host local a lista de projetos
e de agentes não tenta rodar nada localmente.

### 1.3 Container

- `Dockerfile`: `python:3.12-slim`, `openssh-client`, `tmux` não (não roda agente), usuário sem root,
  `COPY orqweb/`, `CMD orq-web`. Imagem pequena.
- `docker-compose.yml` para o server0: porta `10.0.0.10:8765:8765`; volumes só leitura:
  `~/.ssh` (chave e `known_hosts`), `~/.config/claude-multiconta/accounts.json`, `~/.config/orq-web/password`;
  `restart: unless-stopped`.
- No container, `~/.local/bin` não existe: `ORQ_BIN` aponta para onde? Os utilitários (`usage-all`,
  `claude-last`, `orq --status-json`) rodam **por ssh no server0**, não dentro do container. A cota
  (`usage-all --json`) também passa a vir por ssh de uma máquina com as credenciais: `ORQ_USAGE_HOST`
  (padrão: o host local; no container, `server0`).
- `deploy/server0.sh`: `docker compose pull && docker compose up -d`, mais a criação do arquivo de
  senha se não existir (`openssl rand -base64 18`), com permissão `600`. A senha você lê por ssh:
  `ssh server0 cat ~/.config/orq-web/password`.

Testes: `docker build` na CI; teste de fumaça que sobe o container com um `accounts.json` falso e um
arquivo de senha, confere `GET /login` 200 e que `/api/state` sem cookie dá 403.

Verificação real: subir no server0, abrir `http://10.0.0.10:8765/` do laptop e do celular pelo
ZeroTier, logar, abrir uma aba do `kaggle@server0` e do `dev@node3`.

## Fase 2 — monorepo privado e CI/CD  ✅ feita em 2026-10-11

`https://github.com/cmarcond/orq`. CI por projeto verde; imagem `ghcr.io/cmarcond/orq-web:latest`.
O monorepo é a fonte de verdade a partir daqui; as pastas `~/Projects/orq-web` e `~/Projects/claude-multiconta` são
cópias antigas. O deploy do server0 continua pelo `push-server0.sh`; para puxar do GHCR, `docker login ghcr.io`
lá com um PAT `read:packages`.

### 2.1 Layout

```
claude-orq/                     (nome a confirmar)
├── orq-web/                    histórico preservado (git subtree add do repo atual)
├── claude-multiconta/          snapshot atual (não tinha git)
├── claude-highlevel/           snapshot do server0 (31/08, o mais novo), só referência até a fase 3
├── .github/workflows/
│   ├── orq-web.yml             testes py 3.8–3.12 + node; build da imagem; push no GHCR em main e tags
│   ├── claude-multiconta.yml   `sh -n install.sh`, `py_compile bin/*`, testes do usage-all (a criar)
│   └── highlevel.yml           `pytest -m "not slow"`
└── README.md                   mapa dos três
```

- Workflows com `paths:` para rodar só o que mudou.
- Imagem: `ghcr.io/<usuario>/orq-web:latest` e `:<sha>`; `GITHUB_TOKEN` basta para publicar.
- O server0 faz `docker login ghcr.io` uma vez com um PAT de `read:packages`, guardado só lá.
- Sem deploy automático: o CI publica, você (ou eu, por ssh) roda `deploy/server0.sh`.

Testes: a própria CI verde no primeiro push é o teste; mais um `tests/test_packaging.py` que confere
que `orqweb/server/static` entra no pacote e que o `Dockerfile` copia o que o `orq-web` importa.

### 2.2 Testes do claude-multiconta que não existem ainda

`usage-all` ganha testes com respostas da API gravadas (fixtures JSON): janelas, ritmo, ordenação,
`--json`. `install.sh` ganha um teste de shell que roda num `HOME` temporário e confere aliases e
`settings.json`. Pequeno, mas tira o projeto do zero de cobertura.

## Fase 3 — níveis (highlevel) dentro do orq-web  ✅ feita em 2026-10-11

O que mudou em relação ao planejado, descoberto no teste real com duas sessões `claude -p`:
- o `SendMessage` recusa nome com `@` ("to must be a bare teammate name"), então `-n` recebe o rótulo
  saneado (`config.session_name`: `kaggle@server0` → `kaggle-server0`); `{acima}`/`{abaixo}` nos papéis
  viram esses nomes, não os rótulos;
- a mensagem chega como `user` com `isReplay`/`isSynthetic` e `origin = {kind: "peer", name, body, msg_id}`;
  o supervisor carimba `orq_origin: "agent"`. O `tool_result` do SendMessage traz o `msg_id`, que a página
  usa para marcar o fio como entregue;
- o executor costuma responder para o endereço `uds:/tmp/cc-socks/<pid>.sock` em vez do nome; a página
  mostra como "resposta";
- cada mensagem de peer reabre o turno com um novo `system init` no stream (inofensivo);
- as receitas listam só os níveis que sobem ACIMA do agente da aba (ele é o executor, sem papel novo);
  não há `bootstrap`: a primeira mensagem da pessoa já vai ao professor, cujo papel manda delegar;
- o papel viaja pelo stdin do `orq-agent spawn --role-file -` (por ssh) e fica em `~/.cache/orq/<nome>/role.txt`;
- Python < 3.11 no servidor usa um leitor TOML mínimo (`pipelines._parse_minimal`), testado contra o `tomllib`.

### 3.1 O que muda no supervisor

- Todo agente da página sobe com `-n <rótulo>` (ex.: `kaggle@server0`), para ser endereçável pelo
  `SendMessage`. Agentes já abertos precisam ser reabertos uma vez.
- `orq-agent spawn` ganha `--role-file` (texto do papel, via `--append-system-prompt`) e `--parent`
  (rótulo do agente de baixo). O estado grava `level` e `parent`.
- `stamp_origin` ganha `agent`: mensagem `user` que chegou pelo `SendMessage` de outro agente (o
  JSONL marca a origem; confirmar o campo no teste).

### 3.2 Receitas

`orqweb/pipelines/*.toml`, portadas do highlevel (`professor-executor`, `professor-arquiteto-executor`).
`{acima}`/`{abaixo}` viram os rótulos reais. Lidas pelo servidor (Python 3.11+ tem `tomllib`); a
máquina do agente só recebe o texto do papel.

### 3.3 Botões

- **Upgrade** numa aba: escolhe a receita (ou a padrão), sobe o(s) nível(is) de cima na mesma máquina e
  pasta, com `--parent` apontando para o agente atual. A aba passa a conversar com o nível de cima; o
  agente original vira o executor, escondido atrás.
- **Downgrade**: encerra os níveis de cima; a aba volta a falar com o executor.
- A aba ganha o painel **fios**, como no highlevel: `SendMessage` do nível de cima (saída), chegada no
  de baixo (entrega), resposta de volta. Vem dos eventos que já passam pelo socket: `tool_use
  SendMessage` num stream e `user` com `orq_origin=agent` no outro.
- Cartões de **níveis**: estado de cada um (ocioso, trabalhando, pedindo permissão), clicável para ver
  o chat cru daquele nível.

### 3.4 Testes

- `fake_claude` aprende `SendMessage`/`ListAgents`: ao receber "delegue X", emite `tool_use SendMessage`
  e, num segundo processo falso, a mensagem chega como `user`. Com isso o teste de integração sobe
  executor e professor falsos pelo supervisor real e confere os fios ponta a ponta.
- Redutor (`events.js`): ações `wireOut`, `wireIn`, `levelState`; testes no Node.
- Handlers: `POST /api/agent/<nome>/upgrade {pipeline}` e `/downgrade`; recusa upgrade em agente de
  terminal e em agente que já é nível de cima.
- Verificação real: upgrade no `kaggle@server0`, pedir algo, ver o executor trabalhar nos fios,
  downgrade.

### 3.5 O que fica do claude-highlevel

O conceito, as receitas `.toml` e o desenho da tela (palco, fios, níveis). O código de PTY, tailer e
portões não é portado: o supervisor já entrega eventos estruturados e permissões sem raspar terminal.
O diretório `claude-highlevel/` fica no monorepo como referência histórica.

## Fase 4 — acabamentos e troca de conta  ✅ feita em 2026-10-11

Da lista levantada depois da fase 3, foram feitos: fios mostram o nível de cima quando o executor
responde ao endereço `uds:` (e o papel do professor pede resposta pelo nome `{eu}`); **↻ reabrir com
nome** no seletor de níveis (`reopen` + `spawn --restart`); permissão pendente de um nível respondida
no próprio cartão do painel; **troca automática de conta** quando a cota acaba (`state: quota` no
supervisor, `Migrator` no servidor, `retry` da última mensagem, `auto_switch` no accounts.json);
**notificações** do navegador e contagem no título da aba.

Ficam para depois: histórico de rodadas e linha do tempo de marcos; modelo/esforço por nível; receitas
editáveis pela página; um professor para vários executores; busca na lista de projetos; painel de níveis
melhor no celular; níveis na grade do `orq` do terminal; `orq-web --no-open`.

## Fase 5 — conjuntos: abrir vários agentes de uma vez, com ou sem professor  ✅ feita em 2026-10-13

O que mudou em relação ao planejado: o prompt de um agente fica no próprio `state.json` (`role`), não só em
`role.txt`, então a fotografia não precisa ler arquivo nas máquinas; `snapshot` devolve o JSON sem salvar
(a página mostra para editar e o `POST /api/conjuntos` salva); `lider.prompt` só vale para receitas de um
nível (as de dois ou mais usam os papéis do TOML); a receita da fotografia vem do `pipeline` gravado no
estado do nível pelo upgrade (inferida pelos nomes dos níveis e pelos vínculos só para níveis de antes desta versão),
porque a lista de vínculos oscila quando o `orq-agent links` da máquina falha sob carga. Achado no QA real: um professor cuja base foi
encerrada sem ↓ desfazer sobrevive e gruda no próximo agente de mesmo rótulo (a pasta é a mesma), e o ⇅ recusa
com "já tem níveis"; agora abrir um rótulo (pelo + ou por um conjunto) encerra esses níveis órfãos
(`handlers.close_orphan_levels`). Testes: `tests/test_sets.py`,
`tests/test_handlers.py` (conjuntos), `tests/test_supervisor.py` (permissão/papel/conjunto no estado) e
`qa/tests/test_conjuntos.py`.

Um **conjunto** é um JSON que descreve agentes para abrir juntos: os trabalhos que estavam em andamento
(cada um na sua pasta e máquina, conversa retomada) ou um grupo com um professor líder, em que cada agente
sobe com um system prompt próprio. Um botão **⊞ conjuntos** ao lado das abas lista os salvos, abre um,
recebe um JSON pronto, ou fotografa as abas abertas num conjunto novo. Vale também no `/m`.

Decisões tomadas com o autor em 2026-10-13: conjuntos ficam numa **pasta do servidor** editável pela página
(`~/.config/orq-web/conjuntos/*.json`, `ORQ_SETS_DIR`; no container, volume gravável); **dois modos**, sem líder
(agentes independentes) e com líder (professor sobre o primeiro agente, os outros vinculados a ele, receita
`professor-executores`, como o `upgrade` já faz); o JSON pode levar **tarefa inicial**, **conta fixa** e **modo de
permissão**; se qualquer agente do conjunto **já estiver aberto** (mesma máquina e pasta), a página **recusa e
avisa** quais, sem abrir nada.

### 5.1 O formato

```json
{
  "nome": "artigo-kaggle",
  "descricao": "modelo no server0, texto no node3, professor coordena",
  "permissao": "auto",
  "agentes": [
    {"maquina": "server0", "pasta": "~/Projects/kaggle", "modo": "resume",
     "conta": "work3", "prompt": "Você cuida do modelo…", "tarefa": "Retome de onde parou e reporte."},
    {"maquina": "node3", "pasta": "~/Projects/paper", "modo": "new",
     "prompt": "Você escreve o artigo…"}
  ],
  "lider": {
    "receita": "professor-executores",
    "prompt": "opcional: substitui o papel do TOML; aceita {executores}, {abaixo}, {eu}",
    "tarefa": "opcional: primeira mensagem ao professor"
  }
}
```

- `nome` é o id (nome do arquivo saneado). `descricao`, `permissao`, `conta`, `prompt`, `tarefa` e `lider` são
  opcionais. `modo` padrão: `resume` (a conversa mais recente da pasta; `new` se não houver).
- `permissao` vale para o conjunto inteiro (`auto` | `acceptEdits` | `manual`); sem ela, o `permission_mode` da
  máquina. Todos os agentes e níveis sobem no mesmo modo (modos diferentes retêm mensagens entre níveis).
- `conta` ausente: `assign` escolhe pela folga, para o conjunto inteiro de uma vez, o que já distribui a carga entre
  as contas (`PER_AGENT`) em vez de empilhar todos na mesma.
- `prompt` de um agente vai por `--append-system-prompt`, como o papel de um nível. A nota do `_outbox/` continua.
- Sem `lider`, cada agente é independente: nenhum vínculo é feito. Com `lider`, todos os demais são vinculados ao
  primeiro e o professor sobe acima dele; `lider.prompt` substitui `papel` do TOML (com os mesmos marcadores).
- Em `tarefa`, o texto é entregue como mensagem da pessoa assim que o agente confirma que subiu; com `lider`, a
  `tarefa` dos agentes é entregue antes da do professor.

### 5.2 Supervisor (`orq-agent spawn`)

- `--role-file` passa a valer para agente base, não só para nível: sem `--parent`/`--level`, é só o prompt. O
  `state.json` grava `role: true`, e o `--restart` (troca de conta, reabrir com nome) reaproveita o `role.txt`
  sempre que existir, não só quando há `parent`/`level`.
- `--permission-mode` novo, gravado no `state.json` e reaproveitado no `--restart`. Sem ele, o da máquina.
- `state.json` ganha `conjunto: <id>` (de que conjunto o agente veio), para a fotografia (5.4) e para a aba
  mostrar de onde ele veio.

### 5.3 Servidor (`orqweb/server/sets.py` e rotas)

| rota | o que faz |
|---|---|
| `GET /api/conjuntos` | catálogo: id, nome, descricao, nº de agentes, líder ou não, máquinas, `aberto` (quantos agentes dele já estão abertos) |
| `POST /api/conjuntos` `{json}` | valida e salva (cria ou substitui pelo `nome`); é o "enviar JSON" e o "salvar como conjunto" |
| `GET /api/conjuntos/<id>` | o JSON, para baixar ou editar fora |
| `POST /api/conjuntos/<id>/delete` | apaga o arquivo (o botão pede confirmação) |
| `POST /api/conjuntos/<id>/open` | abre: recusa (409, lista) se algum `maquina:pasta` já tem agente vivo ou claude de terminal; senão sobe tudo |
| `POST /api/conjuntos/snapshot` `{nome}` | fotografa as abas abertas (5.4) e salva |

Abertura (`/open`): a resposta volta na hora com o plano (rótulos, contas, porquês), e o trabalho roda numa thread
do servidor, porque abrir N agentes por ssh leva de 5 a 15 s cada. Os agentes entram como provisórios conforme sobem,
então as abas aparecem na página pelo `/api/state` normal. Ordem: `spawn` dos agentes (em paralelo entre máquinas
diferentes, **em sequência na mesma máquina**, porque ssh em rajada para a mesma máquina cai); depois, com líder,
`link` de cada um com o primeiro e `upgrade` com a receita e o `prompt` do líder (mesma rotina de hoje, fatorada do
handler para uma função); por fim, `tarefa`s entregues quando cada agente sai de `abrindo…` (espera até 90 s). O
andamento vai em `/api/state.conjuntos_abrindo` (`{id, etapa, feitos, erros}`) e a página mostra como toast; falha num
agente não desfaz os outros, só é listada.

Validação (`sets.validate`): campos conhecidos, `maquina` entre as configuradas, `conta` entre as contas, `permissao`
entre os modos, `modo` em `resume|new`, `lider.receita` no catálogo, pelo menos um agente, `nome` saneável e sem
colisão de `maquina:pasta` dentro do próprio conjunto.

### 5.4 Fotografia das abas abertas

`snapshot` monta um conjunto a partir do estado atual: um agente por aba (máquina, pasta, conta atual, `modo:
resume`, `prompt` se o agente subiu com `role.txt` — o servidor lê pelo `orq-agent cat` — e `permissao` do
`state.json`); se a aba atual tem níveis acima, vira `lider` com a receita registrada em `level` e os vinculados a ela
entram como agentes; o restante das abas entra como independente. A página pede o nome, mostra o JSON resultante
numa prévia editável e só salva ao confirmar.

### 5.5 Página

- Botão **⊞ conjuntos** na faixa das abas (ao lado da cota) e, no `/m`, na gaveta. Abre o seletor já existente
  (`picker`) com uma linha por conjunto: nome, descrição, `N agentes · máquinas · 👨‍🏫 líder`, e à direita **abrir**,
  **⬇ JSON**, **✕**. Linha de cima: **📄 enviar JSON** (input de arquivo, valida no servidor e lista o erro com a
  linha) e **📷 salvar abas abertas**.
- Abrir com algum agente já aberto: toast vermelho "já aberto: kaggle@server0, paper@node3; feche-os ou abra
  só os demais à mão", sem abrir nada.
- Durante a abertura, toasts de andamento (`conjuntos_abrindo`); ao terminar, `show()` do primeiro agente (ou do
  topo do líder).
- A aba de um agente vindo de conjunto mostra `⊞ nome` no estado da barra inferior.

### 5.6 Testes

- `tests/test_sets.py`: validação (cada erro), catálogo com arquivo quebrado pulado, `plan` (rótulos, `assign`
  do conjunto inteiro, marcadores do líder), recusa por agente já aberto, snapshot a partir de um estado montado.
- `tests/test_handlers.py` + `fake_claude`: abrir conjunto de dois sem líder → dois `spawn` com `--role-file`
  e `--permission-mode`, nenhum `link`; com líder → dois `spawn`, um `link`, um `upgrade` com papel
  substituído, `tarefa`s entregues na ordem; `delete` e `POST` com JSON inválido (400 com motivo).
- `tests/test_supervisor.py`: `spawn --role-file` sem nível grava `role: true`; `--restart` reaproveita papel
  e `--permission-mode`.
- QA (`qa/tests/test_conjuntos.py`): enviar JSON, abrir, ver abas, salvar abas abertas, apagar.
- Verificação real: conjunto com `kaggle@server0` + uma pasta no node3 e professor; tarefa inicial chega.

### 5.7 Docs

`docs/04-orq-web-page.md` ganha 4.12 "Conjuntos"; `docs/07-reference.md` as rotas e o esquema do JSON;
`docs/05-architecture.md` a sequência "abrir um conjunto" em 5.5 e `ORQ_SETS_DIR` em 5.4.

Tamanho estimado: ~900 linhas (sets.py ~200, handlers ~150, supervisor ~60, app.js ~200, testes ~300), mais docs.
Ordem sugerida: supervisor (5.2) → sets.py e rotas sem líder → página → líder e tarefas → snapshot → QA e docs.

## Fase 6 — histórico de uso: coletor a cada 5 min, banco SQLite e comandos que calculam sobre ele  ✅ feita em 2026-10-22

**Por quê.** A API de cota (`/api/oauth/usage`, não documentada) só diz a porcentagem *de agora* de cada janela. Sem
guardar amostras não existe "quanto gastei hoje", "a que ritmo", "quando acaba" nem "quanto um ponto de cota vale
em tokens". E o `usage-all`, a página do orq e a troca automática de conta consultam a API cada um por si, o que
leva a 429 (`rate_limit_error`) em rajadas. Um coletor só, a cada 5 minutos, resolve as duas coisas.

Decisões (2026-10-22, padrões assumidos; o autor revisa depois de usar): banco **só no node4** (sempre ligado; o
laptop consulta por ssh); guarda **tudo** (mensagens e cota; retratos de processos por 14 dias); a página do orq
continua lendo o `usage-all --json`, que passa a vir do banco (integração própria fica para 6.8).

### 6.1 Peças

| peça | onde | o que faz |
|---|---|---|
| `bin/usage-hist` (novo, claude-multiconta) | node4 | coletor (`coletar`), instalador do timer (`instalar`/`remover`/`estado`) e todos os relatórios (`painel`, `dia`, `semana`, `calibrar`, `alertas`); também a biblioteca de banco e cálculos que o `usage-all` e o `usage-sessoes` carregam |
| `usage-coletor.timer` (systemd de usuário) | node4 | roda `usage-hist coletar` a cada 5 min, sobrevive a reboot (linger), log no journal |
| `~/.local/share/claude-multiconta/usage.db` (`USAGE_DB`) | node4 | SQLite em WAL; um escritor (o coletor, com trava), vários leitores |
| `usage-sessoes --agente --desde T` | cada máquina | modo do agente que devolve as respostas (uma linha por mensagem) desde T, mais prompts e processos |
| `usage-all`, `usage-sessoes` | qualquer máquina | leem o banco quando ele tem menos de 10 min (`--ao-vivo` força a API/varredura); do laptop, por ssh ao node4 (`usage_db_host` no accounts.json) |

### 6.2 Coleta (a cada 5 min)

1. **Cota**: `get_windows` do `usage-all` para cada conta, em paralelo; cada janela (`session`, `weekly_all`,
   `weekly_scoped` por modelo) vira uma amostra `(ts, conta, tipo, escopo, %, resets_at)`. Erro (429, token) vai
   para `quota_err` e não interrompe as outras contas. Com 429, a próxima rodada espera em dobro (até 30 min).
2. **Consumo**: em todas as máquinas, em paralelo, uma conexão ssh cada (o script vai pelo stdin, como o
   `usage-sessoes`), o agente devolve as respostas desde a última coleta bem-sucedida daquela máquina menos 15 min
   (sem estado na máquina: a sobreposição é deduplicada pela chave da mensagem). Primeira coleta: 7 dias para
   trás (`--desde-horas`), para a calibração já ter dados. Máquina fora do ar: na volta, a janela cobre o buraco,
   porque as conversas ficam gravadas nela. A cota, ao contrário, não se recupera: buraco de cota fica marcado.
3. **Processos**: o retrato dos processos do claude vivos em cada máquina (conta, pasta, tipo, tmux, quem lançou).
4. **Saúde**: uma linha por máquina por rodada (`runs`: ok/erro, duração), para o `estado` e os alertas.

### 6.3 Esquema

```
quota(ts, account, kind, scope, pct, resets_at, severity)        PK (ts, account, kind, scope)
quota_err(ts, account, err)
messages(key PK, ts, host, account, session, project, cwd, entrypoint, model,
         input, output, cache_new, cache_read, sub)              key = id da mensagem | requestId
sessions(account, session PK, host, project, cwd, entrypoint, prompt, first_ts, last_ts)
processes(ts, host, pid, account, kind, cwd, tmux, parent, session_arg, seconds, last_activity)
runs(ts, host, ok, err, seconds)
meta(key PK, value)                                              versão do esquema, último 429
```
Sem texto das conversas: só números, sessão, pasta, modelo e o começo do prompt das sessões (o que identifica um
script). Tamanho esperado: poucos MB por mês.

### 6.4 Cálculos

- **Consumo no intervalo** (`consumed`): soma das subidas entre amostras consecutivas da mesma janela; se o
  `resets_at` mudou ou a % caiu, houve reset e conta a % nova a partir de zero. Vale para 5h e 7d.
- **Hoje** por conta: pontos da semana (7d) consumidos desde a meia-noite local, e pontos da janela de 5h.
- **Ritmo**: pontos/h na última hora e nas últimas 6 h; **previsão**: horas até 100% no ritmo de 6 h, comparada ao
  reset ("acaba antes do reset" vira alerta); **frota**: pontos livres somados e em quantos dias acabam no ritmo
  de 24 h.
- **Calibração tokens → pontos**: por conta, nos últimos 7 dias, `peso dos tokens / pontos de 7d consumidos`
  (o peso pondera entrada 1, cache novo 1,25, cache lido 0,1, saída 5). Com isso o `usage-sessoes` e o `dia`
  mostram **pontos estimados por sessão e por projeto**, não só a fatia relativa. Só com 5+ pontos de amostra.
- **Perfil**: consumo por hora do dia e por dia da semana (quando você usa).
- **Alertas** (`usage-hist alertas` e no painel):
  - *subiu por script*: 5+ pontos de 5h em 30 min, 80%+ vindos de sessões `sdk/-p` (o caso do `claude-diario`);
  - *subiu sem consumo nas máquinas*: a cota subiu e os tokens vistos explicam menos de 20% (claude.ai, celular
    ou máquina fora do ar);
  - *acaba antes do reset*: previsão de 7d ou 5h antes do horário de reset;
  - *processo esquecido*: processo do claude parado há mais de 1 dia;
  - *coletor parado*: última coleta há mais de 15 min, ou máquina sem resposta há mais de 1 h.

### 6.5 Comandos

| comando | o que mostra |
|---|---|
| `usage-hist` | painel: por conta, 5h e 7d agora, **hoje**, ritmo, previsão, **barras das últimas 24 h**; frota; alertas; idade da última coleta |
| `usage-hist dia [AAAA-MM-DD]` | o dia: pontos por conta, por hora (barras) e os projetos/sessões que mais gastaram, em pontos estimados |
| `usage-hist semana` | 7 dias × contas em pontos, total por dia, perfil por hora do dia |
| `usage-hist calibrar` | tokens por ponto de cada conta e o tamanho da amostra |
| `usage-hist alertas` | só os alertas (para um cron de aviso, depois) |
| `usage-hist coletar` / `instalar` / `remover` / `estado` | coleta uma vez; instala/remove o timer; saúde do coletor e do banco |
| `usage-all` | o de sempre, agora do banco (instantâneo, sem 429), com "do banco · há N min" no rodapé; `--ao-vivo` |
| `usage-sessoes` | o de sempre, agora do banco, com **pontos estimados** por linha; `--ao-vivo` |

Todos com `--json`. No laptop, sem banco local, os comandos rodam no node4 por ssh (`usage_db_host`), e o
`usage-all` cai na API se o ssh não responder em 8 s.

### 6.6 Testes

- **Unidade** (`tests/test_usage_hist.py`): esquema e inserção idempotente; `consumed` com subidas, reset por
  `resets_at` e queda de %; hoje com fuso local; ritmo, previsão e frota; calibração; cada alerta; agente
  `--desde` (janela, dedupe, subagente, prompt).
- **Interface**: cada relatório (`painel`, `dia`, `semana`, `calibrar`, `alertas`, `estado`) gerado sobre um banco
  de exemplo, conferindo as linhas da tabela; `coletar` ponta a ponta com a API e o ssh falsos; `usage-all` e
  `usage-sessoes` lendo o banco fresco, caindo para ao vivo com banco velho, e com `--ao-vivo`.
- **Real**: timer instalado no node4, 3 rodadas, comandos no node4 e no laptop.

### 6.7 Deploy

`usage-hist` nos dois lugares (`~/.local/bin`), `usage-all` e `usage-sessoes` novos; `usage_db_host: "node4"` no
accounts.json (repositório e laptop); `usage-hist instalar` no node4 com a primeira coleta de 7 dias.

### 6.8 A página do orq lendo o banco  ✅ feita em 2026-10-22

O servidor roda `usage-hist painel --json` a cada 60 s com a página aberta (na máquina da cota, por ssh no
container; `External.history`, `StateCache.refresh_history`) e manda o resultado no `/api/state` como `history`.
Os cartões de conta ganham as barras das últimas 24 h (SVG, uma por hora, escala comum, tooltip por hora) e o
resumo hoje · ritmo · previsão (vermelho com ⚠ quando acaba antes do reset); o TOTAL, os pontos de 24 h e o
aviso de coleta parada; os alertas ⚠ viram toast, um a um a cada 6 h (lembrados no navegador). Funções puras em
`format.js` (`sparkSvg`, `histSummary`, `freshAlerts`) com testes no Node. De quebra, no celular o botão de
conjuntos deixou de cobrir o título do TOTAL.

### 6.9 Painel da conta  ✅ feito em 2026-10-23

Clicar no cartão de uma conta abre o painel dela: cinco números (semana, 5 h, hoje, ritmo, previsão), a curva da
semana até o reset com o ritmo ideal e a projeção no ritmo atual, as 24 h por hora, os 7 dias e o que mais gastou
(24 h ou 7 dias, com ≈pts). `usage-hist conta NOME [--json]`, `GET /api/conta/<nome>` (cache de 30 s), gráficos em
`static/js/charts.js` (SVG puro, testes no Node). De quebra: o servidor ignora um segundo SIGTERM durante o
encerramento (o `uv run` repassa o seu, e a segunda interrupção caía no meio da limpeza).

### 6.10 Selo de alerta no cartão  ✅ feito em 2026-10-23

O `usage-hist` passa a entregar os alertas estruturados (`alert_items`: conta, tipo, `warn`/`info`, rótulo curto,
texto); a página põe um selo no cartão da conta enquanto o alerta durar (vermelho para ⚠, cinza para aviso; título
com o texto) e a contagem de ⚠ da frota no TOTAL. O toast continua. `badgeFor`/`fleetBadge` em `format.js`, com
testes no Node.

### 6.11 Aba "uso"  ✅ feita em 2026-10-23

📊 uso abre, na janela do painel, a tabela do `usage-sessoes` a partir do banco: 5 h, 24 h ou 7 dias; por conta, a
cota e uma linha por máquina + pasta + tipo (parte, ≈pts, agora, processo vivo) e os processos esquecidos; linhas
de agentes abertos levam à aba deles. O agrupamento saiu do `render_brief` para `usage-sessoes.brief_groups`, que o
terminal e a página usam (`usage-hist uso`, `GET /api/uso`, `static/js/usage.js` com testes no Node).

### 6.12 Depois (não nesta rodada)

A troca automática de conta usando a previsão para trocar *antes* de acabar; aviso por notificação (ntfy/e-mail)
dos alertas; relatório semanal automático.

---

## Estimativa e ordem

| fase | tamanho | dependências |
|---|---|---|
| 1 login + bind + container + deploy | ~500 linhas + Dockerfile/compose | nenhuma |
| 2 monorepo + CI/CD | pouco código, muita configuração | fase 1 (para a imagem valer a pena) |
| 3 níveis | ~800 linhas (supervisor, servidor, página, fake_claude, receitas) | fase 1 (reabrir agentes com `-n`) |

Riscos conhecidos: no container, o `laptop` só é alcançável se estiver ligado e com ssh pelo ZeroTier;
mensagens entre níveis em classes de permissão diferentes ficam retidas (o highlevel já documenta),
por isso todos os níveis sobem no mesmo `permission_mode`.
