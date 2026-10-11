# Handover — 2026-10-11

Para a próxima sessão do Claude Code, aberta em `~/Projects/claude-orq` (este monorepo). A sessão
anterior rodava em `~/Projects/claude-multiconta`, por isso aquela pasta ainda não foi apagada.

## Onde as coisas estão

| o quê | onde |
|---|---|
| código (fonte de verdade) | `~/Projects/claude-orq`, remoto `https://github.com/cmarcond/orq` (privado). CI por projeto em `.github/workflows/`; imagem `ghcr.io/cmarcond/orq-web:latest` |
| página pela rede | container `orq-web` no server0, `http://10.0.0.10:8765/`. Senha: `ssh server0 'cat ~/.config/orq-web/password'` (com aspas). Deploy: `orq-web/deploy/push-server0.sh` |
| página local | `orq web` (atalho do claude-multiconta) ou `orq-web`, em primeiro plano no terminal |
| pacote nas máquinas | `~/.local/lib/orq-web` + wrappers `orq-agent`, `orq-web`, `converte-web` em `~/.local/bin`; instalados por `orq-web/install.sh` / `orq-web/deploy.sh` (sem pip) |
| contas e utilitários | `claude-multiconta/`: `install.sh`, `usage-all`, `orq`, `claude-last`; distribuição por `make-tarball.sh` + `install.sh --no-install` em cada máquina |
| plano das fases | `orq-web/docs/PLANO.md`: fases 1 e 2 feitas; **fase 3 (níveis professor/executor) é a próxima** |
| memória do Claude | `~/.claude-gmail/projects/-Users-user-Projects-claude-multiconta/memory/` (da sessão antiga; a sessão nova terá a sua) |

Máquinas (`orq_hosts` do `accounts.json`): server0, node0, node1, node2, node3; mais o laptop,
alcançável do server0 por ssh pelo ZeroTier (`10.0.0.11`). No laptop, agentes por ssh só funcionam
dentro do servidor tmux da sessão gráfica (LaunchAgent `io.orq-tmux-keep`), por causa do Keychain.

## Limpeza combinada (fazer logo ao abrir a sessão nova)

Arquivar em `server0:~/OldProjects/` como `<máquina>-<nome>-20261011.tar.gz` e **apagar os originais**.
Inventário feito em 2026-10-11 (node0 fora do ar na hora):

| máquina | apagar depois de arquivar |
|---|---|
| laptop | `~/Projects/orq-web`, `~/Projects/claude-multiconta`, `~/Projects/claude-highlevel`, `~/Projects/claude-multiconta-2026100{5,7,9}.tar.gz`, `~/Projects/highlevel-20260831.tar.gz` |
| server0 | `~/orq-web` (o `push-server0.sh` recria quando precisar), `~/claude-multiconta`, `~/Projects/claude-highlevel` (44 M, inclui `.venv` e `runs`), `~/claude-highlevel-20260831.tar.gz`, `~/claude-multiconta-20260925.tar.gz` |
| node1 | `~/claude-multiconta` |
| node2 | `~/claude-multiconta`, `~/claude-multiconta-20260925.tar.gz` |
| node3 | `~/claude-multiconta`, `~/Projects/claude-highlevel` (34 M), `~/claude-highlevel-20260831.tar.gz`, `~/claude-multiconta-20260925.tar.gz` |
| node0 | verificar quando voltar (provavelmente `~/claude-multiconta` e tarballs) |

Cuidados:
- No laptop, antes de apagar `~/Projects/orq-web`, trocar a instalação editável do pacote:
  `uv pip uninstall --system orqweb && uv pip install --system -e "~/Projects/claude-orq/orq-web[dev]"`
  (os wrappers de `~/.local/bin` não dependem disso, mas `/opt/homebrew/bin/orq-web` sim).
- As máquinas remotas continuam funcionando sem `~/claude-multiconta`: o `install.sh` já copiou tudo para
  `~/.local/bin`. A próxima distribuição recria a pasta ao extrair o tarball.
- Conferir cada tarball (`tar tzf … | wc -l`) antes de apagar a origem.
- Nada disso toca `~/.claude*`, `~/.config/claude-multiconta`, `~/.config/orq-web` nem `~/.cache/orq`.

## Pendências

1. **Token do GitHub**: o `ghp_…` atual foi colado no chat da sessão anterior. Regenerar em
   `https://github.com/settings/tokens` com `repo`, `workflow`, `read:org` (+ `read:packages`) e
   `pbpaste | gh auth login -h github.com -p https --with-token`. O `git` usa o `gh` como credencial
   (`gh auth setup-git` já feito).
2. **node0** fora do ar: quando voltar, `claude-multiconta/make-tarball.sh` + `install.sh --no-install` lá,
   `orq-web/deploy.sh node0`, reabrir `acervo@node0` (ainda no supervisor antigo) e fazer a limpeza acima.
3. **Fase 3** do plano: níveis dentro do orq-web (todo agente com `-n <rótulo>`, receitas `.toml`,
   upgrade/downgrade, painel de fios). Viabilidade já testada: `SendMessage`/`ListAgents` funcionam entre
   sessões `claude -p`.
4. Duas sessões tmux soltas no laptop (`claude-work2-claude-multiconta`, `claude-user`) são duplicatas
   de conversas abertas; podem ser fechadas com `/exit` (a sessão nova não roda dentro delas).

## Como testar cada parte

```sh
cd ~/Projects/claude-orq/orq-web && make test          # pytest + node --test
cd ~/Projects/claude-orq/claude-multiconta && python3 -m pytest -q tests
cd ~/Projects/claude-orq/claude-highlevel && python3 -m pytest -q -m "not slow"
gh run list --repo cmarcond/orq --limit 5       # CI
ssh server0 'docker logs orq-web 2>&1 | tail -20'    # servidor pela rede
```
