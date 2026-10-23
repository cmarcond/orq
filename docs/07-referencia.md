# 7. Referência

## 7.1 Linhas de comando

### claude-multiconta

| comando | uso |
|---|---|
| `claude`, `claude-<conta> [args]` | abre o Claude Code na conta, numa sessão tmux `claude[-<conta>]-<pasta>` |
| `claude-<conta>-login` | login sem navegador (imprime a URL; cole o código) |
| `claude-usage-<conta>` | `usage-all <conta>` |
| `claude-whoami` | e-mail e plano de cada diretório; qual é o `claude` puro |
| `usage-all [-v] [--json] [--ao-vivo] [conta…]` | cota de todas as contas + TOTAL, do banco do `usage-hist` quando fresco (`--ao-vivo` = API); `--json` para scripts |
| `usage-hist [painel\|conta NOME\|dia [data]\|semana\|calibrar\|alertas\|estado\|coletar\|instalar\|remover] [--conta C] [--json]` | histórico de uso no SQLite do node4: hoje, ritmo, previsão, por hora, por projeto (≈pts), alertas; `coletar` é o que o timer roda a cada 5 min |
| `usage-sessoes [-v] [--horas N] [--conta C] [--maquinas a,b] [--json] [--tudo]` | quem gasta cada conta em todas as máquinas, com a cota ao lado: resumido por pasta e tipo; `-v` cada sessão e processo |
| `codex-usage [-v]` | cota do Codex |
| `claude-last [-a] [pasta]`, `--pick [N]`, `--as CONTA`, `--projects` | conversas por conta; cópia entre contas; lista de projetos da máquina |
| `claude-resume [N]`, `claude-resume --as CONTA [N]` | retoma na conta certa, ou noutra |
| `orq`, `orq --new`, `orq --list`, `orq --status-json`, `orq web` | grade tmux de 4 projetos; estado dos agentes; atalho da página |
| `orq-run --dir P --account C [--label L] [--mode resume\|new] [--tmux] [--depth N]` | um painel da grade (chamado pelo `orq`) |
| `./install.sh [--no-install\|--uninstall]` | instala/atualiza/remove |
| `./add-account.sh nome email [rótulo]`, `--remove nome` | contas |
| `./make-tarball.sh` | pacote para outra máquina |

### orq-web

| comando | uso |
|---|---|
| `orq-web [--port N]` | sobe o servidor local e abre o navegador (`ORQ_NO_OPEN=1` não abre) |
| `converte-web [rótulo@máquina…] [--all]` | lista/converte claudes interativos de tmux em agentes da página |
| `./install.sh` | instala nesta máquina (`~/.local/lib/orq-web`, comandos em `~/.local/bin`) |
| `./deploy.sh [máquina…]` | instala no laptop e nas máquinas de `orq_hosts` (ou nas citadas) |
| `./deploy/push-server0.sh [máquina]` | recria o container da página pela rede |
| `./deploy/macos-tmux-keep.sh [--remove]` | LaunchAgent do tmux no Mac remoto |

### orq-agent

| subcomando | uso |
|---|---|
| `spawn --dir P --account C --label L [--mode resume\|new] [--force] [--role-file ARQ\|-] [--parent L2] [--level N] [--permission-mode M] [--set S] [--pipeline R] [--restart]` | garante o supervisor numa sessão tmux `orqs-<L>`; `--restart` fecha o que roda e sobe de novo (e reaproveita papel, permissão e conjunto do `state.json`); `--role-file -` lê o papel do stdin (sem `--parent`/`--level` é só o prompt do agente); `--permission-mode` troca o modo só deste agente; `--set` marca o conjunto de origem; `--pipeline` a receita de que um nível veio |
| `start …` | o supervisor em primeiro plano (é o que o `spawn` abre no tmux) |
| `attach L [--since N]` | ponte stdin/stdout com o socket (o que o servidor usa) |
| `stop L` | encerra o claude e o supervisor |
| `list` | agentes desta máquina com estado |
| `convert L [--account C]` | fecha um claude interativo de tmux e reabre a conversa como agente; imprime JSON |
| `ls PASTA` | arquivos da pasta em JSON `[{name,size,mtime}]` |
| `cat ARQ [--range INI-FIM]` | uma linha JSON `{size,start,length}` e os bytes |
| `link A B`, `unlink A B`, `links`, `link-run A B` | vínculos (capítulo 6) |
| `peer L [--probe PID \| --mirror --sock S --from A \| --unmirror PID --sock S]` | lado remoto do vínculo; responde JSON, erro como `{"err": …}` com código 0 |

## 7.2 API HTTP do servidor

Autenticação: cabeçalho `X-Orq-Token` (o token do link) ou `?t=` no SSE, ou o cookie de sessão obtido
em `POST /login` (formulário `password`). Cabeçalho `Host` precisa estar em `ORQ_ALLOWED_HOSTS`. Erros
vêm como `{"err": "texto"}` com 400/403/404/409/500.

| rota | corpo / parâmetros | resposta |
|---|---|---|
| `GET /` | | a página (redireciona para `/login` sem cookie quando a senha está ligada) |
| `GET /m` | | a versão para celular (mesmo `app.js`, `mobile.css`); sem cookie, `/login?next=/m` |
| `GET /login?next=`, `POST /login`, `POST /logout` | `password=`, `next=` (`/` ou `/m`) | cookie de sessão e volta para `next`; 429 após tentativas seguidas |
| `GET /api/state` | | `{usage, usage_ts, agents, links, agents_ts, host_errors, accounts, labels, colors, migrations, auto_switch, conjuntos_abrindo}` |
| `GET /api/projects` | | `{items: [{host, cwd, account, mtime, title, …}]}` |
| `GET /api/pipelines` | | `{items: [{id, titulo, descricao, niveis, cores, multi}], default}` |
| `GET /api/conta/<conta>` | | o painel da conta (`usage-hist conta --json`): agora, hoje, ritmo, previsão, `hourly_24h`, `days_7`, `week` (série da semana até o reset), projetos de 24 h e 7 dias, alertas, calibração; 404 conta desconhecida, 503 sem histórico |
| `POST /api/agent/open` | `{host, cwd, account?, mode: "resume"\|"new"}` | `{label, acc, why, orphans_closed}`; 409 se há claude de terminal na pasta; níveis órfãos de uma vida anterior do rótulo são encerrados |
| `POST /api/agent/convert` | `{label}` | `{label, how, msg}` |
| `GET /api/agent/<L>/stream?since=N` | | SSE: um `data:` por evento do agente |
| `POST /api/agent/<L>/send` | `{text}` | `{ok}` |
| `POST /api/agent/<L>/permission` | `{request_id, allow, input?}` (`input.answers` para perguntas) | `{ok}` |
| `POST /api/agent/<L>/interrupt` | `{}` | `{ok}` |
| `POST /api/agent/<L>/stop` | `{}` | `{msg}` |
| `POST /api/agent/<L>/upload` | corpo cru, `X-Filename` | `{path, bytes}`; grava em `_inbox/` (máx. 200 MB) |
| `GET /api/agent/<L>/files` | | `{dir, items: [{name,size,mtime}], err?}` da `_outbox/` |
| `GET /api/agent/<L>/file?name=X[&dl=1]` | `Range` opcional | o arquivo (inline para imagem/PDF/vídeo/áudio/texto, senão anexo); 206 com `Range`; 416 fora do arquivo |
| `POST /api/agent/<L>/upgrade` | `{pipeline?}` | `{pipeline, levels, top, links, link_errors}` |
| `POST /api/agent/<L>/downgrade` | `{}` | `{stopped, errors, unlinked}` |
| `POST /api/agent/<L>/reopen` | `{account?}` | `{label, acc}`; mesma conversa, com nome ou noutra conta |
| `POST /api/agent/<L>/link` | `{to}` | `{ok, link, note_errors}` |
| `POST /api/agent/<L>/unlink` | `{to}` | `{ok, told}` |
| `GET /api/conjuntos` | | `{items: [{id, nome, descricao, n, maquinas, lider, permissao, abertos}], dir}` |
| `POST /api/conjuntos` | o JSON do conjunto (4.13) | `{id, conjunto}`; 400 com todos os problemas de validação |
| `GET /api/conjuntos/<id>` | | `{id, conjunto}` |
| `POST /api/conjuntos/<id>/delete` | `{}` | `{ok}` |
| `POST /api/conjuntos/snapshot` | `{nome?}` | `{conjunto, avisos}`: as abas abertas como conjunto (não salva) |
| `POST /api/conjuntos/<id>/open` | `{}` | `{id, nome, agentes: [{label, host, acc, why}], lider}`; 409 `{err, abertos}` se algum já está aberto; o andamento vai em `conjuntos_abrindo` do `/api/state`: `{id, nome, etapa (abrindo\|vínculos\|professor\|tarefas\|pronto), labels, feitos, erros, top, done, ts}` |

Agente no `/api/state`: `{label, host, account, dir, state, msg, pid, kind ("sdk"\|terminal), name,
parent?, level?, permission?, role?, set?, pipeline?, tmux?, ts}` (`role` é o prompt extra com que subiu; `set` o
conjunto de origem; `pipeline` a receita de um nível, que a fotografia de conjunto reaproveita). Vínculo: `{local, remote, host, remote_host, kind ("local"\|"ssh"),
local_name, remote_name, up, resources?, sessions?, why?}`.

## 7.3 Rótulos e nomes

| forma | exemplo | onde |
|---|---|---|
| rótulo | `kaggle@server0`, `kaggle-2@server0`, `kaggle~professor@server0` | página, `orq-agent`, `state.json` |
| rótulo saneado | `kaggle-server0`, `kaggle-professor-server0` | pasta em `~/.cache/orq/`, nome de sessão (`-n`), `SendMessage` |
| sessão tmux do agente | `orqs-kaggle`, `orqs-kaggle-professor` | nas máquinas |
| sessão tmux de um vínculo | `orql-kaggle--dados-node3` | na máquina do lado local |
| sessão tmux de terminal | `claude-foo`, `claude-gmail-foo` | aliases do claude-multiconta |
| grade do `orq` | servidor `tmux -L orq`, sessão `grade`; remotos em `orq-<pasta>` | terminal |

## 7.4 Variáveis de ambiente

Servidor: ver o capítulo 2 (mais `ORQ_SETS_DIR`, a pasta dos conjuntos, padrão `~/.config/orq-web/conjuntos`).
Agente e utilitários: `ORQ_STATE_DIR`, `ORQ_BIN`, `CLAUDE_MULTICONTA_CONF`. QA:
`ORQ_QA_URL`, `ORQ_QA_PASSWORD`, `ORQ_QA_VIA` (túnel ssh), `ORQ_QA_PORT` (8790), `ORQ_QA_PROJECT`,
`ORQ_QA_PROJECT2`, `ORQ_QA_HEADED`, `ORQ_QA_KEEP`. `orq-run`/`orq-hook`: `ORQ_TTY`, `ORQ_DEPTH`,
`ORQ_LABEL`, `ORQ_STATE`.

## 7.5 Constantes que mudam o comportamento

| onde | constante | valor | efeito |
|---|---|---|---|
| `server/state.py` | `USAGE_EVERY` | 150 s | consulta de cota |
| | `AGENTS_EVERY` | 15 s | consulta de agentes e vínculos |
| | `PROJECTS_TTL` | 60 s | validade da lista de projetos |
| | `OPEN_WINDOW` | 60 s | "página aberta" desde o último pedido |
| | `CLOSING_TTL` | 30 s | agente mandado encerrar conta como fechado |
| | `PROVISIONAL_TTL` | 60 s | agente recém-aberto fica até a máquina confirmar |
| `agent/supervisor.py` | `QUIT_GRACE` | 15 s | entre fechar o stdin e matar o claude |
| `agent/events.py` | `REPLAY_MAX` | 400 | entradas do histórico antigo reenviadas |
| `agent/link.py` | `POLL`, `REMOTE_POLL`, `TUNNEL_WAIT`, `ENTRY_WAIT` | 5 s, 30 s, 20 s, 40 s | ciclo do vínculo, sonda remota, espera do túnel e do cartão |
| `agent/client.py` | `spawn_link(wait)` | 100 s | quanto o `link` espera o `link-run` subir |
| `server/external.py` | `link` timeout | 150 s | prazo do `orq-agent link` por ssh |
| `server/assign.py` | `LIMIT`, `PER_AGENT`, `STAY` | 95, 20, 10 | conta sem cota; peso por agente; preferência por ficar |
| `server/handlers.py` | `UPLOAD_MAX` | 200 MB | anexo |
