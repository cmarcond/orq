# claude-multiconta

Esquema para usar **várias contas do Claude Code** (e o Codex) na mesma máquina, cada uma
isolada, com aliases para abrir e logar cada conta e um `usage-all` que mostra o uso de todas
com um marcador de ritmo. Feito para levar de máquina em máquina: Mac (zsh) e Linux (bash).

## Comandos, num relance

| comando | o que faz |
|---|---|
| `claude` | o Claude Code na conta padrão (`default_account`, hoje Work), numa sessão tmux nova `claude-<pasta>` |
| `claude-work`, `claude-gmail`, … | abre o Claude Code naquela conta, numa sessão tmux nova (`claude-<pasta>` para a padrão, `claude-<conta>-<pasta>` para as outras) |
| `claude-work-login`, `claude-gmail-login`, … | faz login naquela conta (imprime link, você cola o código) |
| `claude-usage-work`, `claude-usage-gmail`, … | uso daquela conta, com barra e ritmo |
| `claude-whoami` | qual e-mail/plano está em cada conta e qual delas é o `claude` puro |
| `codex-login` | login do Codex por *device auth* (sem navegador local) |
| `codex-usage` | uso do Codex (comando próprio; não entra no `usage-all`) |
| `usage-all` | uso de todas as contas do Claude, **resumido**: uma linha por conta e, por janela, % usado, mini-barra com marcador de ritmo e tempo até o reset |
| `usage-all -v` | o mesmo, **completo**: barra por janela, marcador de ritmo e horário do reset |
| `usage-all gmail` | só as contas citadas (nomes do `accounts.json`); combina com `-v` |
| `TOTAL` (última linha) | consolidado da semana entre as contas: % livre, o equivalente em contas inteiras, ritmo, em quanto tempo acaba tudo no ritmo atual e o próximo reset semanal; no `--json` vem na chave `_total` |
| `orq` | **orquestrador**: escolhe até 4 projetos de qualquer máquina e abre uma janela com 4 painéis, cada um no ssh, no tmux e na conta certa |
| `orq web` | atalho para o `orq-web` (projeto `orq-web`): página local com cota, abas e chat com os agentes |
| `claude-last` | lista, para a pasta atual, a conversa mais recente de cada conta, da mais nova para a mais velha |
| `claude-resume` | retoma a conversa mais recente da pasta atual **na conta certa**; `claude-resume 2` abre a linha 2 do `claude-last` |
| `claude-resume --as gmail` | retoma a mesma conversa **usando outra conta** (ex.: a original acabou a cota) |
| `tmux ls`, `tmux attach -t claude-<pasta>` | voltar a uma sessão do Claude (attach é manual) |
| `./add-account.sh nome email "rótulo"` | cria uma conta nova e gera os três aliases dela |
| `./add-account.sh --remove nome` | tira a conta dos aliases (sem apagar nada) |
| `./install.sh` | instala ou atualiza tudo nesta máquina, baixando `claude` e `codex` se faltarem (`--no-install` pula) |
| `./install.sh --uninstall` | remove scripts e aliases (as contas ficam) |
| `./make-tarball.sh` | empacota o projeto para enviar a outra máquina |

Os aliases de cada conta são gerados a partir do `accounts.json`, então uma conta nova ganha
os seus três aliases e entra no `usage-all` automaticamente.

## Como funciona

### 1. Uma conta por diretório

O Claude Code guarda login, histórico e configuração no diretório apontado pela variável
`CLAUDE_CONFIG_DIR`. Sem a variável, ele usa `~/.claude`. O truque todo é dar a cada conta
o seu próprio diretório:

| conta | diretório | como o alias abre |
|---|---|---|
| `work` | `~/.claude` (padrão) | `env -u CLAUDE_CONFIG_DIR claude` |
| `gmail` | `~/.claude-gmail` | `CLAUDE_CONFIG_DIR=~/.claude-gmail claude` |

Cada terminal pode estar numa conta diferente ao mesmo tempo, e o login de uma nunca
sobrescreve a outra. Note que a conta padrão é aberta **removendo** a variável em vez de
apontar para `~/.claude`: se a variável apontar explicitamente para `~/.claude`, o Claude Code
procura a credencial com outro nome e aparece como deslogado.

### 2. `claude` puro e as sessões tmux

`claude` sem nada abre a conta padrão (`default_account` no `accounts.json`, hoje `work`).
E tanto ele quanto `claude-<conta>` abrem o Claude **numa sessão tmux nova**, para você poder
fechar o terminal, cair a conexão, e depois voltar exatamente onde estava:

```sh
cd ~/Projects/foo
claude                  # sessão tmux "claude-foo" com a conta Work
claude-gmail           # sessão tmux "claude-gmail-foo" com a conta Gmail
claude                  # de novo na mesma pasta: "claude-foo-2"
tmux ls                 # lista as sessões
tmux attach -t claude-foo
```

- O nome é `<prefixo>-<pasta atual>`, com `-2`, `-3`… se já existir. Caracteres estranhos
  viram `-`. A conta padrão usa o prefixo `claude`, as outras `claude-<conta>`, então
  `tmux attach -t claude-<pasta>` funciona igual ao esquema antigo.
- O attach é manual, de propósito: `tmux ls` mostra o que está aberto.
- Já **dentro** de uma sessão tmux, ou numa máquina sem tmux, o comando roda direto.
- Argumentos passam normalmente: `claude --resume`, `claude-gmail -p "pergunta"`.
- Subcomandos rápidos **não** abrem tmux: `auth`, `mcp`, `config`, `doctor`, `update`, `install`,
  `plugin`, `agents`, `-p`/`--print`, `--version`, `--help`. Então `claude auth status` responde
  na hora, no terminal atual.

Para saber qual conta o `claude` puro está usando, e o que há em cada diretório:

```sh
claude-whoami
#   claude-work  ~/.claude          ✓ you@example.org  (team, Work)  ← `claude` puro
#   claude-gmail   ~/.claude-gmail   ✓ you@example.com  (max, ...)
claude auth status           # o mesmo, só para o `claude` puro (JSON com email e configDirectory)
```

Dentro do Claude Code, `/status` também mostra a conta.
- Para desligar o tmux em alguma máquina, `"tmux": false` no `accounts.json` e `./install.sh`.
- O `install.sh` também grava `"tui": "default"` no `settings.json` de **cada conta**: é o
  renderizador que escreve a conversa no scrollback nativo, então o tmux (`prefix + [`, PgUp,
  roda do mouse) enxerga o histórico inteiro. O outro modo, `fullscreen`, guarda a conversa num
  buffer próprio e ela some depois de uma tela. Equivale a digitar `/tui default` em cada conta.
  Para outro valor, `"tui": "fullscreen"` no `accounts.json`; para o instalador não mexer nisso,
  `"tui": false`. Sessão já aberta só muda depois de reiniciar o `claude`.

Todos os aliases chamam o binário via `env`, que resolve `claude` pelo `PATH` e ignora a
função, então não há recursão nem risco do `claude-gmail` cair na conta padrão.

### 3. Login sem navegador

O `claude auth login` normalmente abre o navegador padrão, e o navegador autoriza a conta que
**já estiver logada** no claude.ai, seja qual for o e-mail pré-preenchido. Foi assim que uma
conta já sobrescreveu a outra uma vez.

Por isso os aliases de login passam `BROWSER=~/.local/bin/claude-login-url`, um script que
não abre nada: só imprime a URL. Você abre a URL onde quiser, logado na conta certa (ou em
janela anônima), autoriza, e cola o código que aparece de volta no terminal. Funciona igual
numa máquina com desktop e num servidor por SSH.

```sh
claude-gmail-login
# >>> Abra esta URL logado na conta certa (ou em janela anônima) e cole o código aqui:
#     https://claude.ai/oauth/authorize?...
# Paste code here if prompted > ...
```

O Codex tem o mesmo fluxo nativo: `codex-login` roda `codex login --device-auth`, que mostra
um código para você confirmar no site.

Para conferir em qual conta cada diretório está: `claude-gmail auth status`.

**O onboarding é pulado de propósito.** O `claude auth login` grava a credencial, mas não marca
o assistente de primeira execução (tema, dicas, login) como concluído. Sem essa marca, o primeiro
`claude-<conta>` abre o assistente, que termina num **segundo** login pelo navegador, com callback
em localhost. Esse login é redundante e quebra com `Redirect URI ... not supported`. Por isso o
`install.sh` grava `hasCompletedOnboarding: true` (e `theme: dark`, se não houver tema) no
`.claude.json` de cada conta: `~/.claude.json` para a conta em `~/.claude`, `<config_dir>/.claude.json`
para as outras. A mudança é idempotente e não mexe em quem já tem a marca. Se aparecer esse erro numa
conta, rode o `./install.sh` de novo.

### 4. Uso com marcador de ritmo

`usage-all -v` consulta a API de uso da Anthropic com a credencial de cada conta e
desenha, para cada janela de limite, uma barra de 20 células:

```
⏱  Sessão 5h            30% [██████░░░░░░░░┃░░░░░]  reseta 25/09 12:30  ritmo  73% (▼-43 abaixo do ritmo)
📅 Semanal 7d (todos)    30% [███┃██░░░░░░░░░░░░░░]  reseta 01/10 03:00  ritmo  19% (▲+11 acima do ritmo)
🔮 Semanal 7d só Fable   26% [███┃█░░░░░░░░░░░░░░░]  reseta 01/10 03:00  ritmo  19% (▲+7 acima do ritmo)
```

- A parte cheia (`█`) é o **uso** que a API reporta. O script não estima isso.
- O `┃` amarelo é o **tempo**: a fração da janela que já passou. Para "ficar no ritmo", a barra
  cheia deveria chegar exatamente até ele. Exemplo: se em 30 min de uma janela de 5 h você
  gastou 50%, passaram 10% do tempo, o `┃` fica em 10% e a barra (50%) passa muito dele.
- O texto ao lado diz o desvio: verde quando está abaixo ou no ritmo (diferença menor que 3
  pontos), amarelo até 15 pontos acima, vermelho além disso.
- Como o tempo é calculado: no Claude a API só dá o horário de reset, então o script assume
  janelas fixas de 5 h e 7 dias e conta para trás. Se a janela não está ativa (reset já passou)
  o marcador some.
- As cores só saem quando a saída é um terminal; num pipe ou arquivo é texto puro.

Sem argumento, `usage-all` percorre **todas** as contas do `accounts.json`; com um ou mais
nomes, mostra só aquelas. O formato completo (`-v`) é o das barras acima; o padrão é o resumido
descrito a seguir. `--json` é a saída que o `orq` e o `orq-web` consomem. O Codex é outro
serviço e tem comando próprio, `codex-usage` (`bin/codex-usage-fast`), com os mesmos modos.

```
usage-all                 # todas as contas do Claude, resumido
usage-all work         # só essa conta (pode listar várias)
usage-all -v              # completo: barra, marcador de ritmo e horário do reset
usage-all -v gmail        # combina os dois
usage-all --json          # para scripts
```

O resumido é o padrão (`-v`/`--verbose` dá o completo). No resumido,
cada conta vira uma linha de tabela. Por janela: a % usada, uma mini-barra de 10 células
(cada célula = 10%) com o mesmo marcador `┃` de ritmo do modo completo e, à direita, o tempo
até o reset. Uma legenda de duas linhas e o nome das janelas saem em cima da tabela.

```
█ usado  ┃ ritmo (quanto da janela já passou)  à direita: falta até o reset
verde = abaixo do ritmo · amarelo = acima · vermelho = 15+ pontos acima
                 sessão 5h              semana 7d              Fable 7d
Gmail        2% ░░░┃░░░░░░ 03h13    2% ░░░░┃░░░░░ 3d12h    3% ░░░░┃░░░░░ 3d12h
Work 2    0% ░░░┃░░░░░░ 03h13   32% ███░░░░┃░░ 2d02h    0% ░░░░░░░┃░░ 2d02h
Work      3% ░░░┃░░░░░░ 03h13   19% ██░┃░░░░░░ 4d10h   11% █░░┃░░░░░░ 4d10h
Work 3   37% ███┃░░░░░░ 03h13    8% ┃░░░░░░░░░ 6d13h   15% ┃█░░░░░░░░ 6d13h
```

O `┃` é o **tempo**: quanto da janela já passou. Barra que não chega no `┃` está abaixo do
ritmo (verde); barra que passa dele está acima (amarela; vermelha com 15 pontos ou mais de
diferença). A % e a barra têm a mesma cor. Quando o `┃` cai dentro da parte preenchida, ele
vira um entalhe na cor da barra, como em `███┃░░░░░░` (37% usado com 34% da sessão decorrida:
no ritmo). Janela que ainda não começou aparece com o `┃` na primeira célula e a duração
cheia em cinza, pois o relógio só dispara no primeiro uso.

As contas do Claude saem ordenadas pela **folga no semanal** (`semana 7d`, todos os modelos):
a diferença entre o usado e o ritmo. Quem está mais abaixo do ritmo vem primeiro; quem está
mais acima, por último. No exemplo, a Work 3 gastou só 8% da semana, mas com 6% dela
decorrida está acima do ritmo, por isso vem depois da Work (19% com 37% decorridos).
Conta com erro vai para o fim.

`sessão 5h` é a janela de 5 h, `semana 7d` a de 7 dias corridos a partir do primeiro uso
(por isso cada conta reseta num dia e horário diferentes); um nome de modelo (ex.: `Fable 7d`)
é o limite semanal só daquele modelo.

### 4b. Retomar a conversa certa com várias contas

Cada conta guarda o histórico no próprio diretório (`<config_dir>/projects/<pasta>/`), então
o `--resume` de uma conta não enxerga as conversas das outras. O `claude-last` olha todas as
contas e mostra, para a pasta atual, a conversa mais recente de cada uma:

```
$ claude-last -a ~
~   (claude-resume N abre a conversa N)
 1  Work      20h  Wifi do MacBook com problemas
 2  Work      20h  Comando usage-all com formatação
 3  Work       1d  Log de Dropbox por API
    = claude-work --resume 00000000-0000-0000-0000-000000000000
```

`claude-resume` abre a linha 1 na conta dela; `claude-resume 2` abre a linha 2, e argumentos
extras vão para o `claude`. Com `-a`, os dois comandos usam as 15 conversas mais recentes da
pasta, de qualquer conta. Conversas vazias e as de renovação de token ficam de fora.

**Retomar com outra conta.** Quando a conta da conversa acabou a cota, `claude-resume --as gmail`
(ou `--as gmail 2`, `--as gmail -a 3`) copia a conversa para a conta `gmail` e abre por lá. A
conversa é só um arquivo local, então a outra conta continua de onde parou. Junto vão os anexos
da conversa e os arquivos de memória do projeto que a conta destino ainda não tem. A original
fica intacta na conta de origem; se você continuar nas duas, viram ramos separados. Se a conta
destino já tinha uma cópia diferente dessa conversa, ela é guardada como `<id>.jsonl.bak-<data>`.
A primeira mensagem na conta nova é mais cara: ela relê a conversa inteira, sem o cache da outra.

### 4c. Orquestrador: 4 projetos de uma vez (`orq`)

```
orq            # escolhe os projetos e abre a grade (se ela já existe, só volta para ela)
orq --new      # fecha a grade e escolhe de novo (os agentes remotos continuam rodando)
orq --list     # só mostra a lista
```

O `orq` pergunta a todas as máquinas de `orq_hosts` (no `accounts.json`) e ao laptop quais
pastas têm conversas recentes, e mostra uma lista única. Você digita até 4 números, ou
`maquina:/caminho` para uma pasta nova. Ficam de fora conversas de `claude -p` (scripts e cron)
e as de `/tmp`.

```
  #  máquina    pasta                              conta        há  assunto
  1  laptop     ~/Projects/claude-multiconta       work    0min  Usage-all account and brief parameters
  2  server0  ~/Projects/cluster    work    0min  HANDOVER-2026-10-04
  3  node0    ~/acervo                           work3   8min  Cópia disco USB para NAS
  4  server0  ~/Projects/myproject               gmail        1d  Atualização do projeto
```

**Conta.** Os projetos são espalhados pelas contas com mais folga no 7d (usado − máx). Cada agente
já rodando numa conta, inclusive os de grades anteriores, pesa 20 pontos contra ela; ficar na conta
da conversa tem 10 pontos de preferência, para não copiar conversas à toa. Conta sem cota (5h ou 7d
em 95%) nunca é escolhida. Antes de abrir, o `orq` mostra a conta de cada projeto e o motivo. Se a
conta mudar, a conversa vai junto (o mesmo `claude-resume --as`).

**Grade.** Uma janela tmux própria (servidor `tmux -L orq`, separado do seu tmux normal) com até 4
painéis. A borda de cada painel mostra projeto, máquina, conta e o assunto atual. Clique ou
`Ctrl-b` + seta muda de painel, `Ctrl-b z` amplia, `Ctrl-b d` sai sem matar nada.

**Agentes que sobrevivem.** Nas máquinas remotas cada projeto roda numa sessão tmux
`orq-<pasta>` de lá. Fechar o laptop ou cair o ssh não mata o agente: o painel oferece
reconectar, e o `orq` volta para a mesma sessão. No tmux remoto o prefixo é `Ctrl-a` (raramente
necessário) e a rolagem é pela roda do mouse. Máquina sem tmux (node1) roda direto, sem essa
proteção.

**Alertas.** Quando um agente termina ou pede permissão, sai uma notificação do iTerm2 com o nome
do projeto. Ela vai pelo próprio terminal (OSC 9 atravessando os dois tmux), sem serviço externo.
Os hooks entram só nos agentes do `orq`, via `claude --settings`, sem mexer no `settings.json`
das contas. Rode o `orq` direto no iTerm2, não dentro de outro tmux, senão o alerta não chega.

**Página web (`orq web`).** O chat com os agentes virou um projeto próprio, o
[`orq-web`](../orq-web) (`~/Projects/orq-web`): pacote Python com testes e CI, dois comandos:

- **`orq-web`**, no laptop: página local (só `127.0.0.1`, token no link) com a cota das contas
  em cima, uma aba por agente de qualquer máquina e um chat no estilo do Remote Control:
  Markdown, ferramentas dobráveis, permissões por botão e formulário para o `AskUserQuestion`.
  `orq web` aqui é só um atalho para ele.
- **`orq-agent`**, em cada máquina: o supervisor que roda o `claude -p` em stream-json na pasta e
  na conta certas, atrás de um socket Unix, dentro de uma sessão tmux `orqs-<projeto>`. É o que a
  página abre nas máquinas; sobrevive ao ssh cair e a conversa é uma sessão normal do Claude Code.

Instalação: `cd ~/Projects/orq-web && ./install.sh` nesta máquina, ou `./deploy.sh` para todas
as de `orq_hosts` (copia o pacote, sem pip). O `permission_mode` do `accounts.json` vale para
os agentes da página. Detalhes no README do orq-web.

**Cota no meio do trabalho.** Ao sair do claude num painel (`/exit`), o `orq-run` confere a conta:
se ela acabou, retoma a conversa na conta com mais folga em 5 s. Senão, mostra
`[Enter] retomar  [c] outra conta  [n] conversa nova  [q] fechar`.

### 5. Onde cada sistema guarda a credencial (o script descobre sozinho)

- **macOS**: no Keychain. Para `~/.claude` o item se chama `Claude Code-credentials`; para
  qualquer outro diretório, `Claude Code-credentials-` seguido dos 8 primeiros caracteres do
  SHA-256 do caminho absoluto. O script calcula esse sufixo, então funciona com qualquer
  usuário e qualquer diretório.
- **Linux**: no arquivo `$CLAUDE_CONFIG_DIR/.credentials.json`.
- **Codex**: `~/.codex/auth.json` nos dois sistemas.

Os tokens nunca são impressos. O token de acesso vence a cada ~8 h; quando isso acontece o
`usage-all` pede ao próprio Claude Code para renovar, disparando um pedido mínimo
(`claude -p "ok" --model haiku`, custo desprezível) naquela conta, e segue. Isso não tem relação com a assinatura: "token expirado" nunca significa plano
vencido. Só se a renovação falhar é que ele pede `claude-<conta>-login`.

### 6. Aliases gerados, não escritos à mão

O `install.sh` lê o `accounts.json` e escreve `~/.config/claude-multiconta/aliases.sh` com
todos os aliases, mais a linha que garante `~/.local/bin` no `PATH` e a dica de abertura do
terminal. No `.zshrc` (Mac) ou `.bashrc` (Linux) entra uma única linha de `source`, marcada
com `# >>> claude-multiconta >>>` para o instalador reconhecer e não duplicar. No zsh com
Powerlevel10k a linha vai **antes** do bloco de *instant prompt*, porque a dica é impressa
na abertura e o p10k reclama de saída depois desse bloco.

Rodar `./install.sh` de novo é seguro: sobrescreve os scripts, regera os aliases e deixa o
`.zshrc`/`.bashrc` como está.

### 7. Adicionar uma conta

```sh
cd ~/Projects/claude-multiconta        # ou a pasta onde o tarball foi extraído
./add-account.sh conta3 fulano@exemplo.com "Fulano (exemplo.com)"
# num terminal novo (ou: . ~/.config/claude-multiconta/aliases.sh):
claude-conta3-login      # abre link, cola código
claude-conta3            # usar
claude-usage-conta3      # uso desta conta
usage-all                # já inclui a conta nova
```

O comando faz três coisas: registra a conta no `accounts.json` com `config_dir` igual a
`~/.claude-conta3`, cria esse diretório e roda o `install.sh`, que regera os aliases. Como o
`usage-all` percorre o JSON, ele passa a incluir a conta sem nenhuma edição.

Regras: o nome só pode ter `[a-z0-9_-]`; nome ou diretório repetido é recusado. O terceiro
argumento é o rótulo que aparece no cabeçalho do uso; se omitido, vira `nome (email)`.

Para tirar uma conta dos aliases, mantendo o diretório e o login:

```sh
./add-account.sh --remove conta3
```

Feito numa máquina, rode `./make-tarball.sh` e reinstale nas outras para o `accounts.json`
ficar igual em todas. Cada máquina faz o próprio login da conta nova.

### 8. Levar para outra máquina

```sh
./make-tarball.sh                                   # gera ~/Projects/claude-multiconta-AAAAMMDD.tar.gz
scp ~/Projects/claude-multiconta-*.tar.gz user@maquina:
ssh user@maquina
tar xzf claude-multiconta-*.tar.gz && ./claude-multiconta/install.sh
exec bash                                           # ou abra um terminal novo
claude-work-login ; claude-gmail-login ; codex-login
usage-all
```

Se o `.bashrc` da máquina já tinha uma função `claude()` própria (o esquema antigo do tmux), o
`install.sh` avisa: remova aquele bloco, senão as duas definições brigam.

O `install.sh` **instala o `claude` e o `codex` se não existirem**: o Claude pelo script
oficial (`claude.ai/install.sh`, vai para `~/.local/bin`), o Codex via `npm` se houver, senão
baixando o binário oficial do GitHub (~100 MB) para `~/.local/bin/codex`. Para pular essa etapa,
`./install.sh --no-install`. O único pré-requisito de verdade é `python3` (3.9 ou mais para
mostrar horários em São Paulo; com 3.8 usa o fuso local da máquina) e `curl`.

O tarball leva scripts, aliases e a lista de contas, **nunca credenciais**: cada máquina faz
o próprio login.

## Arquivos do projeto

| arquivo | o que é |
|---|---|
| `accounts.json` | fonte única de verdade: nome, e-mail, rótulo e `config_dir` de cada conta; também `default_account` (o que o `claude` puro abre), `tmux`, `tui`, `permission_mode` (agentes da página), o fuso horário, `orq_hosts` e `codex` (instalar o Codex e criar `codex-login`/`codex-usage`) |
| `install.sh` | instala `claude`/`codex` se faltarem, copia `bin/*` para `~/.local/bin`, gera `~/.config/claude-multiconta/aliases.sh` e adiciona a linha de source no `.zshrc` ou `.bashrc` |
| `add-account.sh` | adiciona/remove uma conta no `accounts.json`, cria o diretório e reroda o `install.sh` |
| `bin/usage-all` | uso do Claude por conta, direto na API OAuth: resumido, `-v` com barras e `--json` |
| `bin/codex-usage-fast` | o mesmo para o Codex (alias `codex-usage`); separado do `usage-all` |
| `bin/orq` | orquestrador: lista projetos de todas as máquinas e monta a grade tmux; `orq web` chama o `orq-web` |
| `bin/orq-run` | roda em cada painel: abre o claude na conta certa, troca de conta quando a cota acaba |
| `bin/orq-hook` | hook dos agentes do orq: notificação do iTerm2 quando param |
| `bin/claude-last` | conversa mais recente de cada conta numa pasta; base da função `claude-resume` |
| `bin/claude-login-url` | usado como `$BROWSER` no login: não abre navegador, só imprime a URL |
| `bin/claude-whoami` | roda `claude auth status` em cada conta e mostra e-mail, plano e qual é o `claude` puro |
| `make-tarball.sh` | empacota tudo em `~/Projects/claude-multiconta-AAAAMMDD.tar.gz` |

## Desinstalar

```sh
./install.sh --uninstall     # tira scripts, aliases e a linha de source; os diretórios das contas ficam
```
