# 6. Vínculos e níveis: como duas sessões do Claude Code se falam

## 6.1 O canal nativo e o registro de sessões

O Claude Code tem um canal entre sessões: as ferramentas `ListAgents` (quem está vivo) e `SendMessage`
(mandar texto a uma sessão pelo nome). A mensagem chega na outra sessão como uma mensagem de usuário
embrulhada em `<cross-session-message>`, com `origin = {kind: "peer", name, body, msg_id}`, e reabre o
turno dela. O orq usa esse canal para tudo entre agentes: níveis e vínculos.

Como as sessões se acham: cada claude vivo grava em `<config_dir>/sessions/` um **cartão**
`<pid>.json` (nome dado por `-n`, pasta, estado, versão, `messagingSocketPath` = o socket Unix em que
recebe mensagens, `/tmp/cc-socks/<pid>.sock` no Mac ou `/run/user/<uid>/cc-socks/<pid>.sock` no Linux,
mais `procStart` e `pidDomain` para detectar cartão de sessão morta) e um arquivo
`<pid>.<hash>.key` com o `peerToken`, que quem manda mensagem apresenta ao socket. O `ListAgents` lê a
pasta `sessions/` da **própria conta**; o `SendMessage` fala com o socket. Três consequências:

1. duas sessões da mesma conta, na mesma máquina, se enxergam sozinhas (é o caso dos níveis);
2. duas sessões de **contas diferentes** não se enxergam, mesmo na mesma máquina (descoberto no QA dos
   vínculos: o orq-qa na Work 3 não via o orq-qa2 no Gmail);
3. duas sessões em **máquinas diferentes** não se enxergam, e o socket é local.

Os dois últimos casos são resolvidos por **espelho**: copiar o cartão e a chave de uma sessão para a
pasta `sessions/` da outra, com o `messagingSocketPath` apontando para um socket que alcance a sessão
de origem. Na mesma máquina, o próprio socket dela. Entre máquinas, um socket encaminhado por ssh.
O cartão espelhado leva uma marca `_orq_mirror: <rótulo de origem>`, que o orq nunca confunde com uma
sessão local, e o `pidDomain` diferente não fez o `ListAgents` descartá-lo (testado).

## 6.2 Nomes de sessão

Todo agente da página sobe com `claude -n <rótulo saneado>`: `kaggle@server0` → `kaggle-server0`,
`kaggle~professor@server0` → `kaggle-professor-server0` (o `SendMessage` recusa `@`). É esse nome
que vai nos papéis das receitas (`{acima}`, `{abaixo}`, `{eu}`, `{executores}`) e nas notas de vínculo.
Um agente aberto antes dessa versão não tem nome; a página oferece **↻ reabrir com nome**.

## 6.3 Níveis: receitas em TOML

`orqweb/pipelines/*.toml` descreve, do topo para baixo, os níveis que sobem **acima** do agente da aba
(ele é sempre o executor e não ganha papel novo):

```toml
titulo = "professor → executor"
descricao = "…"
[[niveis]]
nome = "professor"
cor = "#2b6cb0"
papel = """Você é o PROFESSOR … delegue para `{abaixo}` … marcos via SendMessage para `{eu}` …"""
```

`plan_levels` resolve os marcadores: `{acima}` e `{abaixo}` são os nomes de sessão dos vizinhos (o de
baixo do último nível é o agente da aba), `{eu}` o nome do próprio nível, e `{executores}` (só no nível
mais baixo, em receitas *multi*) a lista pronta `- \`nome\`: máquina X, pasta Y` do agente da aba e
dos vinculados a ele. Cada nível sobe com `orq-agent spawn --mode new --role-file - --parent <de
baixo> --level <nome>`; o papel viaja pelo stdin do ssh e fica em `role.txt` no estado do agente, para
um `--restart` (troca de conta) reaproveitá-lo. O `state.json` guarda `parent` e `level`; a página
reconstrói a cadeia seguindo `parent` e esconde os níveis atrás da aba do base.

Papéis que funcionaram, na prática: o professor nunca executa, delega com `SendMessage`, pede marcos
curtos (PLANEJANDO, IMPLEMENTANDO, PROBLEMA, VALIDANDO, CONCLUÍDO) mandados ao nome dele, e usa
`AskUserQuestion` para perguntar à pessoa (texto com "qual dos dois?" não chega como pergunta). O
executor costuma responder ao endereço `uds:/tmp/cc-socks/<pid>.sock` em vez do nome; a página mostra
como "resposta". Todos os níveis sobem no mesmo `permission_mode`: modos diferentes fazem o Claude Code
reter a mensagem entre eles.

Lições do claude-highlevel (o projeto anterior, com PTY): são as mesmas duas sessões conversando pelo
canal nativo; o que mudou foi tirar o PTY e a tela própria e usar o supervisor e a página do orq-web.

## 6.4 Vínculos: `orq-agent link`

Um vínculo é um par de agentes que se enxergam. `orq-agent link A B` roda na máquina de `A`:

**Mesma máquina** (`same_host`): sem processo. `local_link` acha o cartão de cada um na pasta
`sessions/` da conta dele (esperando até 40 s, porque um agente recém-aberto demora a registrar) e, se
as contas forem diferentes, grava o cartão e a chave de cada um na pasta do outro, com o mesmo socket.
O arquivo do vínculo guarda `sessions` (os pids das duas sessões): se um agente for fechado e reaberto, o
vínculo conta como morto, e o `orq-agent links` seguinte recolhe os espelhos.

**Máquinas diferentes**: um processo `orq-agent link-run A B` numa sessão tmux `orql-<a>--<b>`:

1. acha o cartão local de `A`; pede à outra máquina, por ssh, `orq-agent peer B --probe <pid de A>`, que
   devolve o cartão de `B` (com a chave) e diz se já existe lá, vivo, um socket encaminhado para `A`;
2. decide os encaminhamentos: `-L <aqui>/orq-<pid B>.sock:<lá>/<pid B>.sock` se não há aqui um socket
   encaminhado vivo para `B`; `-R <lá>/orq-<pid A>.sock:<aqui>/<pid A>.sock` se não há lá um para `A`.
   O socket encaminhado tem nome próprio (`orq-<pid>.sock`) para nunca se confundir com o de uma
   sessão real (pids se repetem entre máquinas);
3. abre `ssh -N` com esses encaminhamentos (`ExitOnForwardFailure`, `ServerAlive`, `StreamLocalBindUnlink`;
   stderr no log do vínculo), espera o socket local aceitar conexão;
4. grava o espelho de `B` aqui e manda o de `A` para lá (`peer B --mirror --sock … --from A`, com o
   cartão e a chave pelo stdin do ssh);
5. escreve o arquivo do vínculo e fica de pé: a cada 5 s confere o túnel, o cartão e o pid de `A`, e o
   socket encaminhado; a cada 30 s sonda a outra máquina (`B` fechou? foi reaberto com outro pid?). Sai
   no `unlink` (SIGTERM), em SIGHUP, se um dos dois fechar ou se o túnel cair, sempre passando pela
   limpeza, inclusive em erro inesperado.

**Recursos compartilhados.** Espelhos e sockets encaminhados são por (pasta `sessions/`, pid), não por
vínculo: o agente da aba e o professor acima dele, na mesma conta, ambos ligados ao mesmo executor
remoto, usam o mesmo cartão de `B` e o mesmo socket `orq-<pid B>.sock`. Cada arquivo de vínculo lista
os recursos de que depende (`card:<pasta>/<pid>`, `sock:<caminho>`, `remote:<máquina>:<pasta>/<pid>`),
e `release()` só apaga o que nenhum outro vínculo vivo desta máquina usa. Um encaminhamento que já está
de pé é reaproveitado; se o vínculo que o mantinha sai, o que sobrou nota o socket morto no próximo
ciclo e refaz o túnel com o que já era dele mais o que faltou. Ao sair, o vínculo primeiro se marca
como fora, para os outros não contarem com ele. Essa foi a correção mais importante da revisão
adversarial: antes, o segundo vínculo destruía o espelho do primeiro.

**Segurança.** O `.key` é a credencial do socket: só viaja por ssh, é gravado com modo 0600 e o nome
é validado (`<pid>.<hex>.key`); escrever ou apagar espelho nunca toca num cartão que não seja espelho.
O orq não lê nem imprime o token; o assistente que ajudou a desenvolver isto não conseguia sequer
copiar os arquivos, por política, e o próprio supervisor faz tudo.

## 6.5 O servidor e a página

`POST /api/agent/<A>/link {to: B}`: valida que os dois são agentes da página vivos, roda `orq-agent
link A B` na máquina de `A`, registra o vínculo no estado (até a máquina o listar) e entrega a cada um
uma nota `[orq] Vínculo criado com o agente …` (uma mensagem de usuário injetada; a página a mostra como
chip de sistema). `unlink` faz o inverso, por qualquer dos dois lados, e avisa quem sobrou. O estado
dos vínculos vem das máquinas junto com a lista de agentes (`orq-agent links`, na mesma conexão ssh que
o `orq --status-json`); `host` no registro é sempre a máquina onde o vínculo roda (o lado local).

A receita `professor-executores` usa isso: ao subir, o nível de baixo é vinculado a cada agente
vinculado ao da aba; **↓ desfazer** desfaz esses vínculos antes de encerrar o nível. Na página, a vista
**vínculos** mostra cartões e fios; a aba, `🔗n`.

## 6.6 Limitações conhecidas

- O aviso de ociosidade (`notify_when_idle` do `SendMessage`) depende de um registro que o espelho não
  satisfaz: a entrega passa, o aviso de "terminei" talvez não. Os papéis pedem marcos explícitos.
- Um vínculo por ssh precisa que a máquina de `A` alcance a de `B` por ssh com esse nome. Do laptop para
  as máquinas isso vale; o inverso depende do `~/.ssh/config` de cada uma.
- Migração de conta de um agente vinculado não refaz o vínculo (o agente reabre com outro pid; o vínculo
  cai e precisa ser feito de novo).
- `orq-agent links` lista vínculos desfeitos para sempre (filtrados na página).
