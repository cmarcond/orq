# 2. Instalação e deploy

## Pré-requisitos

| onde | o que |
|---|---|
| toda máquina | `python3` (3.8 ou mais; 3.9+ para horários em São Paulo), `curl`, `ssh`; `tmux` recomendado (sem ele os agentes rodam com `nohup`, sem reconexão) |
| laptop | ssh por chave para todas as máquinas de `orq_hosts`, com os nomes de `~/.ssh/config`; `tmux`; iTerm2 para os alertas do `orq` de terminal |
| server0 (servidor da página) | Docker com o plugin compose, interface ZeroTier, chave `~/.ssh/id_ed25519` que abra as outras máquinas e o laptop |
| Mac que recebe agentes por ssh | `sshd` ligado, `~/.zshenv` com `~/.local/bin` e `/opt/homebrew/bin` no `PATH`, servidor tmux nascido na sessão gráfica (ver abaixo) |

O `claude` e o `codex` são instalados pelo `install.sh` do claude-multiconta se faltarem.

## Ordem numa máquina nova

```sh
# 1. contas e utilitários
./claude-multiconta/install.sh            # instala claude/codex se faltarem, copia bin/*, gera os aliases
exec zsh                                  # ou abra um terminal novo
claude-work-login; claude-gmail-login  # um login por conta, colando o código (sem navegador)
usage-all                                 # confere: uma linha por conta + TOTAL

# 2. o supervisor e a página
./orq-web/install.sh                      # ~/.local/lib/orq-web + comandos orq-agent, orq-web, converte-web
```

Para as outras máquinas, do laptop:

```sh
./claude-multiconta/make-tarball.sh       # ~/Projects/claude-multiconta-AAAAMMDD.tar.gz (sem credenciais)
scp ~/Projects/claude-multiconta-*.tar.gz server0:
ssh server0 'tar xzf claude-multiconta-*.tar.gz && ./claude-multiconta/install.sh --no-install'
./orq-web/deploy.sh                       # laptop + todas as de orq_hosts; ou: ./orq-web/deploy.sh server0 node3
```

`deploy.sh` empacota `orqweb/` e `install.sh`, copia por `scp` e roda o `install.sh` lá. Máquina fora
do ar aparece como `✗ falhou`; repita quando ela voltar. Os dois instaladores são idempotentes.

**O que precisa estar em cada máquina, e por quê**

| item | laptop | máquinas remotas | container |
|---|---|---|---|
| `claude` logado nas contas | sim | sim (login por máquina) | não |
| `claude-multiconta` (`usage-all`, `claude-last`, `orq`) | sim | sim: o servidor lista projetos e agentes por eles, e lê a cota em `ORQ_USAGE_HOST` | não |
| `orq-agent` | sim (o container abre agentes aqui) | sim | não |
| `orq-web` (servidor) | instalado, mas não fica no ar | não | sim (imagem) |

Trocar o `usage-all` (ex.: a linha TOTAL) exige reinstalar o claude-multiconta na máquina que responde a
cota ao servidor (`ORQ_USAGE_HOST`, hoje o server0). Trocar o `orq-agent` (ex.: `ls`, `cat`, `link`)
exige o `deploy.sh` em todas as máquinas, senão a página avisa "orq-agent antigo".

## Contas

Uma conta nova:

```sh
cd claude-multiconta
./add-account.sh conta3 fulano@exemplo.com "Fulano (exemplo.com)"
# num terminal novo:
claude-conta3-login
```

Isso registra a conta no `accounts.json` com `config_dir: ~/.claude-conta3`, cria o diretório e regera
os aliases. O `usage-all`, o `orq` e a página passam a considerá-la. Depois, `make-tarball.sh` e
reinstalação nas outras máquinas, para o `accounts.json` ficar igual em todas; cada máquina faz o
próprio login. Remover dos aliases, sem apagar nada: `./add-account.sh --remove conta3`.

O login nunca abre navegador: o alias passa `BROWSER=claude-login-url`, que só imprime a URL. Abra-a
logado na conta certa (ou em janela anônima) e cole o código. O motivo está no capítulo 3.

## A página pela rede: container no server0

```sh
./orq-web/deploy/push-server0.sh        # do laptop
```

O script manda o código por ssh (um tarball pelo stdin, numa conexão só) e roda
`deploy/server0/deploy.sh` lá, que:

1. gera a senha em `~/.config/orq-web/password` se não houver (`openssl rand`), modo 600;
2. garante ssh da máquina para ela mesma (a chave em `authorized_keys`), porque dentro do container o
   próprio server0 é "remoto";
3. escreve `~/.config/orq-web/ssh_config`, um `Host` por máquina de `orq_hosts` mais `laptop` e o
   próprio host, com os IPs resolvidos ali (o container não vê o `/etc/hosts` nem o `~/.ssh/config` do
   host), `StrictHostKeyChecking accept-new` e um `known_hosts` próprio;
4. sobe o `docker compose` com a imagem `ghcr.io/cmarcond/orq-web:latest` publicada pela CI (se o
   Docker estiver logado no GHCR) ou construída ali (`ORQ_BUILD_LOCAL=1` força construir com o código
   enviado, útil antes de o commit chegar à `main`);
5. publica a porta 8765 só no IP do ZeroTier.

A imagem é `python:3.12-slim` com o cliente ssh; o usuário dentro dela tem o mesmo uid e home do usuário
do host, para montar `~/.ssh` (somente leitura), `~/.config/claude-multiconta` (somente leitura) e
`~/.config/orq-web` (senha, `ssh_config`, estado). Variáveis fixas na imagem: `ORQ_BIND=0.0.0.0`,
`ORQ_LOCAL_HOST=none` (nenhum agente local; tudo por ssh), `ORQ_STATE_DIR`, `ORQ_PASSWORD_FILE`,
`ORQ_SSH_CONFIG`, `CLAUDE_MULTICONTA_CONF`. Do compose: `ORQ_ALLOWED_HOSTS` (IP do ZeroTier e o
hostname), `ORQ_EXTRA_HOSTS=laptop`, `ORQ_USAGE_HOST=<hostname>`. Um `HEALTHCHECK` pede `/login` a
cada 30 s.

Entrar: `http://<ip zerotier>:8765/`, senha `ssh server0 cat ~/.config/orq-web/password`. O cookie de
sessão dura enquanto o servidor viver.

Atualizar depois de um commit na `main`: espere o workflow `orq-web` publicar a imagem (uns 3 minutos) e
rode `push-server0.sh` de novo; ele recria o container. `docker ps` no server0 mostra `healthy` em
~30 s.

## Mac como máquina remota

O Claude Code guarda a credencial no Keychain, que não se abre para sessões ssh. Um Mac que recebe
agentes por ssh precisa de um servidor tmux nascido na sessão gráfica, para os processos herdarem o
Keychain destravado:

```sh
./orq-web/deploy/macos-tmux-keep.sh       # LaunchAgent de usuário que mantém uma sessão `keep` desde o login
```

Os agentes entram nesse servidor, e a cota remota é lida com `tmux run-shell "usage-all --json"`. Também
é preciso `sudo systemsetup -setremotelogin on` e um `~/.zshenv` com o `PATH` (sessões ssh não leem o
`.zshrc`). `--remove` desfaz.

## Desenvolver a partir do server0

Desde 2026-10-13 o ambiente de desenvolvimento fica no server0, não no laptop:

```sh
ssh server0
cd ~/Projects/claude-orq            # clone por https com a credencial do gh (`gh auth setup-git`)
claude-work3                     # ou outra conta: abre o Claude Code numa sessão tmux claude-work3-claude-orq
cd orq-web && make test && make lint   # venv em .venv (uv, Python 3.12); node 22 do sistema
.venv/bin/python -m pytest qa/tests/test_smoke.py   # QA aqui mesmo: Chromium do Playwright 1.48
```

- O `~/.bashrc` de lá exporta `ORQ_LOCAL_HOST=server0`, `ORQ_EXTRA_HOSTS=laptop`, `ORQ_QA_PROJECT=server0:~/Projects/orq-qa`
  e `ORQ_QA_PROJECT2`: um `orq-web` local ou do QA no server0 trata a própria máquina como local e alcança o
  laptop por ssh (`ssh laptop` funciona de lá).
- `orq-web/deploy.sh` sem argumentos instala localmente na máquina atual e, nas outras, por ssh; `laptop` só é
  local no Mac. `deploy/push-server0.sh` rodado no próprio server0 faz ssh para ele mesmo, o que já está
  autorizado pelo deploy do container.
- O Playwright mais novo não traz navegador para Ubuntu 20.04: o venv do server0 usa `playwright==1.48.0`
  (`uv pip install --python .venv/bin/python playwright==1.48.0 && .venv/bin/python -m playwright install --with-deps chromium`).
  Nessa versão o `page.wait_for_function` só serve quando o predicado já é verdadeiro de cara: para sondar,
  ele usa `eval` dentro da página e a CSP (sem `unsafe-eval`) barra. Por isso o harness (`qa/orqqa.py`) espera
  com `Orq.wait_fn`, que avalia pelo CDP a cada 300 ms; os testes de QA usam só ela. Para rodar o QA aqui:
  `ORQ_LOCAL_HOST=server0 ORQ_EXTRA_HOSTS=laptop ORQ_QA_PROJECT=server0:~/Projects/orq-qa
  ORQ_QA_PROJECT2=server0:~/Projects/orq-qa2 .venv/bin/python -m pytest qa/tests/test_conjuntos.py`.
- A memória do Claude Code deste projeto foi copiada para cada conta do server0
  (`~/.claude*/projects/-home-user-Projects-claude-orq/memory/`). O clone do laptop ficou, mas não é mais usado.
- Alternativa sem terminal: abrir `server0:~/Projects/claude-orq` pelo **+** na própria página e desenvolver
  pelo chat, com o agente rodando no server0.

## Variáveis de ambiente do servidor

| variável | padrão | efeito |
|---|---|---|
| `ORQ_BIND` | `127.0.0.1` | endereço de escuta; fora de `127.0.0.1` exige a senha |
| `ORQ_PASSWORD_FILE` | `~/.config/orq-web/password` | senha do login; sem o arquivo, só o token do link |
| `ORQ_ALLOWED_HOSTS` | `127.0.0.1,localhost` | cabeçalhos `Host` aceitos, além do `ORQ_BIND` |
| `ORQ_LOCAL_HOST` | `laptop` | nome desta máquina na página; `none` = sem agentes locais (container) |
| `ORQ_EXTRA_HOSTS` | vazio | máquinas além de `orq_hosts` (o container acrescenta `laptop`) |
| `ORQ_SSH_CONFIG` | vazio | `ssh -F` próprio (o container) |
| `ORQ_USAGE_HOST` | vazio | máquina que responde `usage-all --json` por ssh; vazio = esta |
| `ORQ_STATE_DIR` | `~/.cache/orq` | estado dos agentes e dos vínculos |
| `ORQ_SETS_DIR` | `~/.config/orq-web/conjuntos` | os conjuntos (⊞) salvos pela página |
| `ORQ_BIN` | `~/.local/bin` | onde estão `usage-all`, `claude-last`, `orq`, `orq-agent` |
| `CLAUDE_MULTICONTA_CONF` | `~/.config/claude-multiconta/accounts.json` | as contas |
| `ORQ_NO_OPEN` | vazio | `1` não abre o navegador ao subir |

## CI/CD

Um workflow por subprojeto, disparado só por mudanças na pasta dele:

- **claude-multiconta**: sintaxe de todos os scripts (`py_compile` ou `sh -n`), `accounts.json` válido,
  pytest do `usage-all` e do `claude-last`, em Python 3.8 e 3.12.
- **orq-web**: `ruff check`, `ruff format --check` e pytest em 3.8, 3.11 e 3.12; `node --test` em Node 22;
  depois constrói a imagem, faz um teste de fumaça (sobe o container com um `accounts.json` vazio, pede
  `/login` e confere que `/api/state` sem senha dá 403) e, só em `push` na `main`, publica
  `ghcr.io/cmarcond/orq-web:latest` e `:<sha curto>`.
- **claude-highlevel**: pytest rápido, por ser referência histórica.

O código do supervisor roda nas máquinas remotas, algumas com Python antigo: por isso tudo em
`orqweb/` fica compatível com 3.8 (a CI garante).
