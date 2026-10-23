# 4. A página (orq-web): guia de uso

Endereço: `http://<ip zerotier>:8765/` (senha) ou, com um `orq-web` local, `http://127.0.0.1:8765/?t=<token>`.
No celular, use `http://<ip zerotier>:8765/m`: a mesma página com arranjo para tela estreita (abas numa
linha com rolagem lateral, cota recolhida numa faixa com o TOTAL que abre as contas ao toque, chat com a
tela inteira, painel lateral como gaveta). Pelo ZeroTier do celular o acesso é direto, sem túnel.
Ao entrar, a página pede o estado (`/api/state`) a cada 5 s e abre um stream por aba aberta.

## 4.1 O topo: cota

À direita do topo, um cartão por conta com as barras de `5h` e `7d` no mesmo formato do `usage-all`
(barra = usado, `┃` = ritmo, à direita o tempo até o reset; verde/amarelo/vermelho pela folga) e, acima
deles, o cartão **TOTAL · semana**: a barra média da frota, "quando zera" ou `∞`, `queima N×` e
`% livre`. A cota é lida a cada 150 s (a API reclama de consultas frequentes) e só enquanto a página
está aberta. O 🔔 pede permissão de notificação (ver 4.9).

Com o histórico de uso no ar (o `usage-hist` do claude-multiconta, coletor a cada 5 min), a cota vem do banco
dele e os cartões ganham mais duas linhas, lidas a cada 60 s (`usage-hist painel --json`, no `/api/state` como
`history`):

- **barras das últimas 24 h**: uma por hora, na cor da conta, com a mesma escala em todos os cartões; passar o
  mouse numa barra mostra a hora e os pontos gastos nela (1 ponto = 1% da cota semanal da conta);
- **resumo**: `hoje +N` (pontos da semana desde a meia-noite), o ritmo em pontos por hora nas últimas 6 h e
  `100% em …` (quando a conta acaba nesse ritmo); em vermelho, com ⚠, se isso vem antes do reset. `*` marca o
  que ainda é calculado desde o início da coleta.

O cartão TOTAL mostra os pontos gastos pela frota em 24 h e avisa se a coleta parou há mais de 15 min. Os
**alertas** do coletor (subida por script, subida sem consumo nas máquinas, cota que acaba antes do reset,
coletor parado) aparecem como toast, cada um uma vez a cada 6 h, e também como **selo** no cartão enquanto
durarem: `⚠ script`, `⚠ semana`, `⚠ 5h`, `⚠ fora das máquinas` em vermelho (`⚠ 2` com mais de um), e avisos como
`· processo parado` em cinza; o título do selo traz o texto completo, e o TOTAL mostra `⚠ N alertas` da frota
inteira (inclui coletor parado e máquina sem resposta). Sem o `usage-hist`, a página continua só com a cota.

**Clicar no cartão de uma conta** abre o painel dela (na janela da prévia de arquivos; Esc ou ✕ fecha):

- cinco números: % da semana e da janela de 5 h com o tempo até o reset, `hoje`, ritmo e a previsão (o tempo
  até 100%, em vermelho quando vem antes do reset);
- **semana até o reset**: a % usada desde o início da janela semanal (linha cheia), o ritmo ideal da janela
  (tracejado cinza, de 0% no início a 100% no reset), a marca de agora e a projeção no ritmo atual (tracejado
  na cor da conta, até o reset ou, em vermelho, até onde bate 100%);
- **últimas 24 h** (pontos por hora) e **últimos 7 dias** (pontos por dia, com o valor em cima);
- **o que mais gastou**, em 24 h ou 7 dias: máquina, pasta, tipo, sessões e ≈pts;
- os alertas da conta e a calibração (quantos tokens valem 1 ponto).

Os dados vêm de `GET /api/conta/<nome>` (`usage-hist conta NOME --json`, com cache de 30 s).

## 4.2 Abas: uma linha por máquina

Cada máquina tem uma linha com as abas dos agentes dela e um **+** para abrir um projeto ali. A aba
mostra o nome da pasta, um ponto com o estado (pulsando enquanto trabalha; amarelo pedindo permissão;
vermelho em erro ou sem cota), a cor da conta, e sufixos: `⇅n` níveis acima, `🔗n` vínculos, `⌘1`…`⌘9`
para trocar pelo teclado, `✕` para encerrar (duas vezes; a conversa fica salva). Aba tracejada com
*terminal* é um claude interativo em tmux, que só pode ser convertido (4.10).

A ordem e o conjunto das abas vêm das máquinas (`orq --status-json` a cada 15 s). Uma aba
recém-aberta aparece na hora, como provisória, até a máquina a confirmar; uma encerrada some em
segundos. Nível de cima não ocupa aba: ele mora dentro da aba do agente base.

## 4.3 Abrir um projeto

**+** abre o seletor: a conversa mais recente de cada pasta, em todas as máquinas (máquina, pasta, conta,
idade, assunto), as mais recentes primeiro. Clique abre com a conversa retomada (`--mode resume`, com a
conversa copiada se a conta escolhida for outra). O campo `maquina:/caminho` abre uma pasta nova
(`--mode new`). A conta é escolhida pela folga no 7d (3.6); o toast diz qual e por quê. Se já houver um
claude de terminal aberto naquela pasta, a página recusa e manda converter primeiro, para não pôr dois
processos na mesma conversa.

## 4.4 O chat

- **Mensagens**: as suas à direita (`me`), as do Claude em Markdown sanitizado, com texto chegando
  palavra a palavra (`▍` pulsando enquanto escreve).
- **Ferramentas**: cada chamada vira um chip dobrável com o nome e um resumo do argumento (comando,
  arquivo, padrão); ✓ ou ✗ ao terminar; clique abre o resultado. `SendMessage` aparece como
  `→ destinatário` e uma mensagem de outra sessão como `de remetente` (ver 4.7).
- **Permissão**: cartão amarelo com o pedido e Permitir/Negar. **Pergunta** (`AskUserQuestion`):
  formulário com botões de rádio ou caixas, campo "Outro…", Responder/Cancelar. Em `permission_mode:
  auto` o Claude decide o rotineiro e só pergunta o sensível; em `manual` pergunta tudo.
- **Avisos** (`sistema`): chips tracejados para mensagens injetadas pelo sistema (notas do orq, hooks)
  e linhas cinza centradas para eventos do supervisor ("agente encerrado", "interrompido por você").
- **Estado** na barra inferior: conta, nível (se houver) e `trabalhando…`, `esperando você`, `pede
  permissão`, `erro`, `abrindo…`, `sem cota: trocando de conta…`, `fechado`.
- **Teclado**: `Enter` envia, `Shift+Enter` quebra linha, `Esc` interrompe o turno (`■ parar` também),
  `⌘1`…`⌘9` trocam de aba, `Esc` fecha a prévia de arquivo.

Reabrir a página, ou outra aba do navegador, recupera a conversa inteira: o stream começa do evento
`N` que a página já tinha, ou do zero com o histórico antigo (até 400 entradas do `.jsonl` da sessão).

## 4.5 Anexos: `_inbox/`

**📎** ou arrastar arquivos para o chat os enfileira como chips; ao enviar, cada um é gravado em
`<pasta do projeto>/_inbox/<nome>` na máquina do agente (por ssh quando remota; até 200 MB cada), e a
mensagem sai com os caminhos, para o Claude ler o PDF ou a planilha direto de lá. Sem texto, a mensagem
padrão é "Veja o arquivo enviado."

## 4.6 Arquivos que o agente compartilha: `_outbox/`

O caminho inverso. Todo agente da página sobe com uma nota no system prompt: para compartilhar um arquivo
com você, copie-o para `_outbox/` na raiz do projeto. **📂** na barra do chat abre a lista no painel
lateral: ícone por tipo, nome, tamanho e idade; clique abre a prévia (imagem, PDF no visualizador do
navegador, vídeo e áudio com player, texto e Markdown renderizado) e **⬇** baixa; **↻** relê. A lista é
relida ao fim de cada rodada do agente e a cada 30 s; arquivo novo gera o toast "📂 nome: novo arquivo
…", a contagem no botão e um destaque na linha. A página nunca apaga nada da pasta.

Por baixo: `GET /api/agent/<x>/files` lista (`orq-agent ls` na máquina) e `GET /api/agent/<x>/file?name=…`
serve (`orq-agent cat`, com faixas `Range` para o vídeo pular no tempo). Todo arquivo sai com
`Content-Security-Policy: sandbox` e `nosniff`; HTML nunca é servido inline.

## 4.7 O painel lateral: níveis, vínculos, arquivos

À direita do chat, um painel com abinhas no topo mostra uma vista por vez: **⇅ níveis** (quando o
agente tem níveis), **🔗 vínculos** (quando tem vínculos) e **📂 arquivos** (quando você abre). Em telas
estreitas o painel desliza por cima do chat; os botões da barra do chat o abrem e fecham.

**Fios** aparecem nas vistas de níveis e de vínculos: cada `SendMessage` que sai de um agente vira um
fio `remetente → destinatário` com o resumo e o texto (clique abre), marcado ✓ quando enviado e ✓✓
quando o outro lado o recebeu; mensagens que chegam aparecem como `remetente → destinatário ✓✓`.

## 4.8 Níveis: professor → executor

**⇅ subir nível** escolhe uma receita (`orqweb/pipelines/*.toml`):

- `professor → executor`: um professor acima do agente da aba;
- `professor → arquiteto → executor`: dois níveis;
- `professor → executores (este + vinculados)`: um professor que coordena o agente da aba **e** os
  agentes vinculados a ele, em qualquer máquina (4.9 abaixo e capítulo 6).

Os níveis sobem na mesma máquina, pasta e conta do agente, cada um como agente normal da página, com o
papel da receita no system prompt e o nome dos vizinhos resolvido. A aba passa a falar com o nível de
cima; o agente original vira o executor, escondido atrás, e recebe o trabalho pelo `SendMessage`. A
vista **níveis** mostra um cartão por nível, do topo para baixo (clique abre o chat cru daquele nível;
uma permissão pendente num nível pode ser respondida ali mesmo). **↓ desfazer** (duas vezes) encerra os
níveis de cima e a aba volta ao executor.

Um agente aberto antes de os agentes subirem com nome não pode ter níveis (o `SendMessage` não o
acharia); o seletor oferece **↻ reabrir com nome**, que fecha e reabre a mesma conversa.

## 4.9 Vínculos: um agente pede ao outro

**🔗 vincular** escolhe outro agente da página, em qualquer máquina. Os dois passam a se ver no
`ListAgents` e a pedir coisas um ao outro pelo `SendMessage`, como pares. Cada um recebe a nota
`[orq] Vínculo criado com o agente …` dizendo o nome do outro (pasta, máquina) e como falar com ele. A
vista **vínculos** mostra um cartão por agente ligado (clique abre a aba dele; ✕ duas vezes desfaz) e os
fios; **+ vincular** liga a mais um. A aba ganha `🔗n` e o botão vira `🔗 n`.

Com vínculos feitos, a receita `professor → executores` sobe um professor que ganha vínculo com cada
agente vinculado ao da aba. O papel dele lista os executores (nome de sessão, máquina, pasta) e manda
distribuir o trabalho pelo que cada um tem à mão, intermediando quando um precisa do resultado do outro.
**↓ desfazer** desfaz os vínculos do professor e o encerra; os vínculos entre os executores ficam.

Vínculo entre máquinas diferentes passa por um túnel ssh que o servidor abre na máquina do primeiro
agente (o toast avisa "túnel ssh"); leva uns segundos a mais.

## 4.10 Cota acabou: troca de conta sozinha

Quando o claude avisa que a conta esgotou, o supervisor marca o agente como `quota` e guarda a sua
última mensagem. Na consulta seguinte às máquinas (até 15 s), o servidor reabre o agente na conta com
mais folga, na mesma conversa (copiada), e reenvia a mensagem. A aba fica, passa por `abrindo…` e
continua; o toast diz "conta X sem cota; continua na Y". `/api/state` traz `migrations` com o
histórico. `"auto_switch": false` desliga.

## 4.11 Notificações e bipe

O 🔔 pede permissão ao navegador. Com a página fora de vista, cada agente que pede permissão, faz uma
pergunta ou termina um turno gera uma notificação e um bipe curto; o título da aba vira `(2) orq` até
você abrir cada um. Navegadores só dão notificação em `https` ou `localhost`: pela rede em `http://<ip>`
fica o bipe (o clique no sino destrava o áudio) e a contagem no título. Para ter notificação, abra a
página por um túnel: `ssh -N -L 8765:<ip zerotier>:8765 server0` e `http://127.0.0.1:8765/`.

## 4.12 Converter um claude de terminal

Um `claude` interativo rodando em tmux (aberto por `claude-gmail` etc.) aparece como aba tracejada com
**⇄ converter**: a página dá `/exit` nele (ou Ctrl-C, ou SIGTERM, nessa ordem) e reabre a mesma conversa
como agente da página. No terminal, `converte-web` lista e converte em qualquer máquina.

## 4.13 Conjuntos: abrir vários agentes de uma vez

**⊞ conjuntos**, no topo ao lado do sino, abre a lista dos conjuntos salvos. Um conjunto é um JSON com
vários agentes para abrir juntos: os trabalhos que estavam em andamento (cada um na sua pasta e máquina,
conversa retomada) ou um grupo com um **professor líder**, em que cada agente sobe com um system prompt
próprio. Clique na linha para abrir; **⬇** baixa o JSON; **✕** (duas vezes) apaga. Em cima, **📄 enviar
JSON** recebe um arquivo pronto e **📷 salvar abas abertas** fotografa o que está aberto (máquinas,
pastas, contas, prompts, professor) num JSON que você confere, nomeia e salva. Os arquivos ficam em
`~/.config/orq-web/conjuntos/` do servidor (`ORQ_SETS_DIR`; no container, o volume `~/.config/orq-web`).

```json
{
  "nome": "artigo-kaggle",
  "descricao": "modelo no server0, texto no node3, professor coordena",
  "permissao": "auto",
  "agentes": [
    {"maquina": "server0", "pasta": "~/Projects/kaggle", "modo": "resume",
     "conta": "work3", "prompt": "Você cuida do modelo…", "tarefa": "Retome de onde parou e reporte."},
    {"maquina": "node3", "pasta": "~/Projects/paper", "modo": "new", "prompt": "Você escreve o artigo…"}
  ],
  "lider": {"receita": "professor-executores", "prompt": "opcional, substitui o papel do TOML", "tarefa": "…"}
}
```

- Só `nome` e `agentes` (com `maquina` e `pasta`) são obrigatórios. `modo` padrão é `resume` (a conversa
  mais recente da pasta; nova se não houver). `conta` fixa a conta; sem ela, a escolha pela folga é feita
  para o conjunto inteiro de uma vez, o que distribui a carga entre as contas. `permissao` (`auto`,
  `manual`, `acceptEdits`, `plan`) vale para todos; sem ela, o `permission_mode` da máquina.
- `prompt` vai no system prompt do agente (`--append-system-prompt`, como o papel de um nível); num
  agente retomado ele entra numa conversa que já tinha contexto, então grupos com professor costumam
  ficar mais limpos com `modo: new`. `tarefa` é a primeira mensagem, enviada assim que o agente sobe.
- **Sem `lider`**, os agentes são independentes. **Com `lider`**, o professor sobe acima do primeiro
  agente da lista e os demais são vinculados a ele (🔗) e viram executores, exatamente como ⇅ com a
  receita `professor-executores` (4.8 e 4.9); `lider.prompt` substitui o papel do TOML (aceita
  `{abaixo}`, `{eu}`, `{executores}`) e `lider.tarefa` vai ao professor depois das tarefas dos agentes.
- Se qualquer `maquina:pasta` do conjunto já tiver um agente vivo (da página ou de terminal), a página
  **recusa e diz quais**: feche-os ou abra só os demais à mão. A lista mostra "N já abertos" na linha.
- Abrir leva de alguns segundos a um minuto: cada agente sobe por ssh (em paralelo entre máquinas, em
  sequência na mesma), depois vêm os vínculos, o professor e as tarefas. As abas aparecem conforme sobem
  e os toasts contam cada etapa; o último diz quantos subiram e lista problemas, se houver. A barra de
  estado de um agente aberto assim mostra `⊞ <conjunto>`.
- Reabrir uma pasta cujo agente foi encerrado sem **↓ desfazer** (por fora da página, ou porque o claude
  caiu) encerra os níveis órfãos que ainda apontavam para aquele rótulo, tanto pelo **+** quanto por um
  conjunto; senão o professor antigo grudaria no agente novo e o ⇅ recusaria com "já tem níveis".
- A fotografia pega todas as abas abertas: se alguma tem níveis acima, ela vira a primeira e o `lider`
  recebe a receita (só o primeiro professor; os outros agentes viram executores dele); `prompt` só
  aparece em agentes que subiram com um. O JSON fica editável antes de salvar.
