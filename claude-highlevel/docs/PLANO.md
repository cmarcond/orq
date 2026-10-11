# Plano de implementação — 5 fases

O exercício de aceitação é sempre o mesmo, o do experimento original:

> **Tarefa T:** criar em `<cwd do run>` um `hello.py` que recebe um nome por argumento
> e imprime `Olá, <nome>!`, com pelo menos 3 testes automatizados, e rodar os testes.
>
> O **professor** nunca escreve código: ele delega ao **executor** e narra em português
> natural. O executor reporta só marcos (`PLANEJANDO`, `IMPLEMENTANDO`, `PROBLEMA`,
> `VALIDANDO`, `CONCLUÍDO`).

Cada fase repete a tarefa T e sobe um degrau de exigência. Nada é dado como pronto
sem a T passando de ponta a ponta naquela fase.

---

## Fase 1 — Núcleo: subir, tailer, normalizar  ✅ concluída

**Entrega:** `hl/session.py`, `hl/gates.py`, `hl/tailer.py`, `hl/events.py`,
`hl/config.py` e um `hl/cli.py` com `hl run --task "…"` que imprime eventos coloridos
no terminal. Sem web ainda.

Três problemas concretos, já mapeados no spike, precisam morrer aqui:

1. **Env do processo filho.** Se o app for iniciado de dentro de uma sessão Claude
   Code, o filho herda `CLAUDE_CODE_CHILD_SESSION` e **o transcript não é gravado** —
   o JSONL nunca aparece e o viewer fica cego. O spawner limpa `CLAUDECODE`,
   `AI_AGENT` e todo `CLAUDE_*` antes de subir.
2. **Chave do diretório de log.** `/tmp/x` vira `-private-tmp-x`: resolver `realpath`
   do cwd antes de montar `~/.claude/projects/<chave>`.
3. **Portões de boot.** Diálogo de confiança do diretório e aviso do
   `bypassPermissions` são respondidos por `gates.py`, com timeout que vira erro.

**Teste:** `hl run --cwd /tmp/hl-t1 --task T`. Passa quando:
- os dois JSONL aparecem em `~/.claude/projects/-private-tmp-hl-t1/` com os UUIDs que
  **nós** escolhemos;
- o terminal mostra, em cores diferentes, `wire_out` (professor→executor),
  `wire_in` (executor→professor) e `narration`;
- `hello.py` existe e os testes rodam;
- `hl run` mata os dois processos ao sair, sem deixar órfão (`pgrep -f claude`).

**Resultado (28/08):** passou. `python3 -m hl run --cwd /private/tmp/hl-t1 --task "…"
--permission-mode auto` entregou `hello.py` + `test_hello.py` com 12 testes verdes,
os dois transcripts nasceram com os UUIDs escolhidos, o PTY de cada nível ficou em
`runs/<id>/`, e o encerramento não deixou processo nem socket para trás.

Três defeitos encontrados e corrigidos no caminho: prontidão medida pelo transcript
(deadlock), classes de permissão diferentes entre níveis (mensagem retida) e registros
de custo reiniciando o cronômetro de silêncio (o run nunca encerrava sozinho).

---

## Fase 2 — Orquestrador e servidor  ✅ concluída

**Entrega:** `hl/orchestrator.py` (ciclo de vida do run, ordem de subida de baixo para
cima, injeção da tarefa, parada), `hl/bus.py` (fan-out + buffer com `seq`),
`hl/server.py` (FastAPI: `POST /api/runs`, `GET /api/runs/{id}`,
`POST /api/runs/{id}/message`, `POST /api/runs/{id}/stop`, `WS /ws/runs/{id}`).
Persistência em `runs/<id>/events.ndjson`.

**Teste:** sem navegador, com `curl` + um cliente WS de linha de comando:
- `POST /api/runs` com a tarefa T devolve `run_id` e os dois níveis sobem;
- o WS entrega a mesma sequência de eventos que a fase 1 imprimia;
- reconectar com `?since=<seq>` **não perde nem duplica** evento;
- `POST /stop` derruba tudo; `runs/<id>/events.ndjson` reproduz o run inteiro.

**Resultado (28/08):** passou. 32 eventos lidos em duas conexões (corte proposital no
`seq` 12, reconexão pedindo `since=12`, retomada no 13): **zero duplicado, zero furo**.
Histórico contíguo `1..40`, persistido em `events.ndjson`, e a mensagem enviada por
`POST /message` chegou ao professor como evento `prompt`.

**Desvio do plano:** WebSocket virou **SSE**. O fluxo é de mão única e o `Last-Event-ID`
do SSE é o nosso `seq`, então a reconexão sem furo é nativa — e o servidor cabe na
biblioteca padrão, sem FastAPI nem uvicorn.

---

## Fase 3 — Interface: o palco limpo  ✅ concluída

**Entrega:** `web/` (HTML/CSS/JS puro): rail de níveis com cor e estado, **palco**
central só com a narração do professor (markdown, fonte grande), painel **fios** com
as mensagens entre os níveis, toggles de `tool calls` e `thinking`, gaveta de terminal
cru por sessão.

**Teste:** rodar a tarefa T pelo navegador. Passa quando:
- o palco **não** contém nenhum bloco `@ executor❯`, nenhuma tool call, nenhum
  thinking — só a narração (é o critério que definiu o 9/10 do experimento);
- os 5 marcos do executor aparecem no painel de fios, cada um com sua cor de origem;
- o estado de cada nível muda visivelmente entre ocioso e trabalhando;
- fechar e reabrir a aba no meio do run reconstrói a tela inteira sem buraco.

**Resultado (28/08):** passou, no navegador de verdade. O palco ficou só com a narração
do professor; os marcos do executor foram para os fios com a cor de origem; os chips de
estado alternaram entre ocioso, trabalhando e morto; e recarregar a página no meio do
trabalho reconstruiu palco e fios inteiros (`?since=0` no `EventSource`).

Três defeitos encontrados olhando a tela, não o código: `display:grid` vencia o atributo
`hidden` e a tela do run aparecia por baixo do formulário; a narração do **executor**
invadia o palco (justo o critério da tela limpa); e o colapso do eco assumia que a saída
chega antes da entrada — os dois transcripts são lidos em paralelo, então a ordem não é
garantida.

Um pedaço da fase 4 veio junto de brinde: mandei pelo chat *"peça pro executor adicionar
um teste para nome com emoji"*, o professor repassou por `SendMessage` e o executor
alterou o `test_hello.py` — o ciclo inteiro, do navegador até o disco.

---

## Fase 4 — Interação e controle  ✅ concluída

**Entrega:** chat funcional (envio ao nível 1 por padrão, seletor para falar direto com
qualquer nível, texto em **bracketed paste** para não submeter na quebra de linha nem
abrir autocomplete no `/`), detecção de nível **bloqueado** com resposta pela UI
(`POST /keys`), parada e limpeza, lista de runs anteriores com replay.

O estado **bloqueado** não sai do JSONL: quando um nível para num prompt de permissão,
o `tool_use` já foi gravado e o `tool_result` nunca chega — o transcript fica em silêncio.
Quem sabe é o PTY. A regra é: `tool_use` pendente **e** diálogo reconhecido na tela.

**Teste:** durante a tarefa T,
- perguntar no chat "ele já rodou os testes?" e a resposta do professor aparecer no
  palco sem quebrar o fluxo do executor;
- mandar "peça pra ele adicionar um teste de nome vazio" e o pedido aparecer como
  `wire_out` e depois virar trabalho real no executor;
- rodar um nível em modo `manual` de propósito, pedir algo que exige aprovação
  (uma chamada de rede, por exemplo), ver o nível ficar **bloqueado** com o pedido e as
  opções na tela, e destravá-lo por um clique;
- abrir um run encerrado e reproduzir a narração inteira.

**Resultado (28/08):** passou, os quatro. O bloqueio foi provocado de propósito — nível
em `manual` mandado a fazer uma chamada de rede — e apareceu no rail em vermelho com as
quatro opções reais do diálogo (`1. Yes`, `2. Yes, and don't ask again for: curl *`, …).
Clicar liberou; o executor bateu num **segundo** pedido, para outro comando, e o
`POST /allow` liberou de novo até a tarefa concluir (`HTTP 200`).

O replay foi testado do jeito honesto: o servidor foi **reiniciado** antes, então o run
só existia em disco. Abriu pela lista, reconstruiu palco e fios, e o SSE fechou no evento
`fim` em vez de ficar reconectando.

Dois defeitos de legibilidade no terminal cru, os dois vindos da mesma raiz: a TUI
escreve os espaços entre palavras como **movimento de cursor**, não como espaço. Apagar
todo escape gruda tudo (`esctointerrupt`); trocar todo escape por espaço quebra palavra
no meio. Movimento virou espaço, o resto some. E o regex de ANSI não cobria os modos
privados (`\x1b[>4m`, `\x1b[<u`) nem salvar/restaurar cursor, que vazavam crus na tela.

---

## Fase 5 — Pipelines como dado, endurecimento, E2E  ✅ concluída

**Entrega:** `pipelines/*.yaml` (papéis, modelo, modo de permissão, cor por nível),
suporte a **3 níveis** (professor → arquiteto → executor) só editando YAML, sufixo de
run no nome do peer para runs simultâneos, `README.md`, e um teste E2E automatizado
que roda a tarefa T e valida os eventos.

**Teste:**
- `pytest` sobe o pipeline de 2 níveis com a tarefa T, e afirma sobre o
  `events.ndjson`: existe ≥1 `wire_out` do nível 1, ≥3 `wire_in` do nível 2,
  ≥3 `narration` do nível 1, zero `narration` do nível 2 no palco, e `hello.py`
  com testes verdes no cwd;
- o mesmo teste com o pipeline de 3 níveis passa sem tocar em código Python;
- dois runs simultâneos em diretórios diferentes não se confundem (nomes com sufixo);
- checklist de higiene: nenhum `claude` órfão, nenhum socket vazado em `/tmp/cc-socks`.

**Resultado (28/08):** o pipeline de **três níveis** passou sem uma linha de Python
tocada — só um `.toml` a mais. O de dois níveis falhou na primeira rodada, e a falha era
minha: eu exigia três marcos do executor, e naquela execução ele fez a tarefa inteira de
uma vez e reportou **uma** vez só, com oito testes verdes. Quantos marcos o executor
manda é juízo dele, não garantia do sistema.

A asserção foi trocada por entrega em vez de quantidade — e aí o teste revelou algo que
eu não sabia sobre o mecanismo. Exigir um `wire_in` para cada `wire_out` falhou no
pipeline de três níveis, mas **nada tinha se perdido**: o executor mandou três marcos
enquanto o arquiteto estava no meio de um turno, e o Claude Code **agrupou** as três
numa entrega só. O arquiteto respondeu pelos três. Ou seja: mensagens de peer que chegam
com o destinatário ocupado são coalescidas, e não existe correspondência 1 para 1.

O que o teste afirma agora é mais fraco e mais verdadeiro: para toda mensagem enviada, ou
o corpo aparece como `wire_in` no destino, ou **o destino produziu algum evento depois
dela** — isto é, acordou. Isso pega furo de canal de verdade sem depender de um
comportamento que o sistema nunca prometeu. Validado contra os 15 runs gravados até aqui.

**Desvio do plano:** YAML virou **TOML**. `tomllib` é biblioteca padrão desde o 3.11, e
YAML exigiria a primeira dependência externa do projeto.

**Dois bugs encontrados pelo próprio teste.** O `stop()` parava os tailers **antes** de
derrubar as sessões, então o fim do transcript se perdia — agora derruba as sessões
primeiro e dá uma última drenada antes de fechar. E o segundo: o estado `trabalhando` nunca voltava para
`ocioso`. Eu procurava o rodapé "esc to interrupt" na tela, mas ele fica preso no buffer
depois que a sessão para — e o rodapé de ocioso não serve de contraprova, porque muda
conforme o modo de permissão. O sinal honesto é o ritmo do terminal: a TUI repinta sem
parar enquanto há turno em andamento e emudece quando acaba. Agora `trabalhando` exige o
rodapé na tela **e** saída recente no PTY.

---

## Ordem de risco

O risco todo está na fase 1 — é lá que mora tudo que já quebrou no spike (env herdado,
chave de path, diálogo de boot). Fases 2–5 são construção sobre terreno conhecido.
