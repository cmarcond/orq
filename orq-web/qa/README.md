# QA of orq-web (Playwright)

It tests the **real application**, with the **real claude**, through the browser: an orq-web just for the QA starts on
port 8790 (or it is pointed at the page on the network), a headless Chromium opens the page, and each test
leaves in `qa/out/` the final screenshot, the console and a **video of the whole session**
(`<test>.mp4`, or `.webm` without ffmpeg; `-FALHOU` in the name when it fails). Simple tests: the page opens,
the picker lists projects, an agent replies, an interruption works (`test_chat.py`), the levels
teacher → executor deliver the hello world of HighLevel (`test_niveis.py`: the teacher delegates through the wire,
the executor creates `hello.py` in the QA folder and reports, the teacher explains; ↓ undo at the end), and the automatic
account switch continues the conversation (`test_troca_conta.py`: it is not possible to exhaust the quota on demand,
so the QA writes `state: quota` in the agent's state.json, as the supervisor would; from there the server reopens
the agent on another account, resends the pending question and the new claude answers the keyword kept
by the old one). The shared files (`test_arquivos.py`): the first test writes straight into the
`_outbox/` of the QA folder and checks list, toast, preview of Markdown and image and download, without spending
quota; the second asks the real claude to create `resumo.md` and "share it through the page", without mentioning the
folder, and waits for the file to appear in the list (it proves that the system prompt note is enough). The page itself
(`test_pagina.py`): the 📊 usage tab (5 h, 24 h, 7 days) and the account panel through the quota card, with the alert
badges, from the usage-hist database; the phone version (`/m`, iPhone viewport: TOTAL strip that opens the
accounts, picker and usage tab on the narrow screen); the 🔔 bell with and without permission; the title `(1) orq` and the notification
when an agent finishes with the page out of sight (a short turn); and the shortcuts ⌘n, Shift+Enter and Esc (opens
two agents, sends nothing). Only the last two open agents; the first four spend no quota. The outbound path of the files (`test_anexos.py`): dropping a file with no tab
open warns (no quota); and a CSV through 📎 (the browser's picker) plus a dragged note (a `DragEvent` with the
`File`, the same thing the page receives) enter the queue of chips, one leaves through ✕ and comes back, sending writes both to
`_inbox/` on the agent's machine and the message goes out with the paths; the real claude reads both from there and answers the sum and
the keyword (one turn); at the end the bytes are checked over ssh in a single connection. ⇄ convert (`test_converter.py`): starts a real interactive
`claude` in a tmux on this machine, in the QA folder and on the account `ORQ_QA_ACCOUNT` (otherwise the first one that is not the default),
accepts the folder trust dialog, asks through tmux that it keep a keyword, waits for the page to list it as a
dashed tab, converts through the panel's button (`/exit` in tmux, same conversation reopened as an agent) and asks the
new agent for the keyword: it remembers. Two turns; it only runs when `ORQ_QA_PROJECT` is on this machine. Permissions and questions
(`test_permissoes.py`): `AskUserQuestion` becomes a form in the chat and the QA answers by choosing an option, then
through "Other…" and finally cancels (three short turns); and a set of a single agent with `permissao: manual` asks
permission to write a file, the QA denies (nothing is written, claude says it was denied) and then allows (two
turns). In the selectors, `.perm` without `.term`: the panel of the terminal claude is also a `.perm`. Reconnection (`test_reconexao.py`): with claude
counting to 100, reloading the page brings the whole conversation back (stream from 0) and the turn continues live;
taking down and bringing up the QA's orq-web in the middle of the response makes the tab reconnect asking `since=N` (only what was missing) and nothing
is repeated in the chat. Two turns. Three levels (`test_niveis.py::test_professor_arquiteto_executor`): the recipe
`teacher → architect → executor` with the same hello world; the teacher delegates to the architect, the architect instructs the
executor, the DONE goes up executor → architect → teacher, the architect's card opens its raw chat, and ↓ undo
closes the two levels. Several turns. Remote path (`test_remoto.py`, `ORQ_QA_PROJECT_REMOTE`, default
`laptop:~/Projects/orq-qa`, on a machine that is not the server's): the remote agent shares a file (list,
preview and download go through `orq-agent ls`/`cat` over ssh); an agent from here links to the remote one (`ssh` link,
tunnel of `orq-agent link`) and asks it for its folder through SendMessage; and a set opens one agent on each machine with
its own task. About 5 turns; it skips when the remote folder is on the server's machine. Login (`test_login.py`): a second orq-web just for the test starts
on port `ORQ_QA_LOGIN_PORT` (8791) with a throwaway `ORQ_PASSWORD_FILE`; without the password there is no getting in (page and API),
a wrong password warns, five errors block for one minute, the right password returns to the requested page (`/m`) and gives the
cookie to the API, and logging out invalidates the cookie. No quota. 📄 upload JSON (`test_conjuntos.py::test_enviar_json_pelo_botao`): the button opens the
browser's file picker, the JSON becomes a saved set (file name when `nome` is missing), ⬇ downloads the same
JSON, broken JSON and an invalid set only warn, ✕ twice deletes. No quota. ↻ reopen with a name (`test_reabrir.py`): removes the `name` from the state.json of a real
agent (like an agent from before the names), the page starts offering ↻ in ⇅ instead of the recipes, the agent reopens in the
same conversation with a new pid and a name, remembers the keyword kept before, and ⇅ goes back to offering the recipes.
Two short turns. This second
test uses the `orq-agent` installed in `~/.local` (it is what the QA's server starts): run `./install.sh`
first if you changed the agent.
Links (`test_vinculos.py`): two real agents on the laptop, 🔗 through the page, one asks the other for its
working folder through SendMessage and receives the reply through the wire. Teacher with several executors
(`test_professor_executores.py`): the same two, linked, get a teacher through the recipe
`professor-executores`, which asks each one for its folder and explains both; ↓ undo clears the teacher's
link and leaves the executors' one. Both use the installed `orq-agent` (`./install.sh` first).

```sh
cd orq-web
uv pip install --python .venv/bin/python -e ".[qa]" && .venv/bin/python -m playwright install chromium
# Ubuntu 20.04 (server0): the new Playwright has no browser for it; use playwright==1.48.0 and --with-deps
.venv/bin/python -m pytest qa                 # everything (test_chat spends a little quota)
.venv/bin/python -m pytest qa/tests/test_smoke.py   # only what spends no quota
```

Variables: `ORQ_QA_URL` + `ORQ_QA_PASSWORD` test the page on the network (`http://10.0.0.10:8765/`)
instead of starting a server; `ORQ_QA_VIA=server0` reaches it through an ssh tunnel (`make qa-rede`) and, without
`ORQ_QA_PASSWORD`, reads the password in `~/.config/orq-web/password` of that machine on the same connection — needed
on macOS, where local network privacy only lets Apple binaries (curl, ssh) reach the ZeroTier
address: Playwright's Chromium and python receive "No route to host" (`ORQ_QA_TUNNEL_PORT`, 18765);
`ORQ_QA_PORT` (8790) and `ORQ_QA_KEEP=1` (does not take the server down at the end);
`ORQ_QA_PROJECT` (`laptop:~/Projects/orq-qa`, throwaway folder where the agent opens); `ORQ_QA_HEADED=1`
shows the browser.

## Driving the page by hand (`qa/orqqa.py`)

Each command opens the page, does one thing, writes `qa/out/<command>-<time>.png` and `.console.txt` and
prints the result. It serves to inject texts and commands into an agent and look at what the page did:

```sh
P=.venv/bin/python
$P qa/orqqa.py up                               # starts the QA's orq-web (port 8790) and shows the link
$P qa/orqqa.py state                            # agents, states, quota, current tab
$P qa/orqqa.py open laptop:~/Projects/orq-qa    # opens a project (new folder = new conversation)
$P qa/orqqa.py say "liste os arquivos desta pasta"   # sends and waits for the reply
$P qa/orqqa.py allow                            # allows the pending tool (deny denies)
$P qa/orqqa.py esc                              # interrupts the turn
$P qa/orqqa.py shot antes-do-stop               # screenshot with that name
$P qa/orqqa.py eval "document.querySelectorAll('.chat.on .ai').length"
$P qa/orqqa.py text "#cstate"                   # text of a selector
$P qa/orqqa.py click ".tab.plus[data-host=laptop]"
$P qa/orqqa.py watch 20                         # stays 20 s listening to console/errors/failed requests
$P qa/orqqa.py levels                           # ⇅ add level (professor-executor) on the current tab
$P qa/orqqa.py wires                            # wires between the levels (→ delegated, ✓✓ delivered)
$P qa/orqqa.py undo                             # ↓ undo: closes the levels above
$P qa/orqqa.py quota "continue"                 # forces "no quota" on the current tab and waits for the account switch
$P qa/orqqa.py stop orq-qa@laptop               # ✕ twice
$P qa/orqqa.py down                             # takes down the QA's orq-web (agents continue)
```

`ORQ_QA_LABEL=label` picks the tab before `say`, `esc`, `allow`, `deny`, `stop`, `shot`, `text` and
`eval`; `ORQ_QA_VIDEO=1` also records a video of the command. With `ORQ_QA_URL`, the same commands apply
to the page on the network.

## What the library can do (`orqqa.Orq`)

`open_project("machine:/path")`, `say(text)` (waits for the reply and returns claude's last text),
`wait_idle()`, `interrupt()`, `permission(allow)`, `upgrade(recipe)`, `levels()`, `wires()`,
`wait_wire(level, contains)` (a wire that left that level: `professor` delegated, `executor` reported),
`downgrade()`, `stop(label)`, `ensure_stopped(label)` (through the API),
`show(label)`, `cstate()`, `last_ai()`, `messages()`, `labels()`, `state()`, `agents()`, `shot(name)`,
`dump(name)`, `problems()` (page errors, failed requests, responses >= 400).

Selectors of the page that the tests use: `.tab[data-l]` (tab; `.on` the current one; `.x` closes; `.plus` opens),
`#cstate` (state: `opening…`, `working…`, `waiting for you`, `asks permission`), `#input`/`#send`,
`.chat.on .me|.ai|.chip|.sys|.perm`, `#picker`/`#pnew`/`#popen`/`#plist .p`, `#toast`.
