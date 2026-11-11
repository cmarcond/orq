# claude-orq documentation

Several Claude Codes, several accounts, several machines, one page. This directory is the full manual
for the project; the README of each sub-project still serves as a quick reference, and this manual
ties them together and goes deeper. State described: October 2026 (commit `a24838a`).

| chapter | for whom | what it answers |
|---|---|---|
| [1. Overview](01-overview.md) | everyone | what the system is, the parts, the concepts (account, agent, label, machine) and a typical session |
| [2. Installation and deploy](02-installation-and-deploy.md) | whoever brings a new machine into the setup | accounts and login, packages, the server0 container, a Mac as a remote machine, CI/CD |
| [3. claude-multiconta](03-claude-multiconta.md) | whoever uses the terminal | isolated accounts, aliases, `usage-all` and the TOTAL, `claude-last`/`claude-resume`, the terminal `orq`, `accounts.json` |
| [4. The page (orq-web)](04-orq-web-page.md) | whoever uses the page | every button and panel: opening a project, chat, permissions, attachments, files, levels, links, account switching |
| [5. Architecture](05-architecture.md) | whoever works on the code | supervisor, socket protocol, server, state cache, bridge, frontend, state on disk |
| [6. Links and levels](06-links-and-levels.md) | whoever works on the code | how two Claude Code sessions talk to each other, mirrors, ssh tunnels, level recipes |
| [7. Reference](07-reference.md) | lookup | command lines, HTTP routes with payloads, `accounts.json`, environment variables, files |
| [8. Operations](08-operations.md) | whoever maintains it | tests and QA, diagnosing the known problems, security, open items |

## Repository map

```
claude-orq/
├── claude-multiconta/   Claude Code accounts isolated by CLAUDE_CONFIG_DIR, aliases, usage-all, orq, claude-last
│   ├── accounts.json    single source of truth for the accounts and the machines
│   ├── bin/             usage-all, orq, orq-run, orq-hook, claude-last, claude-whoami, claude-login-url, codex-usage-fast
│   ├── install.sh, add-account.sh, make-tarball.sh
│   └── tests/
├── orq-web/             orq-agent supervisor + orq-web server/page
│   ├── orqweb/agent/    supervisor, socket client, conversion, files (_outbox), links (link)
│   ├── orqweb/server/   HTTP routes, state cache, bridge, account assignment, migration, recipes
│   ├── orqweb/server/static/   the page (HTML, CSS, ES modules)
│   ├── orqweb/pipelines/       level recipes (TOML)
│   ├── deploy/          the server0 container, the Mac LaunchAgent
│   ├── qa/              QA with Playwright against the real application
│   ├── tests/           pytest + node --test
│   └── docs/PLAN.md    plan by phases (history of the decisions)
├── claude-highlevel/    historical reference: the teacher/executor levels with a PTY; reimplemented in orq-web
└── .github/workflows/   one workflow per sub-project; the orq-web one publishes the image to GHCR
```

## Conventions in this manual

- Paths are relative to the repository root, unless they start with `~`.
- "Laptop" is the machine you are sitting at, the one that runs `orq` and, before the container, the
  page. In agent labels it appears literally as `laptop`.
- Shell commands appear as you type them. Outputs are illustrative: accounts, machines and numbers
  are those of the author's environment.
- The page and the command-line tools have a Portuguese interface. Button captions, status texts,
  option names and program output are quoted exactly as they appear, with an English gloss where it
  helps.
