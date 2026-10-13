# Documentação do claude-orq

Vários Claude Codes, várias contas, várias máquinas, uma página. Este diretório é o manual completo
do projeto; os READMEs de cada subprojeto continuam valendo como referência rápida, e este manual os
amarra e aprofunda. Estado descrito: outubro de 2026 (commit `a24838a`).

| capítulo | para quem | o que responde |
|---|---|---|
| [1. Visão geral](01-visao-geral.md) | todos | o que o sistema é, as peças, os conceitos (conta, agente, rótulo, máquina) e uma sessão típica |
| [2. Instalação e deploy](02-instalacao-e-deploy.md) | quem põe uma máquina nova no circuito | contas e login, pacotes, container do server0, Mac como máquina remota, CI/CD |
| [3. claude-multiconta](03-claude-multiconta.md) | quem usa o terminal | contas isoladas, aliases, `usage-all` e o TOTAL, `claude-last`/`claude-resume`, o `orq` de terminal, `accounts.json` |
| [4. A página (orq-web)](04-pagina-orq-web.md) | quem usa a página | cada botão e painel: abrir projeto, chat, permissões, anexos, arquivos, níveis, vínculos, troca de conta |
| [5. Arquitetura](05-arquitetura.md) | quem mexe no código | supervisor, protocolo do socket, servidor, cache de estado, ponte, frontend, estado em disco |
| [6. Vínculos e níveis](06-vinculos-e-niveis.md) | quem mexe no código | como duas sessões do Claude Code se falam, espelhos, túneis ssh, receitas de níveis |
| [7. Referência](07-referencia.md) | consulta | linhas de comando, rotas HTTP com payloads, `accounts.json`, variáveis de ambiente, arquivos |
| [8. Operação](08-operacao.md) | quem mantém | testes e QA, diagnóstico dos problemas conhecidos, segurança, pendências |

## Mapa do repositório

```
claude-orq/
├── claude-multiconta/   contas do Claude Code isoladas por CLAUDE_CONFIG_DIR, aliases, usage-all, orq, claude-last
│   ├── accounts.json    fonte única de verdade das contas e das máquinas
│   ├── bin/             usage-all, orq, orq-run, orq-hook, claude-last, claude-whoami, claude-login-url, codex-usage-fast
│   ├── install.sh, add-account.sh, make-tarball.sh
│   └── tests/
├── orq-web/             supervisor orq-agent + servidor/página orq-web
│   ├── orqweb/agent/    supervisor, cliente do socket, conversão, arquivos (_outbox), vínculos (link)
│   ├── orqweb/server/   rotas HTTP, cache de estado, ponte, atribuição de conta, migração, receitas
│   ├── orqweb/server/static/   a página (HTML, CSS, módulos ES)
│   ├── orqweb/pipelines/       receitas de níveis (TOML)
│   ├── deploy/          container do server0, LaunchAgent do Mac
│   ├── qa/              QA com Playwright contra a aplicação real
│   ├── tests/           pytest + node --test
│   └── docs/PLANO.md    plano por fases (histórico das decisões)
├── claude-highlevel/    referência histórica: os níveis professor/executor com PTY; reimplementado no orq-web
└── .github/workflows/   um workflow por subprojeto; o do orq-web publica a imagem no GHCR
```

## Convenções deste manual

- Caminhos são relativos à raiz do repositório, salvo quando começam com `~`.
- "Laptop" é a máquina em que você está, a que roda o `orq` e, antes do container, a página. Nos rótulos
  dos agentes ela aparece literalmente como `laptop`.
- Comandos de shell aparecem como você os digita. Saídas são ilustrativas: contas, máquinas e números
  são os do ambiente do autor.
