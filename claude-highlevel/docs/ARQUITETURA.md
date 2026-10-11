# HighLevel — arquitetura

Aplicativo web para rodar, assistir e conversar com um **pipeline de Claude Codes em
múltiplos níveis de abstração**. O nível de cima traduz; os de baixo trabalham.

```
       VOCÊ (navegador)
            │  chat
            ▼
   ┌────────────────────┐
   │ nível 1: professor │  interface + narração, nunca executa a tarefa
   └────────┬───────────┘
            │  SendMessage / ListAgents  (canal nativo do Claude Code, via UDS)
            ▼
   ┌────────────────────┐
   │ nível 2: executor  │  faz o trabalho, reporta só marcos
   └────────────────────┘

   cada sessão escreve ~/.claude/projects/<chave>/<session-id>.jsonl
                          │
                          ▼
              tailer → normalizador → bus → WebSocket → UI
```

O princípio é o do experimento que já validamos no terminal: **o app não inventa um
protocolo de comunicação entre agentes**. Ele usa o que o Claude Code já tem
(sessões peer que se acham com `ListAgents` e se falam com `SendMessage`) e resolve o
que faltava: em vez de três janelas de Terminal e um `tail -f | jq`, uma interface só.

---

## 1. O que já está validado (e vira requisito)

Do experimento em `~/.claude/projects/-Users-user-Projects-test`:

- duas sessões interativas no **mesmo diretório** se enxergam com `ListAgents` e
  trocam mensagens com `SendMessage` (transporte: `uds:/tmp/cc-socks/<pid>.sock`);
- o JSONL da sessão do professor contém, **estruturado**, tudo que a UI precisa —
  inclusive a mensagem que chegou do executor já separada do texto de embrulho;
- filtrar `type=="assistant"` + `content[].type=="text"` produz exatamente a "janela
  limpa": a narração, sem os blocos `@ executor❯`.

Dois achados do spike desta sessão que mudam o desenho para melhor:

- **`claude --session-id <uuid>`**: nós escolhemos o id da sessão, então sabemos o
  caminho do JSONL antes de subir o processo. Some o hack do marcador `PROFESSOR_BOOT_…`
  + `grep -l` para descobrir qual arquivo é de quem.
- **`claude -n <nome>`**: o nome da sessão (o que o peer usa para endereçar) vai por
  flag. Some o `/rename` digitado via `osascript`.

E três achados que viram trabalho obrigatório:

- **portões de boot**. `--permission-mode bypassPermissions` abre um diálogo
  (`Yes, I accept`) antes de qualquer coisa, e diretório novo abre o diálogo de
  confiança. Sem tratar isso, a sessão nunca chega a escrever o JSONL — foi
  exatamente o que travou a primeira versão do spike.

- **o transcript não serve como sinal de "sessão pronta"**. O JSONL só nasce no
  primeiro turno, e um nível que ainda não recebeu mensagem nenhuma nunca teria um —
  esperar por ele é um deadlock: o executor só ganha transcript quando o professor
  fala com ele, e o professor só fala depois que todos estão prontos. O sinal certo é
  o socket de peer, `/tmp/cc-socks/<pid>.sock`, criado com o pid exato que spawnamos,
  mais o `❯` da caixa de entrada desenhado na tela.

- **os níveis precisam compartilhar a classe de modo de permissão**. Com o professor
  em `acceptEdits` e o executor em `bypassPermissions`, a primeira mensagem entre eles
  é **retida**: *"The sending session's permission mode class doesn't match this
  session's"*. A sessão de destino mostra um diálogo de revisão com o foco em **Deny**,
  e o pipeline trava antes de começar — sem nada disso aparecer no transcript. Por isso
  o modo de permissão é do **run**, não do nível.

---

## 2. Decisões estruturais

| Decisão | Por quê |
|---|---|
| **Pipeline em TOML**, não YAML | `tomllib` é biblioteca padrão desde o 3.11; YAML seria a primeira dependência externa do projeto |
| Sessões **interativas em PTY**, não `claude -p` | O modo print é one-shot e não fica registrado como peer. O pipeline exige sessões vivas, ociosas e endereçáveis. |
| **Verdade = JSONL**, não a saída do terminal | O PTY é uma TUI cheia de ANSI, spinner e redesenho. O JSONL é um log append-only já estruturado, com `origin.kind` separando humano de peer. |
| PTY mesmo assim é capturado | É por onde se responde diálogo de boot, prompt de permissão e por onde a UI digita. Fica numa gaveta "terminal cru". |
| **Pipeline é dado**, não código | Dois níveis é o caso de hoje; três (professor → arquiteto → executor) é só mais uma entrada na lista. |
| Backend **Python + FastAPI**, frontend **JS puro** | `pty` é stdlib (sem `node-pty`/compilação). Zero build step no front. |
| Um diretório de trabalho por run | É o que faz os peers se enxergarem, e delimita o estrago do executor. |

---

## 3. Componentes

```
hl/
  config.py        caminhos, chave de log (cwd → -Users-user-…), defaults
  gates.py         reconhece e responde diálogos de boot (confiança, bypass)
  session.py       SessionProcess: PTY, spawn, stdin, ring buffer, liveness
  tailer.py        JsonlTailer: tail -F assíncrono, tolerante a linha parcial
  events.py        normalizador JSONL → Event
  bus.py           fan-out para os WebSockets + buffer para replay
  orchestrator.py  Run: ciclo de vida, ordem de subida, bootstrap, parada
  server.py        FastAPI: REST + WS + estáticos
web/               index.html, app.js, style.css
pipelines/         professor-executor.toml, professor-arquiteto-executor.toml
runs/<run_id>/     run.json, events.ndjson, pty-<nível>.log
```

### SessionProcess

Sobe `claude` num PTY com:

```
claude -n <nome>-<tag do run> \
       --session-id <uuid> \
       --append-system-prompt <papel do nível> \
       --permission-mode <modo> \
       [--model …] [--effort …]
```

`--append-system-prompt` é o que define o papel **sem gastar um turno de conversa** —
diferente do experimento, em que o papel do professor era a primeira mensagem do usuário.

### Normalizador (o coração do viewer)

Cada linha do JSONL vira zero ou mais eventos:

| registro | condição | `kind` | payload |
|---|---|---|---|
| `assistant` | `content[].type=="text"` | `narration` | `{text}` |
| `assistant` | `content[].type=="thinking"` | `thinking` | `{text}` |
| `assistant` | `tool_use` name `SendMessage` | `wire_out` | `{to, summary, message}` |
| `assistant` | outro `tool_use` | `activity` | `{tool, brief}` |
| `user` | `origin.kind=="peer"` | `wire_in` | `{from, body, msg_id}` |
| `user` | `origin.kind=="human"` | `prompt` | `{text}` |
| `user` | `content[].type=="tool_result"` | `activity_done` | `{ok, brief}` |
| `user` | `isMeta` + `<system-reminder>` | — | descartado |
| `custom-title` | | `status` | `{name}` |
| `cost-state` | | `cost` | `{usd, tokens}` |

`origin.body` já traz o texto do peer **limpo**, sem o embrulho "Another Claude session
sent a message… permission laundering…". É o que alimenta o painel de conversa entre os
níveis sem nenhum regex.

Evento normalizado:

```json
{"run_id":"r_a3f1","seq":128,"ts":"…","level":2,"session":"executor",
 "kind":"wire_out","payload":{"to":"professor","summary":"…","message":"…"}}
```

### Ciclo de vida de um run

1. `POST /api/runs {pipeline, task, cwd}` → valida o cwd (existe? confiável?).
2. Gera `run_id` e um UUID de sessão por nível.
3. Sobe os níveis **de baixo para cima** — o executor precisa já estar registrado
   quando o professor chamar `ListAgents`.
4. Para cada nível: espera o socket em `/tmp/cc-socks` **e** a primeira linha do JSONL;
   `gates.py` responde os diálogos de boot nesse meio-tempo.
5. Injeta a tarefa no stdin do nível 1.
6. Tailers → normalizador → bus → WebSocket.
7. `POST …/stop`: SIGTERM no grupo de processos. `runs/<id>/` fica para replay.

---

## 4. Interface

```
┌───────────┬────────────────────────────────────┬──────────────────┐
│ NÍVEIS    │  PALCO                             │  FIOS            │
│           │                                    │                  │
│ ● você    │  só a narração do professor.       │ professor→executor│
│ ● profes… │  fonte grande, markdown,           │ executor→professor│
│   ocioso  │  sem tool call, sem thinking,      │ cartões colapsados│
│ ● execut… │  sem bloco @executor❯.             │ coloridos por nível│
│   trabalh…│  é a "terceira janela" do          │                  │
│   $0.42   │  experimento.                      │ [ ] tool calls   │
│           │                                    │ [ ] thinking     │
├───────────┴────────────────────────────────────┴──────────────────┤
│ > falar com: (professor ▾)  ______________________________  [↵]   │
└───────────────────────────────────────────────────────────────────┘
```

- **Cor por nível** (professor ciano, executor âmbar, você branco), constante em todos
  os painéis: é o que deixa "limpo e colorido" sem virar arco-íris.
- **Estado por nível**: ocioso / trabalhando / **bloqueado** (prompt de permissão
  esperando resposta) / morto.
- **Gaveta de terminal cru** por sessão, para depurar e para responder à mão.
- Tudo que não é narração fica atrás de um toggle. O padrão é o palco limpo.

### API

```
GET  /api/pipelines                    presets disponíveis
POST /api/runs                         {cwd, task, permission_mode} → estado do run
GET  /api/runs, /api/runs/{id}         estado + níveis
GET  /api/runs/{id}/events?since=<seq> histórico
GET  /api/runs/{id}/stream?since=<seq> SSE: eventos ao vivo, com replay
GET  /api/runs/{id}/pty?nivel=&linhas=  terminal cru de um nível
POST /api/runs/{id}/message            {to, text}
POST /api/runs/{id}/keys               {to, data}  teclas cruas (diálogos)
POST /api/runs/{id}/allow              libera o nível travado
POST /api/runs/{id}/stop
```

**Runs vivos e arquivados.** `GET /api/runs` junta o que está em memória com o que está
em `runs/`, e um run encerrado continua abrindo depois que o servidor morre: o
`run.json` guarda tarefa, cwd e níveis, e o `events.ndjson` guarda o run inteiro. Para
um run arquivado o `stream` entrega o histórico e manda um evento `fim` — sem isso o
`EventSource` reconectaria em laço para uma fonte que não tem mais nada a dizer. Runs
antigos sem `run.json` ainda aparecem: o manifesto é reconstruído dos próprios eventos.

**SSE em vez de WebSocket** (desvio do plano original, decidido na fase 2). O fluxo é de
mão única — servidor → navegador — e o SSE já traz reconexão com `Last-Event-ID`, que é
exatamente o nosso `seq`. O `EventSource` do navegador reconecta sozinho e o servidor
repõe só o que faltou, sem uma linha de código de reconexão. De quebra, o projeto inteiro
segue sem dependência externa: `http.server` da biblioteca padrão.

---

## 5. Canais de comando — da interface para dentro

O app é dono do PTY de cada nível, então escrever no stdin é **indistinguível de você
digitar naquele terminal**. Isso dá três canais, todos verificados em spike:

| canal | como | verificado |
|---|---|---|
| **Falar** | texto no stdin do nível escolhido — o de cima por padrão, qualquer um por seleção | sim |
| **Enfileirar** | mandar texto enquanto o nível trabalha; o Claude Code absorve no turno em andamento | sim — a instrução extra entrou no meio da tarefa e foi cumprida |
| **Teclas cruas** | `Enter`/setas para responder diálogo, `Esc` para interromper, `shift+tab` para ciclar o modo de permissão | sim, nos diálogos de boot e de permissão |
| **Slash command** | mesma via de texto: `/rename`, `/compact`, `/model` | sim — `/rename painel-web` gerou `custom-title` no JSONL |

Detalhe de implementação: texto do usuário vai em **bracketed paste**
(`ESC[200~ … ESC[201~`) antes do `Enter`. Sem isso, texto multilinha submete na primeira
quebra e uma mensagem começada com `/` abre o autocomplete em vez de virar mensagem.

### O caminho de volta: a dúvida que vem de baixo

Há dois casos, e eles são bem diferentes:

**1. O executor tem uma dúvida.** Ele manda `SendMessage` para o professor, que chega
como `wire_in`, o professor traduz no palco, você responde no chat e vira `wire_out`.
Fluxo normal, tudo visível.

**2. O executor trava num prompt de permissão.** Aqui está a sutileza: quando isso
acontece, **nada é escrito no JSONL**. O registro `tool_use` já está lá, o `tool_result`
nunca chega, e a sessão simplesmente para — silenciosa para quem só lê o transcript.

```
JSONL neste instante:   tool_use Bash {"command": "curl …"}
                        (nada mais — sem tool_result)
PTY neste instante:     Do you want to proceed?
                        ❯ 1. Yes   2. Yes, and don't ask again   3. … 4. No
```

Por isso o estado **bloqueado** é derivado do PTY, não do JSONL — é a razão de o PTY
continuar sendo capturado mesmo com o transcript sendo a fonte da verdade. A UI mostra
o nível em vermelho, exibe o pedido e as opções, e o clique manda a tecla de volta.
No spike, responder de fora liberou o comando e o `tool_result 200` apareceu em seguida.

Vale a pena expor também o **modo de permissão por nível** na criação do run: um
executor em `manual` vai bloquear a cada passo; em `bypassPermissions` não pergunta nada.
O meio-termo útil é `acceptEdits` ou `auto` — e a UI precisa deixar essa escolha explícita,
porque ela é a diferença entre um pipeline autônomo e um que fica esperando você.

## 6. Riscos conhecidos

| Risco | Tratamento |
|---|---|
| Diálogos de boot travam a sessão | `gates.py` reconhece por texto e responde; timeout vira erro visível na UI |
| Prompt de permissão no meio da tarefa | Estado "bloqueado" no nível + gaveta de terminal para responder |
| Colisão de nomes entre runs simultâneos | Nome do peer com sufixo do run (`executor-a3f1`), injetado no prompt do nível de cima |
| `ListAgents` lista sessões alheias | Prompt do nível de cima nomeia o peer exato; nunca "procure um executor" |
| Lag / linha parcial no JSONL | Tailer com offset e buffer de linha incompleta |
| Executor solto no disco | Um cwd por run, escolhido na UI, com aviso explícito do modo de permissão |
| Mensagem de peer retida | Modo de permissão único por run; o diálogo de retenção é reconhecido e vira estado bloqueado |
| Mensagens de peer são **agrupadas** | Se o destinatário está no meio de um turno, várias mensagens chegam numa entrega só: três `wire_out` podem virar um `wire_in`. Nada é perdido, mas não existe correspondência 1 para 1 — quem contar mensagem para provar entrega vai errar |
| Eco da mesma mensagem | Uma mensagem aparece nos dois transcripts (`wire_out` em quem manda, `wire_in` em quem recebe). A ordem de chegada **não é garantida** — os transcripts são lidos em paralelo — então a UI colapsa o segundo a chegar, seja qual for a direção |
| Estado do nível preso em "trabalhando" | O rodapé "esc to interrupt" fica no buffer depois que a sessão para. `trabalhando` exige o rodapé **e** saída recente no PTY — a TUI repinta enquanto trabalha e emudece quando acaba |
| Fala do nível de baixo no palco | O palco só aceita narração do nível 1. A fala dos níveis de baixo já chega traduzida nos marcos; repeti-la no palco recria o ruído que a tela existe para eliminar |

## 7. Fora do escopo v1

Autenticação (roda em `localhost`), execução remota, mais de um run simultâneo por
diretório, edição de prompt de papel pela UI (v1 lê do YAML).
