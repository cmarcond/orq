# 5. Arquitetura

```
navegador ──SSE/POST──► orq-web (handlers) ──Bridge (orq-agent attach, local ou ssh)──► socket Unix
                              │                                                           │
                        StateCache ──► External (usage-all, claude-last, orq, orq-agent)  Supervisor
                              │                                                           │
                        Migrator, Pipelines, Auth                                claude -p stream-json
```

Três processos distintos: o **navegador** (módulos ES, sem build), o **servidor** (`orq-web`, Python
puro, `http.server` com uma thread por conexão) e, em cada máquina, um **supervisor por agente**
(`orq-agent start`, dentro de tmux). Entre o servidor e o supervisor só existe ssh e um socket Unix.

## 5.1 O supervisor (`orqweb/agent/`)

`orq-agent spawn --dir PASTA --account CONTA --label RÓTULO [--mode resume|new]` garante um supervisor
rodando numa sessão tmux `orqs-<rótulo>` (ou com `nohup`, sem tmux) e espera o socket aparecer. O
supervisor (`supervisor.py`):

1. monta o comando (`claude.py`): `claude -p --input-format stream-json --output-format stream-json
   --verbose --include-partial-messages --replay-user-messages --permission-prompt-tool stdio
   --permission-mode <modo> [--resume <id>] -n <nome de sessão> [--append-system-prompt <papel>]`.
   O `--replay-user-messages` faz o claude ecoar o que recebe, então tudo que a página mostra passou
   pelo mesmo fluxo e está persistido. O system prompt sempre leva a nota da `_outbox/` (`OUTBOX_NOTE`
   em `cli.py`) e, num nível, o papel da receita depois dela;
2. com `--mode resume`, pede ao `claude-last --pick --as=<conta> <pasta>` a conversa mais recente da
   pasta, já copiada para a conta se for de outra, e a retoma; o histórico antigo (`<id>.jsonl`, até
   400 entradas) é reenviado a quem conecta, marcado como `replay`;
3. roda o claude com o ambiente da conta (`CLAUDE_CONFIG_DIR` ou nada para a padrão), lê o stdout linha
   a linha, numera cada evento, grava em `events.jsonl` e repassa aos clientes conectados; escreve
   `state.json` (estado derivado, pid, conta, pasta, nome, parent, level);
4. escuta em `agent.sock` (modo 0600). Cada cliente manda primeiro `{"since": N}` e recebe os eventos a
   partir de N, depois ao vivo; cada linha JSON que o cliente manda vai ao stdin do claude;
5. encerra limpo: `{"orq": "quit"}` fecha o stdin do claude, espera `QUIT_GRACE` (15 s) e mata se
   preciso; apaga o socket.

**Protocolo do socket** (`events.py`): as linhas são as do stream-json do `claude -p`, mais:

| linha | sentido | significado |
|---|---|---|
| `{"since": N}` | cliente → supervisor | primeira linha: de onde reenviar |
| `{"type":"orq","level":…,"text":…}` | supervisor → cliente | avisos ("agente encerrado", "claude saiu") |
| `{"type":"orq","subtype":"permission_answered","request_id":…,"behavior":…}` | supervisor → cliente | a página respondeu uma permissão; o replay fecha o cartão |
| `user` com `orq_origin` | supervisor → cliente | `page` (eco do que a página mandou), `agent` (veio de outra sessão pelo `SendMessage`: `origin.name`, `origin.body`, `origin.msg_id`), `injected` (sem marca de humano: cron, loop, sistema) |
| `{"orq":"quit"}` | cliente → supervisor | encerrar |

`stream_event` (texto parcial) só vai ao vivo: não entra na numeração nem no `events.jsonl`.

**Estado derivado** (`derive_state`): `permission` num `control_request` de permissão (com a ferramenta e
um resumo), `waiting` num `result`, `working` numa mensagem do usuário, `quota` quando um
`rate_limit_event` vem com `status: rejected` ou o `result` termina em erro que casa com `QUOTA_RE`
(nesse caso o supervisor guarda em `retry` a última mensagem da página, para a migração reenviar).

**Outros subcomandos**: `attach` (ponte stdin/stdout usada pelo servidor), `stop`, `list`, `convert`
(fecha um claude interativo de tmux em escalada `/exit` → Ctrl-C → SIGTERM e reabre como agente),
`ls`/`cat` (a `_outbox/`), `link`/`unlink`/`links`/`link-run`/`peer` (vínculos, capítulo 6).

## 5.2 O servidor (`orqweb/server/`)

| módulo | responsabilidade |
|---|---|
| `app.py` | porta, token, segundo servidor na mesma porta (reaproveita o link), montagem do `Context`, encerramento |
| `handlers.py` | rotas (7.2), SSE, token/cookie, CSP, estáticos, upload, arquivos, níveis, vínculos, notas injetadas |
| `auth.py` | login por senha com cookie de sessão; bloqueio por IP após tentativas seguidas (429 por um minuto) |
| `external.py` | tudo que sai do processo: `usage-all --json`, `claude-last --projects`, `orq --status-json` + `orq-agent links` (na mesma conexão ssh), `orq-agent spawn/convert/link/unlink/ls/cat`, gravação de arquivos. Um `Runner` injetável permite testar sem ssh |
| `state.py` | `StateCache`: cota, agentes, vínculos, projetos, com laços de fundo só enquanto a página está aberta |
| `bridge.py` | uma ponte (`orq-agent attach`, local ou por ssh) por aba aberta; ponte passageira para entregar uma mensagem sem aba |
| `assign.py` | escolha da conta pela folga no 7d (3.6) e o rótulo `pasta@máquina` |
| `migrate.py` | troca de conta quando a cota acaba |
| `pipelines.py` | receitas de níveis (TOML) e os papéis com os vizinhos resolvidos |
| `sets.py` | conjuntos (⊞): formato JSON, validação, catálogo em `ORQ_SETS_DIR`, conflitos, plano (contas para o conjunto inteiro) e fotografia das abas abertas; quem abre é `handlers.open_set_job` |

**StateCache**: `refresh_usage` a cada 150 s; `refresh_agents` a cada 15 s (todas as máquinas em
paralelo, 12 threads, uma conexão ssh por máquina); projetos sob demanda com 60 s de validade; tudo
isso só se houve um pedido da página nos últimos 60 s. Três mecanismos evitam que a lista pisque:

- **provisório** (60 s): um agente recém-aberto entra na lista na hora e fica até a máquina o devolver
  vivo, porque uma consulta que começou antes dele volta sem ele;
- **fechando** (30 s): um agente mandado encerrar conta como `closed` mesmo que a máquina ainda o veja
  vivo (o claude leva até 15 s para sair);
- **vínculo recém-criado**: cada consulta anota quando começou; um vínculo criado pela página depois
  disso não pode estar na resposta e fica; criado antes, a resposta da máquina é a verdade.

**Bridge**: ao abrir uma aba, a página pede `/stream?since=N`; o servidor abre `orq-agent attach
<rótulo> --since N` (local, ou `ssh host …`) e repassa cada linha como evento SSE. Uma aba por rótulo:
a anterior é fechada. `deliver(label, obj)` escreve na ponte da aba ou, sem aba, numa ponte passageira
que vive 1,5 s (`fresh=True` força uma nova, usado pela migração porque a antiga apontava para o
processo que morreu).

**Migrator**: a cada lista nova de agentes, procura `state == "quota"`; escolhe a conta com mais folga
que não seja a atual; registra o agente como provisório na conta nova **antes** de rodar `orq-agent
spawn --mode resume --restart` (senão a aba sumiria); entrega a mensagem guardada em `retry` por uma
ponte nova; guarda o histórico em `migrations`.

**Segurança**: `Host` conferido; `/api` exige o token (`X-Orq-Token` ou `?t=` no SSE) ou o cookie de
sessão; CSP restringe scripts a `'self'` e ao cdnjs (marked e DOMPurify); Markdown do modelo passa pelo
DOMPurify; `/static` recusa travessia; o socket do agente é 0600; arquivos da `_outbox/` saem com CSP
`sandbox` e `nosniff`, HTML nunca inline, só nomes sem caminho.

## 5.3 O frontend (`orqweb/server/static/`)

Sem build: `index.html` carrega `js/app.js` como módulo ES, mais `marked` e `DOMPurify` do cdnjs.

| módulo | responsabilidade |
|---|---|
| `format.js` | puro: escape, tempos (`left`, `ago`), Markdown sanitizado, mensagem com anexos, tamanhos, tipo de arquivo (`fileKind`) |
| `classify.js` | puro: registros internos a ignorar, origem de máquina, nomes e resumos de ferramenta |
| `events.js` | puro: redutor `reduce(evento, ctx)` → ações de interface (`text`, `tool`, `permission`, `ask`, `bubble`, `machine`, `wireOut/wireIn/sent`, `result`, `sys`…) |
| `app.js` | estado da página: abas, EventSource por aba (reconecta com `since`), DOM, composer, permissões e perguntas, anexos, painel lateral (níveis, vínculos, arquivos), prévia, seletores, notificações |

Os três primeiros têm testes em `tests/js/*.test.mjs` (`node --test`). O `app.js` é coberto pelo QA.

Pontos de atenção no `app.js`: a aba é sempre a do agente base; `cur` é o rótulo do chat mostrado (o
topo da cadeia de níveis); `tabs[label]` guarda o estado de cada chat (sequência do stream, elemento,
pendências de permissão, fios, arquivos); `refresh()` reconcilia a lista das máquinas com o que a aba
já sabe pelos eventos (o estado dos eventos vence, salvo `abrindo…`, que só a máquina confirma) e
detecta reabertura pelo pid (troca de conta) para recriar a aba e avisar.

## 5.4 Estado em disco

```
~/.cache/orq/<rótulo saneado>/        (ORQ_STATE_DIR)
├── agent.sock          socket do supervisor (0600)
├── state.json          label, dir, account, pid, state, msg, sid, kind, sock, name, parent, level, permission, role, set, pipeline, retry, ts
├── events.jsonl        eventos numerados desta vida do agente (recomeça a cada reabertura)
├── supervisor.log      stderr do supervisor (o que ele diz ao sair)
├── role.txt            papel do nível (--role-file -), para o --restart reaproveitar
└── links/<outro>.json  um arquivo por vínculo (6.4) e <outro>.log com o stderr do ssh

~/.config/claude-multiconta/accounts.json       as contas (cópia do install.sh)
~/.config/claude-multiconta/aliases.sh          gerado
~/.config/orq-web/{password,ssh_config,state/}  só no server0 (container)
~/.config/orq-web/conjuntos/<id>.json           os conjuntos (⊞) salvos pela página (ORQ_SETS_DIR)
<config_dir>/sessions/<pid>.json + <pid>.<hash>.key   registro de sessões do Claude Code (6.1)
<projeto>/_inbox/, <projeto>/_outbox/                 anexos de ida e de volta
```

## 5.5 Sequências que valem desenhar

**Abrir um projeto**: página `POST /api/agent/open` → `assign` escolhe a conta → `External.spawn`
(`orq-agent spawn` por ssh) → `add_provisional` → resposta → página `refresh()` + `show()` → `GET
/stream?since=0` → ponte `attach` → eventos → chat.

**Enviar uma mensagem**: página `POST /send {text}` → `bridges.deliver` → stdin do `claude` → eco
`user` (carimbo `page`, porque o supervisor viu o texto sair) → `assistant` com `stream_event` parciais →
`tool_use` / `control_request` → `result` → página marca `esperando você`, relê a `_outbox/`.

**Troca de conta**: `rate_limit_event rejected` → `state.json: quota, retry` → `refresh_agents` →
`Migrator.check` → provisório na conta nova → `spawn --restart` (copia a conversa) → ponte nova →
`retry` reenviado → página vê pid novo → recria a aba → toast.

**Subir níveis**: `POST /upgrade {pipeline}` → `plan_levels` (papéis com `{acima}`, `{abaixo}`, `{eu}`,
`{executores}`) → `spawn` de baixo para cima com `--role-file - --parent --level` → provisórios → (receita
multi) `orq-agent link` do nível de baixo com cada vinculado → página passa a falar com o topo.

**Abrir um conjunto**: página `POST /api/conjuntos/<id>/open` → `sets.validate` → `sets.conflicts` (409 se
algum `maquina:pasta` já tem agente vivo) → `sets.plan` (rótulos únicos, `assign` do conjunto inteiro) →
resposta com o plano → thread `open_set_job`: `spawn --role-file - --permission-mode --set` por agente
(threads por máquina; na mesma máquina em sequência, com `Pacer` espaçando as conexões ssh) e provisórios →
com `lider`, `make_link` do primeiro com cada um dos outros (nota de vínculo aos dois) → `run_upgrade` do
primeiro com a receita (papel substituído por `lider.prompt`) → `tarefa` de cada agente e do professor
entregues por ponte passageira → `job.etapa = pronto`; a página lê `conjuntos_abrindo` no `/api/state` a
cada 5 s e mostra cada etapa como toast.
