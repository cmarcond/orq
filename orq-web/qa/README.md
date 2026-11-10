# QA do orq-web (Playwright)

Testa a **aplicação de verdade**, com o **claude real**, pelo navegador: um orq-web só do QA sobe na
porta 8790 (ou aponta-se para a página da rede), um Chromium sem janela abre a página, e cada teste
deixa em `qa/out/` a captura de tela final, o console e um **vídeo da sessão inteira**
(`<teste>.mp4`, ou `.webm` sem ffmpeg; `-FALHOU` no nome quando falha). Testes simples: a página abre,
o seletor lista projetos, um agente responde, uma interrupção funciona (`test_chat.py`), os níveis
professor → executor entregam o hello world do HighLevel (`test_niveis.py`: o professor delega pelo fio,
o executor cria `hello.py` na pasta do QA e reporta, o professor explica; ↓ desfazer no fim), e a troca
automática de conta continua a conversa (`test_troca_conta.py`: não dá para esgotar a cota sob demanda,
então o QA grava `state: quota` no state.json do agente, como o supervisor faria; daí o servidor reabre
o agente noutra conta, reenvia a pergunta pendente e o claude novo responde a palavra-chave guardada
pelo antigo). Os arquivos compartilhados (`test_arquivos.py`): o primeiro teste grava direto na
`_outbox/` da pasta do QA e confere lista, toast, prévia de Markdown e imagem e download, sem gastar
cota; o segundo pede ao claude real que crie `resumo.md` e "compartilhe pela página", sem citar a
pasta, e espera o arquivo aparecer na lista (prova que a nota do system prompt basta). A página em si
(`test_pagina.py`): a aba 📊 uso (5 h, 24 h, 7 dias) e o painel da conta pelo cartão de cota, com os selos de
alerta, a partir do banco do usage-hist; a versão para celular (`/m`, viewport de iPhone: faixa do TOTAL que abre as
contas, seletor e aba uso na tela estreita); o sino 🔔 com e sem permissão; o título `(1) orq` e a notificação
quando um agente termina com a página fora de vista (um turno curto); e os atalhos ⌘n, Shift+Enter e Esc (abre
dois agentes, não manda nada). Só os dois últimos abrem agentes; os quatro primeiros não gastam cota. O caminho de ida dos arquivos (`test_anexos.py`): soltar arquivo sem aba
aberta avisa (sem cota); e um CSV pelo 📎 (seletor do navegador) mais uma nota arrastada (um `DragEvent` com o
`File`, o mesmo que a página recebe) entram na fila de chips, um sai pelo ✕ e volta, o envio grava os dois em
`_inbox/` na máquina do agente e a mensagem sai com os caminhos; o claude real lê os dois de lá e responde a soma e
a palavra-chave (um turno); no fim os bytes são conferidos por ssh numa conexão só. ⇄ converter (`test_converter.py`): sobe um `claude` interativo de
verdade num tmux desta máquina, na pasta do QA e na conta `ORQ_QA_ACCOUNT` (senão a primeira que não é a padrão),
aceita o diálogo de confiança da pasta, pede pelo tmux que guarde uma palavra-chave, espera a página listá-lo como
aba tracejada, converte pelo botão do painel (`/exit` no tmux, mesma conversa reaberta como agente) e pergunta a
palavra-chave ao agente novo: ele lembra. Dois turnos; só roda quando `ORQ_QA_PROJECT` está nesta máquina. Permissões e perguntas
(`test_permissoes.py`): o `AskUserQuestion` vira formulário no chat e o QA responde escolhendo uma opção, depois
pelo "Outro…" e por fim cancela (três turnos curtos); e um conjunto de um agente só com `permissao: manual` pede
permissão para gravar um arquivo, o QA nega (nada é gravado, o claude diz que foi negado) e depois permite (dois
turnos). Nos seletores, `.perm` sem `.term`: o painel do claude de terminal também é um `.perm`. Reconexão (`test_reconexao.py`): com o claude
contando até 100, recarregar a página traz a conversa inteira de volta (stream desde 0) e o turno continua ao vivo;
derrubar e subir o orq-web do QA no meio da resposta faz a aba reconectar pedindo `since=N` (só o que faltou) e nada
se repete no chat. Dois turnos. Três níveis (`test_niveis.py::test_professor_arquiteto_executor`): a receita
`professor → arquiteto → executor` com o mesmo hello world; o professor delega ao arquiteto, o arquiteto instrui o
executor, o CONCLUÍDO sobe executor → arquiteto → professor, o cartão do arquiteto abre o chat cru dele, e ↓ desfazer
encerra os dois níveis. Vários turnos. Caminho remoto (`test_remoto.py`, `ORQ_QA_PROJECT_REMOTE`, padrão
`laptop:~/Projects/orq-qa`, numa máquina que não é a do servidor): o agente remoto compartilha um arquivo (lista,
prévia e download passam por `orq-agent ls`/`cat` por ssh); um agente daqui se vincula ao remoto (vínculo `ssh`,
túnel do `orq-agent link`) e pergunta a pasta dele pelo SendMessage; e um conjunto abre um agente em cada máquina com
a própria tarefa. Uns 5 turnos; pula quando a pasta remota está na máquina do servidor. Login (`test_login.py`): um segundo orq-web só do teste sobe
na porta `ORQ_QA_LOGIN_PORT` (8791) com `ORQ_PASSWORD_FILE` descartável; sem senha não se entra (página e API),
senha errada avisa, cinco erros bloqueiam por um minuto, a senha certa volta para a página pedida (`/m`) e dá o
cookie à API, e sair invalida o cookie. Sem cota. 📄 enviar JSON (`test_conjuntos.py::test_enviar_json_pelo_botao`): o botão abre o
seletor de arquivos do navegador, o JSON vira conjunto salvo (nome do arquivo quando falta `nome`), ⬇ baixa o mesmo
JSON, JSON quebrado e conjunto inválido só avisam, ✕ duas vezes apaga. Sem cota. ↻ reabrir com nome (`test_reabrir.py`): tira o `name` do state.json de um agente
real (como um agente de antes dos nomes), a página passa a oferecer ↻ no ⇅ em vez das receitas, o agente reabre na
mesma conversa com pid novo e nome, lembra a palavra-chave guardada antes, e o ⇅ volta a oferecer as receitas.
Dois turnos curtos. Esse segundo
teste usa o `orq-agent` instalado em `~/.local` (é o que o servidor do QA sobe): rode `./install.sh`
antes se mudou o agente.
Vínculos (`test_vinculos.py`): dois agentes reais no laptop, 🔗 pela página, um pergunta ao outro a
pasta de trabalho pelo SendMessage e recebe a resposta pelo fio. Professor com vários executores
(`test_professor_executores.py`): os mesmos dois, vinculados, ganham um professor pela receita
`professor-executores`, que pergunta a cada um a pasta e explica as duas; ↓ desfazer limpa o vínculo
do professor e deixa o dos executores. Os dois usam o `orq-agent` instalado (`./install.sh` antes).

```sh
cd orq-web
uv pip install --python .venv/bin/python -e ".[qa]" && .venv/bin/python -m playwright install chromium
# Ubuntu 20.04 (server0): o Playwright novo não tem navegador para ele; use playwright==1.48.0 e --with-deps
.venv/bin/python -m pytest qa                 # tudo (test_chat gasta um pouco de cota)
.venv/bin/python -m pytest qa/tests/test_smoke.py   # só o que não gasta cota
```

Variáveis: `ORQ_QA_URL` + `ORQ_QA_PASSWORD` testam a página da rede (`http://10.0.0.10:8765/`)
em vez de subir um servidor; `ORQ_QA_VIA=server0` chega a ela por um túnel ssh (`make qa-rede`) e, sem
`ORQ_QA_PASSWORD`, lê a senha em `~/.config/orq-web/password` dessa máquina na mesma conexão — preciso
no macOS, onde a privacidade de rede local só deixa binários da Apple (curl, ssh) alcançarem o endereço
ZeroTier: o Chromium do Playwright e o python recebem "No route to host" (`ORQ_QA_TUNNEL_PORT`, 18765);
`ORQ_QA_PORT` (8790) e `ORQ_QA_KEEP=1` (não derruba o servidor no fim);
`ORQ_QA_PROJECT` (`laptop:~/Projects/orq-qa`, pasta descartável onde o agente abre); `ORQ_QA_HEADED=1`
mostra o navegador.

## Dirigir a página à mão (`qa/orqqa.py`)

Cada comando abre a página, faz uma coisa, grava `qa/out/<comando>-<hora>.png` e `.console.txt` e
imprime o resultado. Serve para injetar textos e comandos num agente e olhar o que a página fez:

```sh
P=.venv/bin/python
$P qa/orqqa.py up                               # sobe o orq-web do QA (porta 8790) e mostra o link
$P qa/orqqa.py state                            # agentes, estados, cota, aba atual
$P qa/orqqa.py open laptop:~/Projects/orq-qa    # abre um projeto (pasta nova = conversa nova)
$P qa/orqqa.py say "liste os arquivos desta pasta"   # envia e espera a resposta
$P qa/orqqa.py allow                            # permite a ferramenta pendente (deny nega)
$P qa/orqqa.py esc                              # interrompe o turno
$P qa/orqqa.py shot antes-do-stop               # captura com esse nome
$P qa/orqqa.py eval "document.querySelectorAll('.chat.on .ai').length"
$P qa/orqqa.py text "#cstate"                   # texto de um seletor
$P qa/orqqa.py click ".tab.plus[data-host=laptop]"
$P qa/orqqa.py watch 20                         # fica 20 s ouvindo console/erros/requisições falhas
$P qa/orqqa.py levels                           # ⇅ subir nível (professor-executor) na aba atual
$P qa/orqqa.py wires                            # fios entre os níveis (→ delegou, ✓✓ entregue)
$P qa/orqqa.py undo                             # ↓ desfazer: encerra os níveis de cima
$P qa/orqqa.py quota "continue"                 # força "sem cota" na aba atual e espera a troca de conta
$P qa/orqqa.py stop orq-qa@laptop               # ✕ duas vezes
$P qa/orqqa.py down                             # derruba o orq-web do QA (agentes continuam)
```

`ORQ_QA_LABEL=rótulo` escolhe a aba antes de `say`, `esc`, `allow`, `deny`, `stop`, `shot`, `text` e
`eval`; `ORQ_QA_VIDEO=1` grava também um vídeo do comando. Com `ORQ_QA_URL`, os mesmos comandos valem
para a página da rede.

## O que a biblioteca sabe fazer (`orqqa.Orq`)

`open_project("maquina:/caminho")`, `say(texto)` (espera a resposta e devolve o último texto do claude),
`wait_idle()`, `interrupt()`, `permission(allow)`, `upgrade(receita)`, `levels()`, `wires()`,
`wait_wire(nível, contém)` (um fio saído daquele nível: `professor` delegou, `executor` reportou),
`downgrade()`, `stop(rótulo)`, `ensure_stopped(rótulo)` (pela API),
`show(rótulo)`, `cstate()`, `last_ai()`, `messages()`, `labels()`, `state()`, `agents()`, `shot(nome)`,
`dump(nome)`, `problems()` (erros de página, requisições falhas, respostas >= 400).

Seletores da página que os testes usam: `.tab[data-l]` (aba; `.on` a atual; `.x` encerra; `.plus` abre),
`#cstate` (estado: `abrindo…`, `trabalhando…`, `esperando você`, `pede permissão`), `#input`/`#send`,
`.chat.on .me|.ai|.chip|.sys|.perm`, `#picker`/`#pnew`/`#popen`/`#plist .p`, `#toast`.
