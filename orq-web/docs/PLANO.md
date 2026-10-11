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

`https://github.com/cmarcond/orq` (privado). CI por projeto verde; imagem `ghcr.io/cmarcond/orq-web:latest`.
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
