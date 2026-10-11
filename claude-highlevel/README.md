# HighLevel

Rodar, assistir e conversar com um **pipeline de Claude Codes em múltiplos níveis de
abstração**. O nível de cima traduz; os de baixo trabalham.

```
       VOCÊ (navegador)
            │  chat
            ▼
   nível 1: professor    interface + narração, nunca executa a tarefa
            │  SendMessage / ListAgents   (canal nativo do Claude Code)
            ▼
   nível 2: executor     faz o trabalho, reporta só marcos
```

A ideia não é nova aqui: são duas sessões Claude Code conversando pelo canal que elas já
têm. O que este projeto resolve é o resto — em vez de três janelas de Terminal e um
`tail -f | jq`, uma tela só, com a fala limpa do professor no centro e a conversa entre
os níveis do lado.

**Sem dependência externa.** Só a biblioteca padrão do Python 3.11+ e o `claude` no PATH.

## Começando

```bash
python3 -m hl serve            # abre em http://127.0.0.1:7333
```

Ou, sem navegador:

```bash
python3 -m hl run \
  --cwd /private/tmp/demo \
  --task "Crie um hello.py que recebe um nome e imprime 'Olá, <nome>!', com 3 testes." \
  --show fios
```

```bash
python3 -m hl pipelines        # as receitas disponíveis
```

## A tela

| painel | o que mostra |
|---|---|
| **níveis** | um cartão por nível, com cor própria e estado: ocioso, trabalhando, **bloqueado**, morto. Clicar abre o terminal cru daquele nível |
| **palco** | só a narração do nível de cima. Sem tool call, sem thinking, sem a fala dos níveis de baixo |
| **fios** | as mensagens entre os níveis; a mesma mensagem aparece uma vez e a segunda vira "entregue" |
| **compositor** | fala com o nível que você escolher — por padrão, o de cima |

Quando um nível trava pedindo permissão, o cartão fica vermelho com as opções reais do
diálogo, e um clique responde. Isso importa porque **o transcript não registra esse
estado**: o `tool_use` já foi gravado, o `tool_result` nunca chega, e a sessão fica em
silêncio. Quem sabe é o terminal — por isso o PTY continua sendo capturado mesmo com o
JSONL sendo a fonte da verdade.

## Pipelines são dados

Cada receita é um `.toml` em `pipelines/`. Acrescentar um nível é escrever um
`[[niveis]]` a mais — nada de Python:

```toml
[[niveis]]
nome = "arquiteto"
cor = "35"
papel = """
Você é o ARQUITETO… Acima está `{acima}`, abaixo está `{abaixo}`.
"""
```

`{acima}` e `{abaixo}` viram os nomes reais das sessões vizinhas, já com o sufixo do run
(`arquiteto-a3f1`) — é o que deixa dois runs rodarem ao mesmo tempo sem disputar nomes.

Acompanham o projeto `professor-executor` e `professor-arquiteto-executor`.

## Modo de permissão

É escolha do **run**, não do nível. Níveis em classes de permissão diferentes fazem o
Claude Code **reter** a mensagem de um para o outro esperando revisão humana, e o
pipeline trava antes de começar.

- `auto` — resolve os pedidos comuns sozinho (padrão, e o que faz o pipeline andar)
- `acceptEdits` — aceita edições, pergunta o resto
- `manual` — pergunta tudo; útil para ver o bloqueio funcionando
- `bypassPermissions` — não pergunta nada

O executor escreve no diretório de trabalho que você escolher. Escolha um diretório
descartável enquanto estiver experimentando.

## Como funciona por dentro

Cada nível é uma sessão Claude Code interativa num PTY que o app controla:

```
claude -n <nome>-<tag> --session-id <uuid> --permission-mode <modo> \
       --append-system-prompt <papel do nível>
```

Como nós escolhemos o `--session-id`, sabemos o caminho do transcript antes do processo
subir: `~/.claude/projects/<chave do cwd>/<uuid>.jsonl`. Um tailer segue esse arquivo e
um normalizador transforma cada registro em evento — `narration`, `wire_out`, `wire_in`,
`activity`, `prompt` — que o navegador recebe por SSE.

## Testes

```bash
python3 -m pytest -m "not slow"    # rápido, sem subir sessão
python3 -m pytest -m slow -s       # ponta a ponta: minutos e tokens de verdade
```

## Onde ficam as coisas

```
hl/          o código: sessão em PTY, portões, tailer, eventos, servidor
web/         a interface (HTML, CSS e JS puro, sem build)
pipelines/   as receitas .toml
runs/<id>/   por run: run.json, events.ndjson e o PTY cru de cada nível
docs/        ARQUITETURA.md e PLANO.md
```
