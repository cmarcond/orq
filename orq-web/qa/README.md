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
pelo antigo).

```sh
cd orq-web
uv pip install --python .venv/bin/python -e ".[qa]" && .venv/bin/python -m playwright install chromium
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
