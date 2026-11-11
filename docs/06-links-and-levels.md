# 6. Links and levels: how two Claude Code sessions talk to each other

## 6.1 The native channel and the session registry

Claude Code has a channel between sessions: the tools `ListAgents` (who is alive) and `SendMessage`
(send text to a session by name). The message arrives in the other session as a user message wrapped
in `<cross-session-message>`, with `origin = {kind: "peer", name, body, msg_id}`, and reopens its
turn. orq uses this channel for everything between agents: levels and links.

How sessions find each other: each live claude writes in `<config_dir>/sessions/` a **card**
`<pid>.json` (name given by `-n`, folder, state, version, `messagingSocketPath` = the Unix socket on
which it receives messages, `/tmp/cc-socks/<pid>.sock` on the Mac or
`/run/user/<uid>/cc-socks/<pid>.sock` on Linux, plus `procStart` and `pidDomain` to detect the card of
a dead session) and a file `<pid>.<hash>.key` with the `peerToken`, which whoever sends a message
presents to the socket. `ListAgents` reads the `sessions/` folder of **its own account**;
`SendMessage` talks to the socket. Three consequences:

1. two sessions of the same account, on the same machine, see each other on their own (this is the
   case of the levels);
2. two sessions of **different accounts** do not see each other, even on the same machine (found in
   the QA of the links: orq-qa on Work 3 did not see orq-qa2 on Gmail);
3. two sessions on **different machines** do not see each other, and the socket is local.

The last two cases are solved with a **mirror**: copying one session's card and key into the other's
`sessions/` folder, with `messagingSocketPath` pointing to a socket that reaches the source session.
On the same machine, its own socket. Between machines, a socket forwarded over ssh. The mirrored card
carries a mark `_orq_mirror: <source label>`, which orq never mistakes for a local session, and the
different `pidDomain` did not make `ListAgents` discard it (tested).

## 6.2 Session names

Every agent of the page starts with `claude -n <sanitized label>`: `kaggle@server0` →
`kaggle-server0`, `kaggle~professor@server0` → `kaggle-professor-server0` (`SendMessage` refuses `@`).
That is the name that goes into the recipes' roles (`{acima}`, `{abaixo}`, `{eu}`, `{executores}`) and
into the link notes. An agent opened before this version has no name; the page offers **↻ reabrir com
nome** (reopen with a name).

## 6.3 Levels: recipes in TOML

`orqweb/pipelines/*.toml` describes, from the top down, the levels that start **above** the tab's
agent (it is always the executor and gets no new role):

```toml
titulo = "professor → executor"
descricao = "…"
[[niveis]]
nome = "professor"
cor = "#2b6cb0"
papel = """Você é o PROFESSOR … delegue para `{abaixo}` … marcos via SendMessage para `{eu}` …"""
```

(`titulo` title, `descricao` description, `niveis` levels, `nome` name, `cor` colour, `papel` role;
the role text reads "You are the TEACHER … delegate to `{abaixo}` … milestones via SendMessage to
`{eu}` …".)

`plan_levels` resolves the placeholders: `{acima}` (above) and `{abaixo}` (below) are the session
names of the neighbours (the one below the last level is the tab's agent), `{eu}` (me) the name of the
level itself, and `{executores}` (only on the lowest level, in *multi* recipes) the ready-made list
`- \`nome\`: máquina X, pasta Y` (name, machine, folder) of the tab's agent and of those linked to it.
Each level starts with `orq-agent spawn --mode new --role-file - --parent <the one below> --level
<name>`; the role travels through ssh's stdin and is kept in `role.txt` in the agent's state, so that
a `--restart` (account switch) can reuse it. `state.json` keeps `parent` and `level`; the page
rebuilds the chain by following `parent` and hides the levels behind the base agent's tab.

Roles that worked, in practice: the teacher never executes, delegates with `SendMessage`, asks for
short milestones (PLANEJANDO, IMPLEMENTANDO, PROBLEMA, VALIDANDO, CONCLUÍDO: planning, implementing,
problem, validating, done) sent to its name, and uses `AskUserQuestion` to ask the person (text with
"which of the two?" does not arrive as a question). The executor usually replies to the address
`uds:/tmp/cc-socks/<pid>.sock` instead of the name; the page shows it as "resposta" (reply). All
levels start in the same `permission_mode`: different modes make Claude Code hold back the message
between them.

Lessons from claude-highlevel (the earlier project, with a PTY): it is the same two sessions talking
over the native channel; what changed was dropping the PTY and its own screen and using orq-web's
supervisor and page.

## 6.4 Links: `orq-agent link`

A link is a pair of agents that see each other. `orq-agent link A B` runs on `A`'s machine:

**Same machine** (`same_host`): no process. `local_link` finds each one's card in the `sessions/`
folder of its account (waiting up to 40 s, because a newly opened agent takes a while to register)
and, if the accounts are different, writes each one's card and key into the other's folder, with the
same socket. The link file keeps `sessions` (the pids of the two sessions): if an agent is closed and
reopened, the link counts as dead, and the next `orq-agent links` collects the mirrors.

**Different machines**: a process `orq-agent link-run A B` in a tmux session `orql-<a>--<b>`:

1. finds `A`'s local card; asks the other machine, over ssh, for `orq-agent peer B --probe <A's pid>`,
   which returns `B`'s card (with the key) and says whether a live socket forwarded to `A` already
   exists there;
2. decides the forwardings: `-L <here>/orq-<B's pid>.sock:<there>/<B's pid>.sock` if there is no live
   socket forwarded to `B` here; `-R <there>/orq-<A's pid>.sock:<here>/<A's pid>.sock` if there is
   none for `A` there. The forwarded socket has its own name (`orq-<pid>.sock`) so that it is never
   confused with that of a real session (pids repeat across machines);
3. opens `ssh -N` with those forwardings (`ExitOnForwardFailure`, `ServerAlive`,
   `StreamLocalBindUnlink`; stderr in the link's log), waits for the local socket to accept a
   connection;
4. writes `B`'s mirror here and sends `A`'s over there (`peer B --mirror --sock … --from A`, with the
   card and the key through ssh's stdin);
5. writes the link file and stays up: every 5 s it checks the tunnel, `A`'s card and pid, and the
   forwarded socket; every 30 s it probes the other machine (did `B` close? was it reopened with
   another pid?). It exits on `unlink` (SIGTERM), on SIGHUP, if either of the two closes or if the
   tunnel drops, always going through the cleanup, even on an unexpected error.

**Shared resources.** Mirrors and forwarded sockets are per (`sessions/` folder, pid), not per link:
the tab's agent and the teacher above it, on the same account, both linked to the same remote
executor, use the same card for `B` and the same socket `orq-<B's pid>.sock`. Each link file lists the
resources it depends on (`card:<folder>/<pid>`, `sock:<path>`, `remote:<machine>:<folder>/<pid>`), and
`release()` only deletes what no other live link on this machine uses. A forwarding that is already up
is reused; if the link that maintained it exits, the one that remains notices the dead socket on the
next cycle and rebuilds the tunnel with what was already its own plus what is now missing. On exit,
the link first marks itself as gone, so that the others do not count on it. This was the most
important fix from the adversarial review: before it, the second link destroyed the first one's
mirror.

**Security.** The `.key` is the socket's credential: it only travels over ssh, is written with mode
0600 and its name is validated (`<pid>.<hex>.key`); writing or deleting a mirror never touches a card
that is not a mirror. To make a mirror, `orq-agent link` does read each session's `.key` file. On
the same machine it copies the content into the other account's `sessions/` folder. Between machines
the content travels inside the ssh connection as a field of a JSON record: the local session's key
goes on the standard input of `orq-agent peer --mirror`, and the remote session's key comes back in
the JSON that `orq-agent peer` writes to its standard output on that same connection. The key is not
stored in the link file and is not sent to the page, and the link does not log it (the one exception:
an error message quotes the tail of a peer response that failed or could not be parsed). What `orq-web` does not handle at all is
the account's OAuth token (the Keychain, or `.credentials.json`): no code in `orqweb/` reads it; only
`usage-all`, in `claude-multiconta`, reads it to query the usage API. The assistant that helped
develop this could not even copy the key files, by policy, and the supervisor itself does
everything.

## 6.5 The server and the page

`POST /api/agent/<A>/link {to: B}`: validates that both are live agents of the page, runs `orq-agent
link A B` on `A`'s machine, records the link in the state (until the machine lists it) and delivers to
each one a note `[orq] Vínculo criado com o agente …` (an injected user message; the page shows it as
a system chip). `unlink` does the reverse, from either side, and tells the one that remains. The state
of the links comes from the machines together with the list of agents (`orq-agent links`, in the same
ssh connection as `orq --status-json`); `host` in the record is always the machine where the link runs
(the local side).

The `professor-executores` recipe uses this: on starting, the bottom level is linked to each agent
linked to the tab's agent; **↓ desfazer** undoes those links before stopping the level. On the page,
the **vínculos** view shows cards and wires; the tab, `🔗n`.

## 6.6 Known limitations

- The idle notice (`SendMessage`'s `notify_when_idle`) depends on a registration that the mirror does
  not satisfy: delivery goes through, the "I'm done" notice may not. The roles ask for explicit
  milestones.
- A link over ssh needs `A`'s machine to reach `B`'s over ssh under that name. From the laptop to the
  machines that holds; the reverse depends on each one's `~/.ssh/config`.
- Account migration of a linked agent does not rebuild the link (the agent reopens with another pid;
  the link drops and has to be made again).
- `orq-agent links` lists undone links forever (filtered out on the page).
