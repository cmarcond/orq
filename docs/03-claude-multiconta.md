# 3. claude-multiconta: contas, cota e o orquestrador de terminal

O claude-multiconta é a base de tudo: é ele que sabe quais contas existem, onde cada uma guarda a
credencial, quanto de cota resta e qual conversa retomar. Shell + Python 3.8, sem dependências. O
`README.md` dele tem a tabela de comandos; este capítulo explica os mecanismos.

## 3.1 Uma conta por diretório

O Claude Code guarda login, histórico e configuração no diretório apontado por `CLAUDE_CONFIG_DIR`;
sem a variável, em `~/.claude`. Cada conta ganha um diretório:

| conta | diretório | como o alias abre |
|---|---|---|
| `work` (padrão) | `~/.claude` | `env -u CLAUDE_CONFIG_DIR claude` |
| `gmail` | `~/.claude-gmail` | `env CLAUDE_CONFIG_DIR=~/.claude-gmail claude` |
| `work2`, `work3` | `~/.claude-work2`, `~/.claude-work3` | idem |

Dois detalhes que custaram para descobrir:

- A conta padrão é aberta **removendo** a variável. Se ela apontar explicitamente para `~/.claude`, o
  Claude Code procura a credencial com outro nome e aparece deslogado.
- O `claude auth login` normal abre o navegador, e o navegador autoriza a conta que **já estiver logada**
  no claude.ai, seja qual for o e-mail pré-preenchido. Foi assim que uma conta sobrescreveu outra uma
  vez. Por isso o login passa `BROWSER=claude-login-url`, que só imprime a URL.

O `install.sh` ainda grava, por conta, `hasCompletedOnboarding: true` no `.claude.json` (sem isso o
primeiro uso abre um assistente que termina num segundo login, com callback em localhost, que falha) e
`"tui": "default"` no `settings.json` (o renderizador que escreve no scrollback nativo, para o tmux
enxergar o histórico inteiro; o `fullscreen` guarda a conversa num buffer próprio e ela some).

## 3.2 Aliases gerados e sessões tmux

`install.sh` lê o `accounts.json` e escreve `~/.config/claude-multiconta/aliases.sh`; no `.zshrc` ou
`.bashrc` entra uma única linha de `source`, marcada com `# >>> claude-multiconta >>>`. Para cada conta:
`claude-<conta>`, `claude-<conta>-login`, `claude-usage-<conta>`. `claude` puro abre **a melhor conta agora**
(abaixo); se a cota não puder ser lida, a `default_account`.

**A melhor conta (`usage-all --melhor`)**: `claude` puro e `claude-resume` escolhem a conta sozinhos, sem imprimir
nada. A regra é "use primeiro o que vai vencer": a cota que sobra numa conta perto do reset semanal some em horas,
a de uma conta que só zera daqui a dias continua valendo; então a ordem é o que sobra da semana dividido pelas horas
até o reset (pontos que vencem por hora). Ficam de fora contas com menos de 5 pontos sobrando na semana ou com a
janela de 5 h acima de 85%; se nenhuma serve, vai a que tem mais sobra (com um aviso). `usage-all --melhor -v`
mostra a ordem e o motivo; a cota vem do banco do `usage-hist` (ou da API, se o banco estiver velho).
`CLAUDE_CONTA=gmail claude` força uma conta; `claude-<conta>` continua abrindo aquela conta.

Sessão interativa abre num tmux **novo** chamado `<prefixo>-<pasta>` (`claude-foo` para a padrão,
`claude-gmail-foo` para as outras; `-2`, `-3` se repetir), nascido com `cd ~` antes do `new-session`
para o servidor tmux não herdar uma pasta que possa ser apagada depois. O attach é manual (`tmux ls`,
`tmux attach -t claude-foo`). Roda direto, sem tmux, quando já está dentro de um tmux, quando não há
tmux, ou para subcomandos rápidos (`auth`, `mcp`, `config`, `doctor`, `update`, `-p`, `--version`…).
`"tmux": false` no `accounts.json` desliga.

`claude-whoami` roda `claude auth status` em cada diretório e mostra e-mail, plano e qual é o `claude`
puro.

## 3.3 Onde está a credencial

- **macOS**: Keychain. Para `~/.claude` o item é `Claude Code-credentials`; para outro diretório,
  `Claude Code-credentials-` + os 8 primeiros caracteres do SHA-256 do caminho absoluto. O `usage-all`
  calcula o sufixo. Sessões ssh não enxergam o Keychain, daí o LaunchAgent do capítulo 2.
- **Linux**: `<config_dir>/.credentials.json`.
- **Codex**: `~/.codex/auth.json`.

O token de acesso vence a cada ~8 h. Quando o `usage-all` encontra um vencido, pede ao próprio Claude
Code para renovar com um pedido mínimo (`claude -p "ok" --model haiku`) naquela conta. "Token expirado"
nunca é "plano vencido". Tokens nunca são impressos.

## 3.4 `usage-all`: cota com marcador de ritmo

Consulta `https://api.anthropic.com/api/oauth/usage` com a credencial de cada conta (uma chamada por
conta, ~1 s). A API devolve, por janela, a porcentagem usada e o horário do reset; o script **não
estima** uso, só desenha.

```
                   sessão 5h              semana 7d              Fable 7d
  Work      5% ░░░░░░░░┃░ 00h35   42% ████░░░░┃░ 1d04h   65% ██████░░┃░ 1d04h
→ Gmail        0% ░░┃░░░░░░░ 03h45   62% ██████░░░┃ 06h35   82% ████████░┃ 06h35
  Work 3    0% ░┃░░░░░░░░ 04h05   19% ██░░░┃░░░░ 3d07h   35% ████░┃░░░░ 3d07h

TOTAL  32% ███░░░┃░░░  queima 0,5×  zera ∞  reset Gmail 06h21
```

Sem legenda (ela está no `--help`): `█` é o usado, `┃` o ritmo, à direita o tempo até o reset. A seta
marca a melhor conta para abrir agora, a mesma do `usage-all --melhor` (o que o `claude` puro abre). A
origem dos dados só aparece quando a coleta do banco tem 15 min ou mais.

- **Janelas**: `session` (5 h), `weekly_all` (7 dias, todos os modelos) e `weekly_scoped` (7 dias de um
  modelo). O tamanho é fixo; o ritmo é `1 - (reset - agora) / tamanho`. Janela cujo reset já passou
  não tem ritmo; janela cujo reset está além do tamanho ainda não começou (o relógio dispara no
  primeiro uso) e aparece com o `┃` na primeira célula.
- **Cores**: diferença uso − ritmo menor que 3 pontos ou negativa é verde; até 15, amarela; além, vermelha.
- **Ordem**: contas da mais folgada para a mais apertada no `semana 7d` (uso − ritmo); erro vai para o fim.
- **TOTAL** (última linha): a semana da frota. Uso médio e ritmo médio na mesma mini-barra; a **queima**,
  velocidade de consumo: o consumo dividido pela fração da semana decorrida, somados sobre as contas;
  `1,0×` é exatamente no ritmo, menos é folga, mais é aperto (verde abaixo de 0,9, amarelo até 1,15,
  vermelho além); **quando zera**: uma simulação que gasta o que sobra de todas as contas no ritmo atual e
  recarrega cada conta no reset dela (`∞` quando os resets chegam antes); e o próximo reset semanal, de
  que conta. A ideia vem dos *error budgets* de SLO: queima e tempo até esgotar.
- Modos: `usage-all` (resumido), `-v` (barras de 20 células com horário do reset, a melhor conta com o
  motivo e a origem dos dados), `usage-all gmail`
  (só as citadas), `--json` (para o `orq` e a página: por conta `5h`, `5h_pace`, `5h_left`, `7d`,
  `7d_pace`, `7d_left` ou `{"err": …}`, mais `_total` com `7d`, `7d_pace`, `7d_free`, `7d_burn`,
  `7d_runs_out`, `7d_next_reset`, `accounts`).
- A API devolve 429 se consultada com frequência; o `orq` e a página consultam a cada 150 s.

O Codex tem comando próprio, `codex-usage` (`bin/codex-usage-fast`), com os mesmos modos; não entra
no `usage-all`.

### `usage-sessoes`: quem está gastando cada conta

O `usage-all` diz quanto cada conta gastou; a API de cota não diz quem gastou (só porcentagens por conta e
janela). O `usage-sessoes` responde a isso olhando as máquinas: em cada uma (o laptop e as `orq_hosts`, em
paralelo, uma conexão ssh por máquina, com o próprio script indo pelo stdin) ele lê

- os **processos do claude vivos**: a conta (pelo `CLAUDE_CONFIG_DIR` do processo), a pasta, o tipo
  (`interativo`, `orq` para os agentes da página, `script` para um `claude -p` lançado por um programa,
  `daemon` para o `claude daemon` e as reservas dele), a sessão tmux, quem lançou e há quanto tempo vive;
- o **consumo de cada sessão** nas últimas 5 horas (`--horas N`), das conversas que o Claude Code grava em
  `~/.claude*/projects/`: respostas, tokens de saída, cache novo e lido, modelos e, para sessões sem
  terminal, o começo da primeira mensagem (é o que identifica um script). Subagentes somam na sessão-mãe.

O padrão é o **resumido**: uma tabela com um bloco por conta. A primeira linha do bloco traz a cota do
`usage-all` (5h · 7d); cada linha é uma máquina, pasta e tipo (`interativo`, `orq`, `script`), com as execuções
de um mesmo script juntas (`n`), o horário, a parte no consumo da conta com uma barra, e `●` se houve resposta
nos últimos 10 min. Processos vivos sem uso há mais de 1 hora (os esquecidos) entram como linhas `parado`.
Daemon e auxiliares não aparecem.

```
últimas 5 h · sem resposta: node1, node2

┌───────────┬─────────────┬─────────┬──────────────────────┬────────────┬────┬─────────────┬───────────────┬───┐
│ conta     │ 5h · 7d     │ máquina │ pasta                │ tipo       │  n │ horário     │ parte         │ ● │
├───────────┼─────────────┼─────────┼──────────────────────┼────────────┼────┼─────────────┼───────────────┼───┤
│ Work 3 │  26% ·  19% │ laptop  │ claude-diario        │ script     │ 2× │ 18:26–18:40 │ ████████ 100% │   │
│           │             │ node4 │ cluster │ parado     │    │ há 1 d 2 h  │               │   │
├───────────┼─────────────┼─────────┼──────────────────────┼────────────┼────┼─────────────┼───────────────┼───┤
│ Gmail     │  13% ·  13% │ node4 │ kubernetes_2026      │ interativo │    │ 17:51–18:17 │ ███░░░░░  41% │   │
│           │             │ node4 │ claude-multiconta    │ interativo │    │ 18:37–22:50 │ ███░░░░░  37% │ ● │
├───────────┼─────────────┼─────────┼──────────────────────┼────────────┼────┼─────────────┼───────────────┼───┤
│ Work   │   0% ·  40% │         │ sem consumo          │            │    │             │               │   │
└───────────┴─────────────┴─────────┴──────────────────────┴────────────┴────┴─────────────┴───────────────┴───┘
parte: fatia de cada linha no consumo da conta na janela · ●: respondeu nos últimos 10 min · -v: detalhe
```

O **completo** (`-v`) mostra cada sessão: id, respostas, tokens de saída, estado (`● trabalhando`, `○ aberta`,
`encerrada`), o processo ligado a ela (pelo `--resume` ou pela pasta da conversa) com tmux e quem lançou, o
começo do prompt das sessões sem terminal, e todos os processos vivos sem consumo.

```
usage-sessoes                     # resumido, todas as máquinas, últimas 5 h
usage-sessoes -v --conta work3 # cada sessão e processo dessa conta
usage-sessoes --horas 24 --json   # um dia inteiro, para scripts
```

A coluna `parte` é a fatia de cada sessão no **peso** da conta: tokens ponderados pela proporção de preço
da Anthropic (entrada 1, cache novo 1,25, cache lido 0,1, saída 5). É uma estimativa relativa, não a
porcentagem exata da cota, que a Anthropic não detalha. Uso pelo claude.ai (navegador, celular) não grava
nada nas máquinas e não aparece; máquina fora do ar aparece em "sem resposta".

Caso real que motivou o script (2026-10-21): a Work 3 subiu 20% em minutos sem nenhuma sessão aberta
nela. O `usage-sessoes` mostrou nove sessões `sdk/-p` no laptop, todas de um projeto que chamava o
`claude -p` com `CLAUDE_CONTA` fixa na Work 3, lançadas por uma sessão interativa em outra conta.

### `usage-hist`: histórico, ritmo, previsão e alertas

A API de cota só diz a porcentagem de agora. O `usage-hist` guarda amostras num banco SQLite
(`~/.local/share/claude-multiconta/usage.db` no node4, `USAGE_DB`) e calcula sobre elas. Um timer do systemd
de usuário (`usage-coletor.timer`, instalado com `usage-hist instalar`) roda `usage-hist coletar` a cada 5 min:

- a **cota** de cada conta e janela, pela mesma API do `usage-all` (com 429, espera 10 min e dobra até 30);
- cada **resposta do claude** em todas as máquinas, pelo agente do `usage-sessoes` por ssh, só o que é novo
  desde a última coleta daquela máquina (a primeira volta 7 dias; uma máquina que ficou fora recupera o
  buraco na volta, porque as conversas ficam gravadas nela; a cota perdida não se recupera);
- o retrato dos **processos** vivos (guardado por 14 dias).

```
usage-hist                 # painel: por conta, 5h e 7d agora, hoje, ritmo, previsão, barras das últimas 24 h; frota; alertas
usage-hist conta NOME       # uma conta: agora, hoje, ritmo, previsão, 24 h, 7 dias e o que mais gastou
usage-hist uso [--horas N]  # a tabela do usage-sessoes a partir do banco (a aba 📊 uso da página)
usage-hist dia [AAAA-MM-DD] # pontos do dia por conta, por hora, e os projetos que mais gastaram (≈pts)
usage-hist semana          # 7 dias × contas e o perfil por hora do dia
usage-hist calibrar        # tokens por ponto de cada conta
usage-hist alertas         # só os alertas
usage-hist estado          # timer, última coleta, máquinas, tamanho do banco
usage-hist instalar | remover | coletar
```

**Pontos**: 1 ponto = 1% da cota semanal de uma conta. "Hoje" soma as subidas da janela de 7 dias desde a
meia-noite (um reset conta a % nova a partir de zero). O ritmo é em pontos por hora nas últimas 6 h; a
previsão diz quando a conta chega a 100% nesse ritmo e avisa se isso vem antes do reset. Até haver 6 h (ou o
dia inteiro) de amostras, os valores saem com `*`, calculados desde o início da coleta.

**Calibração**: o peso dos tokens (entrada 1, cache novo 1,25, cache lido 0,1, saída 5) dividido pelos pontos
de 7d da conta nos últimos 7 dias dá os tokens por ponto. Com isso o `dia` e o `usage-sessoes` mostram
**≈pts** por projeto e por sessão. Só vale a partir de 3 pontos vistos (confiança baixa até 10).

**Alertas**: subida de 5h por script (5+ pontos em 30 min, 80%+ de sessões `sdk/-p`); subida da semana sem
tokens nas máquinas que a expliquem (claude.ai, celular ou máquina sem resposta); cota que acaba antes do
reset; processo do claude parado há mais de 1 dia; coletor parado; máquina sem resposta há mais de 1 h.

**Os outros comandos leem o banco**: o `usage-all` e o `usage-sessoes` usam a última coleta quando ela tem
menos de 10 min (instantâneos e sem 429; o `-v` diz "do banco · coleta há N min", o resumido só avisa coleta de 15 min ou mais); `--ao-vivo` volta à API e à
varredura. A página do orq recebe a cota do `usage-all --json`, então também passa a vir do banco. Numa
máquina sem banco (o laptop), os três comandos rodam por ssh na máquina de `usage_db_host` do accounts.json.

## 3.5 `claude-last` e `claude-resume`: a conversa certa

No `claude-resume` sem `--as`, a conversa fica na conta dela se a conta estiver boa (janela de 5 h abaixo de 70%
e 15+ pontos sobrando na semana); senão vai para a melhor conta agora, copiada como no `--as`.

Cada conta guarda as conversas em `<config_dir>/projects/<pasta codificada>/<id>.jsonl`. O `--resume`
de uma conta não vê as das outras. O `claude-last` olha todas:

```
$ claude-last
~/Projects/foo   (claude-resume N abre a conversa N)
 1  Work      20h  Wifi do MacBook com problemas
 2  Gmail         1d  Log de Dropbox por API
    = claude-work --resume 00000000-…
```

- padrão: a conversa mais recente de cada conta; `-a`: as 15 mais recentes de qualquer conta;
- conversas vazias, de renovação de token e de `claude -p` (scripts, cron) ficam de fora da lista de
  projetos; o `orq-web` tem exceção para as suas próprias, identificadas pelo `state.json`;
- `--pick [N]` imprime `conta id` (usado pela função `claude-resume`);
- `--as CONTA`: copia a conversa para a outra conta (arquivo, anexos e memória do projeto que a conta
  destino ainda não tem) e imprime `CONTA id`. A original fica; se continuar nas duas, viram ramos. Uma
  cópia anterior diferente é guardada como `.bak-<data>`. A primeira mensagem na conta nova é mais cara
  (relê a conversa sem cache);
- `--projects`: JSON com a conversa mais recente de cada pasta desta máquina, em qualquer conta. É o que
  o `orq` e a página usam para listar projetos.

`claude-resume` abre a linha 1 na conta dela; `claude-resume 2`, a linha 2; `claude-resume --as gmail`
retoma usando outra conta.

## 3.6 `orq`: quatro projetos numa grade tmux

```
orq            # escolhe os projetos e abre a grade (se já existe, só volta para ela)
orq --new      # fecha a grade e escolhe de novo (os agentes remotos continuam)
orq --list     # só a lista
orq --status-json   # agentes do orq nesta máquina (usado pela página)
orq web        # atalho para o orq-web
```

O `orq` pergunta a todas as máquinas de `orq_hosts` e ao laptop (`claude-last --projects` por ssh) e
mostra uma lista única; você digita até 4 números ou `maquina:/caminho`. **Conta**: os projetos são
espalhados pelas contas com mais folga no 7d; cada agente já rodando numa conta pesa 20 pontos contra
ela; ficar na conta da conversa vale 10; conta com 5 h ou 7 d em 95% nunca é escolhida (as mesmas
regras do `assign.py` da página). **Grade**: uma janela num servidor tmux próprio (`tmux -L orq`) com
até 4 painéis, borda com projeto, máquina, conta e assunto. **Agentes que sobrevivem**: nas máquinas,
cada projeto roda numa sessão tmux `orq-<pasta>` de lá (prefixo `Ctrl-a`).

`orq-run` roda em cada painel: abre o claude na conta certa (copiando a conversa se preciso) e, quando
você sai (`/exit`), confere a conta: se esgotou, retoma na conta com mais folga em 5 s; senão oferece
`[Enter] retomar [c] outra conta [n] conversa nova [q] fechar`. `orq-hook` é o hook do Claude Code que
os agentes do orq recebem via `claude --settings` (sem mexer no `settings.json` das contas): grava o
estado em `~/.cache/orq/<rótulo>/state.json` e manda uma notificação do iTerm2 (OSC 9 atravessando os
tmux) quando o agente termina ou pede algo. Rode o `orq` direto no iTerm2, não dentro de outro tmux.

A grade e a página convivem: o `orq --status-json` lista os agentes dos dois tipos (`kind: "sdk"` para
os da página, com `tmux` para os de terminal), e a página mostra um claude de terminal como aba
tracejada com **⇄ converter**.

## 3.7 `accounts.json`: referência

```json
{
  "accounts": [
    {"name": "work",  "email": "…", "label": "Work (…)",  "config_dir": "~/.claude"},
    {"name": "gmail",    "email": "…", "label": "Gmail (…)",    "config_dir": "~/.claude-gmail", "color": "#c05621"}
  ],
  "default_account": "work",
  "tmux": true,
  "tui": "default",
  "codex": true,
  "timezone": "America/Sao_Paulo",
  "tip_on_login": true,
  "orq_hosts": ["server0", "node0", "node1", "node2", "node3"],
  "permission_mode": "auto",
  "auto_switch": true
}
```

| campo | quem usa | significado |
|---|---|---|
| `accounts[].name` | tudo | sufixo dos aliases e chave em todo lugar (`[a-z0-9_-]`) |
| `accounts[].config_dir` | tudo | `CLAUDE_CONFIG_DIR` da conta; `~/.claude` é a padrão |
| `accounts[].label` | `usage-all`, página | nome exibido; o que vem antes de ` (` é o nome curto |
| `accounts[].color` | página | cor da conta; sem ela, uma paleta por posição |
| `default_account` | aliases | a conta do `claude` puro |
| `tmux`, `tui`, `tip_on_login` | `install.sh` | sessões tmux, renderizador, dica na abertura do terminal |
| `codex` | `install.sh` | instalar o Codex e criar `codex-login`/`codex-usage` |
| `timezone` | `usage-all`, `codex-usage` | fuso dos horários de reset |
| `orq_hosts` | `orq`, página, `deploy.sh` | máquinas remotas; o laptop entra sempre |
| `usage_db_host` | `usage-hist`, `usage-all`, `usage-sessoes` | máquina do banco de uso (node4); numa máquina sem banco, os comandos rodam lá por ssh |
| `permission_mode` | página | modo dos agentes: `auto`, `manual`, `acceptEdits`, `plan` |
| `auto_switch` | página | trocar de conta sozinho quando a cota acaba (padrão `true`) |

O arquivo do repositório é copiado para `~/.config/claude-multiconta/accounts.json` pelo `install.sh`;
o `orq-web` (e o container, por volume) lê de lá, ou de `CLAUDE_MULTICONTA_CONF`.
