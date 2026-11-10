# 1. Visão geral

## O problema

Um Claude Code só atende um terminal, uma conta e uma máquina por vez. Quem trabalha com várias contas
(cada uma com a própria cota de 5 horas e de 7 dias), várias máquinas (um laptop, servidores com GPU,
um NAS) e vários projetos ao mesmo tempo passa o dia entre janelas de terminal, `ssh`, `tmux attach` e
a pergunta "qual conta ainda tem cota?". O claude-orq resolve isso em três camadas, cada uma usável
sozinha:

1. **claude-multiconta**: cada conta do Claude Code vive num diretório próprio (`CLAUDE_CONFIG_DIR`),
   com aliases para abrir, logar e ver o uso de cada uma. O `usage-all` mostra a cota de todas, com um
   marcador de ritmo e uma linha TOTAL da semana da frota. O `orq` abre até quatro projetos numa grade
   tmux, escolhendo a conta com mais folga para cada um.
2. **orq-agent**: um supervisor que roda o Claude Code sem terminal (`claude -p` em stream-json, o
   protocolo do Agent SDK) na pasta e na conta certas, atrás de um socket Unix, numa sessão tmux da
   máquina. Sobrevive à queda do ssh; a conversa é uma sessão normal do Claude Code.
3. **orq-web**: um servidor e uma página que mostram a cota, uma aba por agente de qualquer máquina e um
   chat no estilo do Remote Control. Pela página você abre projetos, conversa, responde permissões e
   perguntas, manda e recebe arquivos, sobe níveis (professor → executor), vincula agentes entre si e
   deixa a troca de conta acontecer sozinha quando a cota acaba.

Nada depende de serviço externo além do próprio Claude Code: Python 3.8+ da biblioteca padrão nas
máquinas, módulos ES no navegador, ssh e tmux entre as máquinas.

## As peças e onde rodam

```
                 laptop (Mac)                             server0 (Linux, ZeroTier)
  ┌──────────────────────────────────┐        ┌─────────────────────────────────────────┐
  │ terminal: claude-<conta>, orq,   │        │ container orq-web (só Python + ssh)     │
  │ usage-all, claude-resume         │        │   página em http://<ip zerotier>:8765/  │
  │                                  │  ssh   │   fala com TODAS as máquinas por ssh,   │
  │ orq-agent (agentes locais)  ◄────┼────────┤   inclusive o laptop e o próprio host   │
  │   em sessões tmux orqs-<nome>    │        │                                         │
  │                                  │        │ orq-agent (agentes do server0)        │
  │ navegador ───────────────────────┼───────►│   em sessões tmux orqs-<nome>           │
  └──────────────────────────────────┘        └─────────────────────────────────────────┘
                                                        │ ssh
                                              node0, node1, node2, node3 (orq-agent)
```

- O **servidor da página** roda hoje em container no server0, publicado só no IP do ZeroTier, com
  login por senha. Ele não tem `claude` nem credenciais: tudo que precisa de uma conta roda nas máquinas,
  por ssh. A cota é lida por ssh no server0 (`ORQ_USAGE_HOST`). Um `orq-web` local no laptop também
  funciona (só `127.0.0.1`, token no link), mas por decisão do autor não fica no ar, para não manter
  dois lugares.
- O **supervisor** (`orq-agent`) está instalado em todas as máquinas, inclusive no laptop, porque o
  container chega nele por ssh.
- Os **utilitários do claude-multiconta** (`usage-all`, `claude-last`, `orq`) também estão em todas as
  máquinas: o servidor os chama lá para listar projetos, ler a cota e saber quais agentes existem.

## Conceitos

| termo | o que é |
|---|---|
| **conta** | uma assinatura do Claude Code, identificada pelo `name` no `accounts.json` (`work`, `gmail`, `work2`, `work3`). Cada uma tem um `config_dir`; a padrão usa `~/.claude` e é a que o `claude` puro abre. |
| **máquina (host)** | um nome de `~/.ssh/config`: `server0`, `node0`…, listados em `orq_hosts`. O laptop é sempre `laptop`. |
| **projeto** | uma pasta numa máquina. É onde o Claude Code trabalha e onde ele guarda a conversa (`<config_dir>/projects/<pasta>/`). |
| **agente** | um Claude Code rodando sob o `orq-agent`, numa pasta e numa conta. Na página é uma aba. |
| **rótulo (label)** | o nome do agente: `<pasta>@<máquina>`, por exemplo `kaggle@server0`. Um nível de cima vira `kaggle~professor@server0`. Rótulo repetido na mesma máquina ganha `-2`, `-3`. |
| **nome de sessão** | o rótulo saneado, sem `@` e `~` (`kaggle-server0`, `kaggle-professor-server0`): é o `-n` com que o claude sobe e o nome que o `SendMessage` aceita. |
| **estado do agente** | `starting`, `working`, `waiting` (sua vez), `permission` (pede permissão ou faz pergunta), `error`, `quota` (conta esgotada, trocando), `closed`. |
| **janela de cota** | a API da Anthropic reporta, por conta, uma janela de 5 h (`sessão 5h`), uma de 7 dias para todos os modelos (`semana 7d`) e uma de 7 dias por modelo. |
| **ritmo** | a fração da janela que já passou. Uma conta está "no ritmo" quando o uso acompanha o tempo. |
| **nível** | um agente acima de outro (professor, arquiteto), criado por uma receita, que conversa com o de baixo pelo `SendMessage`. |
| **vínculo** | dois agentes quaisquer que se enxergam no `ListAgents` e se falam pelo `SendMessage`, na mesma máquina ou não. |
| **_inbox / _outbox** | pastas na raiz do projeto: o que você manda ao agente pela página cai em `_inbox/`; o que ele compartilha com você fica em `_outbox/`. |

## Uma sessão típica

1. Você abre a página da rede e vê, em cima, a cota de cada conta e a linha TOTAL da semana. Em baixo,
   as abas dos agentes que já existem, uma linha por máquina.
2. **+** na linha do server0: a lista mostra as pastas com conversa recente em todas as máquinas. Você
   escolhe `~/Projects/kaggle`. O servidor escolhe a conta com mais folga, sobe o `orq-agent` lá por ssh
   (numa sessão tmux `orqs-kaggle`), e a aba `kaggle@server0` aparece.
3. Você escreve. O texto vai pela ponte até o socket do supervisor, que o entrega ao `claude -p`. A
   resposta chega palavra a palavra; ferramentas aparecem como chips dobráveis; uma permissão vira um
   cartão com Permitir/Negar; uma pergunta do `AskUserQuestion` vira um formulário.
4. Você arrasta um PDF para o chat: ele vai para `_inbox/` da pasta, na máquina, e a mensagem sai com o
   caminho. O agente gera um relatório e o copia para `_outbox/`: o 📂 avisa e você vê a prévia.
5. A conta esgota no meio do trabalho. O supervisor marca `quota`, o servidor reabre o agente na conta
   com mais folga, copiando a conversa, e reenvia a sua última mensagem. A aba continua; um toast avisa.
6. Você sobe um professor acima do agente (**⇅ subir nível**): a aba passa a falar com ele; ele delega ao
   executor pelo `SendMessage` e explica os marcos. Ou vincula o agente a outro, noutra máquina (**🔗**),
   e um pede coisas ao outro.
7. Você fecha o laptop. Os agentes continuam nas sessões tmux das máquinas; amanhã a página mostra tudo
   onde estava.

## Histórico, em uma linha por etapa

claude-multiconta (contas e `usage-all`) → `orq` de terminal (grade tmux) → `orq web` (página local)
→ `orq-web` como projeto próprio, com supervisor e testes → fases 1 a 4 (`orq-web/docs/PLANO.md`:
rede e container, monorepo e CI, níveis vindos do claude-highlevel, troca de conta automática) →
QA com Playwright → arquivos compartilhados (`_outbox/`) → vínculos entre agentes e entre máquinas →
professor com vários executores → revisão adversarial dos vínculos.
