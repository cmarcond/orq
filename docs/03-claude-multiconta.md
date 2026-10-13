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
`claude-<conta>`, `claude-<conta>-login`, `claude-usage-<conta>`. `claude` puro é a conta
`default_account`.

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
█ usado  ┃ ritmo (quanto da janela já passou)  à direita: falta até o reset
verde = abaixo do ritmo · amarelo = acima · vermelho = 15+ pontos acima
                 sessão 5h              semana 7d              Fable 7d
Gmail        2% ░░░┃░░░░░░ 03h13    2% ░░░░┃░░░░░ 3d12h    3% ░░░░┃░░░░░ 3d12h
Work      3% ░░░┃░░░░░░ 03h13   19% ██░┃░░░░░░ 4d10h   11% █░░┃░░░░░░ 4d10h

            TOTAL   10% █░░░┃░░░░░   ∞    queima 0,2× · 90% livre
```

- **Janelas**: `session` (5 h), `weekly_all` (7 dias, todos os modelos) e `weekly_scoped` (7 dias de um
  modelo). O tamanho é fixo; o ritmo é `1 - (reset - agora) / tamanho`. Janela cujo reset já passou
  não tem ritmo; janela cujo reset está além do tamanho ainda não começou (o relógio dispara no
  primeiro uso) e aparece com o `┃` na primeira célula.
- **Cores**: diferença uso − ritmo menor que 3 pontos ou negativa é verde; até 15, amarela; além, vermelha.
- **Ordem**: contas da mais folgada para a mais apertada no `semana 7d` (uso − ritmo); erro vai para o fim.
- **TOTAL** (linha final, centralizada): a semana da frota. Uso médio e ritmo médio na mesma mini-barra;
  à direita, **quando zera**: uma simulação que gasta o que sobra de todas as contas no ritmo atual e
  recarrega cada conta no reset dela; `∞` quando os resets bastam para a semana não zerar. `queima` é
  o consumo dividido pela fração da semana decorrida, somados sobre as contas: `1,0×` é exatamente no
  ritmo, menos é folga, mais é aperto (verde abaixo de 0,9, amarelo até 1,15, vermelho acima). `% livre`
  é a média do que sobra. A ideia vem dos *error budgets* de SLO: queima e tempo até esgotar.
- Modos: `usage-all` (resumido), `-v` (barras de 20 células com horário do reset), `usage-all gmail`
  (só as citadas), `--json` (para o `orq` e a página: por conta `5h`, `5h_pace`, `5h_left`, `7d`,
  `7d_pace`, `7d_left` ou `{"err": …}`, mais `_total` com `7d`, `7d_pace`, `7d_free`, `7d_burn`,
  `7d_runs_out`, `7d_next_reset`, `accounts`).
- A API devolve 429 se consultada com frequência; o `orq` e a página consultam a cada 150 s.

O Codex tem comando próprio, `codex-usage` (`bin/codex-usage-fast`), com os mesmos modos; não entra
no `usage-all`.

## 3.5 `claude-last` e `claude-resume`: a conversa certa

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
| `permission_mode` | página | modo dos agentes: `auto`, `manual`, `acceptEdits`, `plan` |
| `auto_switch` | página | trocar de conta sozinho quando a cota acaba (padrão `true`) |

O arquivo do repositório é copiado para `~/.config/claude-multiconta/accounts.json` pelo `install.sh`;
o `orq-web` (e o container, por volume) lê de lá, ou de `CLAUDE_MULTICONTA_CONF`.
