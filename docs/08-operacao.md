# 8. Operação: testes, diagnóstico, pendências

## 8.1 Testes

```sh
cd orq-web
make test          # pytest (tests/) + node --test (tests/js/)
make lint          # ruff check + ruff format --check (linha 110, alvo py38)
make fmt
cd ../claude-multiconta && python3 -m pytest -q tests
```

Os testes do orq-web não precisam do Claude: `tests/fake_claude.py` imita o protocolo stream-json
(permissão, `AskUserQuestion`, interrupção, injeção, streaming, e `SendMessage` entre dois processos
falsos por uma caixa de correio em `FAKE_PEER_DIR`). `test_supervisor.py` cobre o supervisor pelo
socket; `test_handlers.py` sobe o servidor HTTP real com o supervisor atrás da ponte; `test_link.py`
usa sockets Unix de verdade e um `ssh -N` falso que escuta neles. Sockets Unix exigem caminhos curtos
(limite de ~104 bytes no macOS): esses testes usam `/tmp`.

## 8.2 QA com Playwright: a aplicação real, com o claude real

```sh
cd orq-web
uv pip install --python .venv/bin/python -e ".[qa]" && .venv/bin/python -m playwright install chromium
make qa-smoke                          # só o que não gasta cota (página abre, seletor lista)
make qa                                # tudo
make qa-rede                           # smoke contra a página da rede, por túnel ssh
ORQ_QA_HEADED=1 pytest qa/tests/test_arquivos.py   # com janela (grava o vídeo com o Chrome completo)
```

Cada teste sobe um `orq-web` só do QA na porta 8790 (ou aponta para `ORQ_QA_URL`), dirige a página num
Chromium e deixa em `qa/out/` a captura final, o console e o **vídeo da sessão** (`<teste>.mp4`;
`-FALHOU` no nome quando falha). Testes: `test_smoke.py`, `test_chat.py` (responde, interrompe),
`test_niveis.py` (hello world do professor → executor), `test_troca_conta.py` (força `state: quota` no
`state.json` e vê a conversa continuar noutra conta), `test_arquivos.py` (`_outbox/` com e sem claude),
`test_vinculos.py` (um agente pergunta ao outro), `test_professor_executores.py`. `qa/orqqa.py` é
também uma linha de comando para dirigir a página à mão (`up`, `open`, `say`, `allow`, `levels`,
`link`, `quota`, `shot`, `watch`…).

Contra a página da rede, o Chromium e o python do laptop **não alcançam** o IP do ZeroTier (a
privacidade de rede local do macOS só libera binários da Apple); `ORQ_QA_VIA=server0` abre um túnel
ssh e a senha vem de lá. Os testes que usam agentes reais sobem o `orq-agent` **instalado** em
`~/.local` (é o que o servidor chama): `./install.sh` antes, se mudou o agente. O Chromium sem janela
não desenha PDF; com `ORQ_QA_HEADED=1` sim.

## 8.3 Diagnóstico: o que já aconteceu e como se resolve

| sintoma | causa | o que fazer |
|---|---|---|
| segunda conexão ssh seguida à mesma máquina cai ("Connection closed") | rede instável; o segundo handshake logo após o primeiro falha | agrupar comandos numa conexão (é o que `External.agents` faz) ou esperar ~4 s |
| navegador/python do laptop: "No route to host" para o IP do ZeroTier; `curl` e `ssh` funcionam | privacidade de rede local do macOS para binários não-Apple a partir do tmux do launchd | túnel ssh (`ORQ_QA_VIA`, ou `ssh -N -L 8765:<ip>:8765 server0`) |
| cota de um Mac remoto não aparece; agente no Mac "deslogado" | sessões ssh não enxergam o Keychain | `deploy/macos-tmux-keep.sh`; a cota é lida com `tmux run-shell` |
| `claude-<conta>` sai na hora com lixo `rgb:` no zsh | o servidor tmux nasceu numa pasta que foi apagada | reiniciar o servidor tmux (LaunchAgent no Mac); os aliases já nascem em `~` |
| `usage-all` dá 429 | consultas frequentes à API de uso | esperar; a página consulta a cada 150 s |
| lista de arquivos diz "orq-agent antigo" | a máquina do agente tem um `orq-agent` sem `ls`/`cat`/`link` | `orq-web/deploy.sh <máquina>` |
| TOTAL não aparece no painel da rede | o `usage-all` da máquina de `ORQ_USAGE_HOST` é antigo | reinstalar o claude-multiconta lá (tarball + `install.sh --no-install`) |
| agente vinculado "não aparece como alcançável" no `SendMessage` | `ListAgents` só lê a pasta `sessions/` da própria conta; vínculo não feito pela página | vincular pela página (🔗); o orq espelha os cartões |
| vínculo mostrado como vivo depois de reabrir um agente | registro antigo sem os pids das sessões | registros novos guardam `sessions`; os antigos contam como mortos |
| nível sobe mas a página fica na vista "vínculos" | corrigido: ao subir níveis o painel troca para a vista de níveis | |
| `/api/agent/<x>/files` dá 404 logo depois de abrir um agente | a página pede antes de o servidor conhecer o agente | silencioso; a lista chega na tentativa seguinte (causa exata ainda não entendida) |
| página pula de aba durante a troca de conta | corrigido: o agente novo entra como provisório antes do `spawn` | |
| `/exit` não chega ao claude de terminal ao converter | o claude não respondeu | a conversão escala para Ctrl-C e SIGTERM |
| máquina fora do ar (`node0/1/2` em outubro de 2026) | | `orq-web/deploy.sh <máquina>` e tarball do claude-multiconta quando voltar |

Logs úteis: `~/.cache/orq/<agente>/supervisor.log` (o que o supervisor disse ao sair),
`~/.cache/orq/<agente>/events.jsonl` (tudo que o claude emitiu), `~/.cache/orq/<agente>/links/*.log`
(o vínculo e o stderr do ssh), `docker logs orq-web` no server0 (logins, migrações), `qa/out/orq-web-8790.log`.

## 8.4 Segurança, em resumo

- O servidor local só escuta em `127.0.0.1` com o token no link. Pela rede: senha, cookie, bloqueio por
  tentativas, porta publicada só no ZeroTier, `Host` conferido.
- O container não tem credenciais do Claude nem `claude`; alcança as máquinas por ssh com a chave do
  usuário (somente leitura) e um `ssh_config` próprio.
- Nada do que o modelo escreve roda na origem da página: Markdown sanitizado, CSP, arquivos da `_outbox/`
  em sandbox, HTML sempre como download.
- Credenciais de sessão (`.key`) só viajam por ssh e ficam com modo 0600; tokens de acesso nunca são
  impressos.
- O `permission_mode: auto` deixa o Claude decidir o rotineiro; `manual` pergunta tudo. Os agentes rodam
  com os poderes do usuário da máquina.

## 8.5 Pendências e ideias

Pequenas, anotadas durante o desenvolvimento:

- nos fios de um agente sem níveis, o agente base aparece como "executor" (`levelName`);
- `orq-agent links` lista vínculos desfeitos para sempre; podar os antigos;
- a receita `professor-executores` entre máquinas diferentes só tem teste unitário (o QA usa dois
  agentes do laptop);
- o 404 de `/files` logo após abrir um agente;
- migração de conta de um agente com vínculo não refaz o vínculo;
- `Range` para vídeo grande vindo por ssh funciona, mas é lido inteiro a cada pedido.

Maiores (fase 4 do `orq-web/docs/PLANO.md`): histórico de rodadas de níveis e linha do tempo de marcos
por projeto; modelo e esforço por nível na receita; receitas editáveis pela página; busca na lista de
projetos; painel de níveis melhor no celular; níveis na grade do `orq` de terminal.
