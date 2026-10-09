# orq-web

Chat local com agentes do Claude Code espalhados por várias máquinas e contas.

- **`orq-agent`** (supervisor): roda o `claude -p` em stream-json, o protocolo do Agent SDK, na
  pasta e na conta certas, guarda os eventos numerados e os expõe num socket Unix. Sobrevive à
  queda da conexão; quem reconecta recebe o que perdeu.
- **`orq-web`** (servidor + página): cota das contas em cima, uma aba por agente, e um chat com
  Markdown, ferramentas dobráveis, permissões por botão e formulários para `AskUserQuestion`.

Extraído do projeto `claude-multiconta`, que continua dono das contas (`accounts.json`) e dos
utilitários de cota e de histórico que este pacote chama.

```sh
pip install -e ".[dev]"
make test
orq-web            # http://127.0.0.1:8765/?t=<token>
```
