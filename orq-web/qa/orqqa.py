#!/usr/bin/env python3
"""QA of orq-web with Playwright: drives the real application (real claude) through the browser.

It is a library (the conftest uses the `Orq` class) and a command line, to inject texts and commands by hand
and see the result as a screenshot, console and state:

  python qa/orqqa.py up | down            starts/stops a QA-only orq-web (port ORQ_QA_PORT, default 8790)
  python qa/orqqa.py state                agents, states and quota (/api/state)
  python qa/orqqa.py shot [name]          screenshot -> qa/out/<name>.png
  python qa/orqqa.py open laptop:~/Projects/orq-qa    opens a project (new folder = new conversation)
  python qa/orqqa.py show label           switches to the tab of that agent
  python qa/orqqa.py say "text"           sends to the agent of the current tab and waits for the reply
  python qa/orqqa.py esc                  interrupts the turn (Esc)
  python qa/orqqa.py allow | deny         answers the pending permission
  python qa/orqqa.py stop [label]         closes the agent (✕ twice)
  python qa/orqqa.py levels [recipe]      ⇅ add level on the current tab (default professor-executor)
  python qa/orqqa.py link label           🔗 links the current tab to the agent label (any machine)
  python qa/orqqa.py unlink label         removes the link
  python qa/orqqa.py wires                wires between the levels and the levels of the panel
  python qa/orqqa.py undo                 ↓ undo: closes the levels above
  python qa/orqqa.py purge machine:/folder  cleans a QA folder: undoes levels, closes its agents and deletes
                                          the folder, the state in ~/.cache/orq and claude's conversations
  python qa/orqqa.py sets                 ⊞ sets: lists the saved ones (name, agents, already open)
  python qa/orqqa.py set ID               opens the set ID via the picker and waits for the server to finish
  python qa/orqqa.py quota ["text"]        forces "out of quota" on the current tab and waits for the
                                          account switch
  python qa/orqqa.py click "selector" | text "selector" | eval "js"
  python qa/orqqa.py watch [seconds]      keeps listening to console, errors and failing requests

Every command writes a screenshot and the console to qa/out/ (ORQ_QA_VIDEO=1 also records a video). Target:
the orq-web of `up`, or ORQ_QA_URL (e.g. http://10.0.0.10:8765/ with ORQ_QA_PASSWORD) for the page on the
network; ORQ_QA_VIA=server0 reaches it through an ssh tunnel (and reads the password there), because the local
network privacy of macOS only lets Apple binaries reach the ZeroTier address — Chromium receives "No route to
host". ORQ_QA_HEADED=1 shows the browser; ORQ_QA_LABEL=label picks the tab before `say`, `esc`, `allow`,
`deny` and `stop`.
"""

import atexit
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse

from playwright.sync_api import Page, Playwright, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # orq-web
OUT = HERE / "out"
PORT = int(os.environ.get("ORQ_QA_PORT", "8790"))
STATE_ROOT = Path(os.environ.get("ORQ_STATE_DIR", str(Path.home() / ".cache/orq")))


def ts() -> str:
    return time.strftime("%H%M%S")


# ---------------------------------------------------------------- orq-web just for the QA
def pid_file(port: int = PORT) -> Path:
    return OUT / f"orq-web-{port}.pid"


def local_url(port: int = PORT) -> Optional[str]:
    """Link (with token) of the orq-web on this port, if it responds."""
    try:
        url = (STATE_ROOT / f"web-{port}.url").read_text().strip()
    except OSError:
        return None
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3).read(64)
    except OSError:
        return None
    return url


def server_up(port: int = PORT, wait: float = 30) -> Tuple[str, bool]:
    """Ensures a real orq-web on this port. Returns (link with token, whether this call started it)."""
    url = local_url(port)
    if url:
        return url, False
    OUT.mkdir(exist_ok=True)
    log = open(OUT / f"orq-web-{port}.log", "ab")
    p = subprocess.Popen(
        [sys.executable, "-m", "orqweb.server.cli", "--port", str(port), "--no-open"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},  # else the log only comes at the end (lost on SIGTERM)
    )
    pid_file(port).write_text(str(p.pid))
    deadline = time.time() + wait
    while time.time() < deadline:
        url = local_url(port)
        if url:
            return url, True
        if p.poll() is not None:
            raise RuntimeError(f"orq-web exited with {p.returncode}; see {OUT / f'orq-web-{port}.log'}")
        time.sleep(0.3)
    raise RuntimeError(f"orq-web did not respond within {wait:.0f} s on port {port}")


# ---------------------------------------------------------------- cleanup of the QA folders
# Only folders with this name are deleted: an ORQ_QA_PROJECT pointing at a real project never loses it.
QA_NAME = re.compile(r"^orq-(qa|demo)[A-Za-z0-9_-]*$")


def local_host_name() -> str:
    """Name of this machine on the QA page (the orq-web of `server_up` inherits the same ORQ_LOCAL_HOST)."""
    return os.environ.get("ORQ_LOCAL_HOST") or "laptop"


def qa_name(path: str) -> str:
    name = path.rstrip("/").split("/")[-1]
    if not QA_NAME.match(name):
        raise ValueError(f"{path!r} is not a QA folder (orq-qa… or orq-demo…): not deleting it")
    return name


def purge_command(path: str) -> str:
    """Shell line that deletes, on the agent's machine, what a round leaves of a QA folder:
    the folder, the state of its supervisors (the agent, the levels and the links, in ~/.cache/orq/<name>-*)
    and the conversations that claude writes per folder in each account
    (~/.claude*/projects/<sanitised folder>)."""
    name = qa_name(path)
    p = path.rstrip("/")
    folder = '"$HOME"/' + shlex.quote(p[2:]) if p.startswith("~/") else shlex.quote(p)
    state = os.environ.get("ORQ_QA_STATE_DIR", "~/.cache/orq")
    # a level that was just closed (↓ undo) may still be exiting: the supervisor rewrites the state.json and
    # claude the conversation as they die; waits for the sockets to vanish (up to 30 s) before deleting,
    # otherwise the state reappears
    return (
        f"P={folder}; K=$(printf %s \"$P\" | sed 's|[^A-Za-z0-9]|-|g'); "
        f"for i in $(seq 1 30); do ls {state}/{name}-*/agent.sock >/dev/null 2>&1 || break; sleep 1; done; "
        f'rm -rf "$P" {state}/{name}-*; '
        'for d in ~/.claude ~/.claude-*; do [ -d "$d/projects" ] && rm -rf "$d/projects/$K"; done; true'
    )


def run_on(host: str, command: str, timeout: float = 60) -> None:
    """Runs `command` on this machine or, over ssh, on the other one (a single connection: see
    ssh-sequencial-falha)."""
    if host == local_host_name():
        cmd = ["sh", "-c", command]
    else:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", host, command]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"cleanup on {host} failed: {(r.stderr or r.stdout).strip()[-200:]}")


def server_down(port: int = PORT, wait: float = 10) -> bool:
    """Stops the QA's orq-web (the agents keep running; close them with `stop`)."""
    try:
        pid = int(pid_file(port).read_text())
    except (OSError, ValueError):
        pid = None
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        pid_file(port).unlink(missing_ok=True)
    else:
        subprocess.run(["pkill", "-f", f"orqweb.server.cli --port {port}"], capture_output=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        if not local_url(port):
            return True
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------- tunnel to the page on the network
TUNNEL_PORT = int(os.environ.get("ORQ_QA_TUNNEL_PORT", "18765"))
_tunnel: Optional[Tuple[str, Optional[str], subprocess.Popen]] = None  # (local url, password, ssh)


def tunnel_up(url: str, via: str, port: int = TUNNEL_PORT, wait: float = 30) -> Tuple[str, Optional[str]]:
    """Ssh tunnel through `via` to the page at `url`. Returns (local url, password read on `via`).

    From here, only Apple binaries (curl, ssh) reach the ZeroTier address: the local network privacy of
    macOS denies Playwright's Chromium and python ("No route to host"). A single ssh connection brings the
    password from ~/.config/orq-web/password (two in a row to the same machine fail) and holds the tunnel
    until exit."""
    global _tunnel
    if _tunnel:
        return _tunnel[0], _tunnel[1]
    u = urlparse(url)
    remote = f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}"
    marker = "__orq_qa_tunel__"
    p = subprocess.Popen(
        [
            "ssh",
            *("-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "-o", "ExitOnForwardFailure=yes"),
            *("-L", f"127.0.0.1:{port}:{remote}"),
            via,
            f"cat ~/.config/orq-web/password 2>/dev/null; echo; echo {marker}; exec sleep 86400",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    lines: List[str] = []
    for line in p.stdout:  # type: ignore[union-attr]
        if line.strip() == marker:
            break
        lines.append(line.strip())
    else:
        raise RuntimeError(f"ssh tunnel through {via} did not open: {' / '.join(x for x in lines if x)}")
    password = next((x for x in lines if x), None)
    local = f"http://127.0.0.1:{port}"
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            urllib.request.urlopen(local + "/", timeout=3).read(64)
            break
        except OSError:
            time.sleep(0.3)
    else:
        tunnel_down(p)
        raise RuntimeError(f"the page {url} did not respond through the tunnel {local} (via {via})")
    _tunnel = (local, password, p)
    atexit.register(tunnel_down)
    return local, password


def tunnel_down(p: Optional[subprocess.Popen] = None) -> None:
    global _tunnel
    p = p or (_tunnel[2] if _tunnel else None)
    _tunnel = None
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def target() -> Tuple[str, Optional[str], Optional[str]]:
    """(base url, token, password): ORQ_QA_URL (page on the network, through a tunnel with ORQ_QA_VIA) or
    the QA's orq-web."""
    url = os.environ.get("ORQ_QA_URL")
    if url:
        password = os.environ.get("ORQ_QA_PASSWORD")
        if os.environ.get("ORQ_QA_VIA"):
            url, read = tunnel_up(url, os.environ["ORQ_QA_VIA"])
            password = password or read
        return url.rstrip("/"), None, password
    url = local_url()
    if not url:
        raise SystemExit(f"no orq-web on port {PORT}: run `python qa/orqqa.py up` (or set ORQ_QA_URL)")
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}", parse_qs(u.query).get("t", [None])[0], None


# ---------------------------------------------------------------- the page
class Orq:
    """An orq-web tab open in Chromium, with console, errors and failed requests kept."""

    def __init__(self, page: Page, base: str, token: Optional[str] = None, password: Optional[str] = None):
        self.page, self.base, self.token, self.password = page, base, token, password
        self.console: List[str] = []
        self.browser = None
        page.on("console", lambda m: self.console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: self.console.append(f"[pageerror] {e}"))
        page.on(
            "requestfailed", lambda r: self.console.append(f"[requestfailed] {r.method} {r.url} {r.failure}")
        )
        page.on("response", lambda r: r.status >= 400 and self.console.append(f"[http {r.status}] {r.url}"))

    @classmethod
    def launch(cls, pw: Playwright, headed: bool = False, video: bool = True) -> "Orq":
        """Opens the page. With `video`, records the session; `close(name)` saves to qa/out/<name>.mp4 (or
        .webm)."""
        base, token, password = target()
        browser = pw.chromium.launch(headless=not headed)
        size = {"width": 1280, "height": 860}
        extra = {"record_video_dir": str(OUT / "video"), "record_video_size": size} if video else {}
        ctx = browser.new_context(viewport=size, locale="pt-BR", **extra)
        o = cls(ctx.new_page(), base, token, password)
        o.browser = browser
        o.goto()
        return o

    def close(self, video_name: Optional[str] = None) -> Optional[Path]:
        """Closes the browser. With `video_name`, keeps the session video in qa/out/<name>.mp4 (or .webm)."""
        if not self.browser:
            return None
        v = self.page.video
        self.page.context.close()  # finishes recording the video
        self.browser.close()
        if not v:
            return None
        src = Path(v.path())
        if not video_name:
            src.unlink(missing_ok=True)
            return None
        dst = OUT / f"{video_name}.webm"
        src.replace(dst)
        if shutil.which("ffmpeg"):  # mp4 opens in QuickTime and on the phone; webm only in a browser
            mp4 = dst.with_suffix(".mp4")
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(dst), "-pix_fmt", "yuv420p", str(mp4)],
                capture_output=True,
            )
            if r.returncode == 0:
                dst.unlink()
                return mp4
        return dst

    def url(self, path: str = "/") -> str:
        if not self.token:
            return self.base + path
        return f"{self.base}{path}{'&' if '?' in path else '?'}t={self.token}"

    def goto(self) -> None:
        self.page.goto(self.url("/"))
        if self.password and self.page.url.rstrip("/").endswith("/login"):
            self.page.fill("#p", self.password)
            self.page.click("button[type=submit]")
        self.page.wait_for_selector("#tabs .tab.plus", timeout=20000)
        self.page.wait_for_timeout(300)

    # ---- API (same token/cookie as the tab)
    def api(self, path: str, body: Optional[dict] = None) -> dict:
        headers = {"X-Orq-Token": self.token} if self.token else {}
        if body is None:
            r = self.page.request.get(self.base + path, headers=headers)
        else:
            r = self.page.request.post(
                self.base + path,
                data=json.dumps(body),
                headers={**headers, "Content-Type": "application/json"},
            )
        try:
            return r.json()
        except Exception:
            return {"status": r.status, "text": r.text()[:300]}

    def state(self) -> dict:
        return self.api("/api/state")

    def agents(self) -> List[dict]:
        return [a for a in self.state().get("agents", []) if a.get("state") != "closed"]

    # ---- what the page shows
    def current(self) -> Optional[str]:
        return self.page.evaluate("document.querySelector('.tab.on')?.dataset.l || null")

    def labels(self) -> List[str]:
        return self.page.evaluate("[...document.querySelectorAll('.tab[data-l]')].map(e => e.dataset.l)")

    def cstate(self) -> str:
        return self.page.evaluate("document.getElementById('cstate').textContent")

    def last_ai(self) -> str:
        return self.page.evaluate("[...document.querySelectorAll('.chat.on .ai')].pop()?.innerText || ''")

    def messages(self) -> List[dict]:
        """Each block of the current chat: {cls, text} (me = you, ai = claude, chip = tool, sys = notice)."""
        return self.page.evaluate(
            "[...document.querySelectorAll('.chat.on .inner > *')]"
            ".map(e => ({cls: e.className, text: e.innerText.slice(0, 400)}))"
        )

    def toast(self) -> str:
        return self.page.evaluate("document.getElementById('toast').textContent")

    # ---- actions
    def show(self, label: str) -> None:
        self.page.click(f'.tab[data-l="{label}"] .meta')
        self.wait_fn(f"() => document.querySelector('.tab.on')?.dataset.l === {json.dumps(label)}")

    def open_project(self, spec: str, timeout: float = 90) -> str:
        """`machine:/path` through +, new conversation. Waits for the agent to leave 'opening…'. Returns the
        label."""
        host, _, path = spec.partition(":")
        if not path:
            raise ValueError("use machine:/path")
        self.page.click(f'.tab.plus[data-host="{host}"]')
        self.page.wait_for_selector("#picker:not([hidden])")
        self.page.fill("#pnew", spec)
        self.page.click("#popen")
        name = path.rstrip("/").split("/")[-1]
        self.wait_fn(
            "([n, h]) => (document.querySelector('.tab.on')?.dataset.l || '').startsWith(n) "
            "&& document.querySelector('.tab.on').dataset.l.endsWith('@' + h)",
            arg=[name, host],
            timeout=timeout * 1000,
        )
        label = self.current()
        # 'opening…' is the page's provisional guess; the machine confirms the agent ready within ~15 s.
        # The tab has to still be there: if it vanishes, the state becomes empty and that is not "ready".
        self.wait_fn(
            "(l) => document.querySelector('.tab.on')?.dataset.l === l"
            " && /waiting|working|permission/.test(document.getElementById('cstate').textContent)",
            arg=label,
            timeout=45000,
        )
        return label

    # Turn finished: after your last message there is a finished claude text (without .live), a
    # permission or a notice, and the state is no longer 'working…'. (The state alone is not enough: on the
    # first turn claude's `init` goes through 'waiting for you' before the reply comes.)
    TURN_DONE = """() => {
      const inner = document.querySelector('.chat.on .inner');
      if (!inner) return false;
      const kids = [...inner.children];
      const lastMe = kids.map((e) => e.classList.contains('me')).lastIndexOf(true);
      const done = kids.slice(lastMe + 1).some((e) =>
        (e.classList.contains('ai') && !e.classList.contains('live'))
        || e.classList.contains('perm') || e.classList.contains('sys'));
      return done && !document.getElementById('cstate').textContent.includes('working');
    }"""

    def wait_fn(self, js: str, arg: Any = None, timeout: float = 30000) -> None:
        """Like `page.wait_for_function(js, arg=…, timeout=ms)`, but polling through `evaluate` every 300 ms.

        Playwright 1.48 (the newest with Chromium for the Ubuntu 20.04 of server0) evaluates the predicate
        once through CDP and, if it is not true right away, starts polling with `eval` inside the page, which
        the CSP (without `unsafe-eval`) blocks. `evaluate` always goes through CDP."""
        end = time.time() + timeout / 1000
        while True:
            try:
                if self.page.evaluate(js, arg):
                    return
            except Exception as e:  # noqa: BLE001 - the page may be between two states
                if "unsafe-eval" not in str(e) and "Execution context" not in str(e):
                    raise
            if time.time() >= end:
                raise AssertionError(f"waited {timeout / 1000:.0f} s for: {js.strip()[:120]}…")
            self.page.wait_for_timeout(300)

    def wait_idle(self, timeout: float = 180) -> str:
        """Waits for the turn to finish. Returns the state text ('waiting for you', 'asks permission',
        'error')."""
        self.wait_fn(self.TURN_DONE, timeout=timeout * 1000)
        return self.cstate()

    def say(self, text: str, wait: bool = True, timeout: float = 180) -> str:
        """Types in the chat and sends (Enter). With `wait`, waits for the turn and returns claude's last
        text."""
        self.page.fill("#input", text)
        self.page.press("#input", "Enter")
        self.wait_fn("() => !document.querySelector('.chat.on .me.pending')", timeout=20000)
        if not wait:
            return ""
        self.page.wait_for_timeout(500)
        self.wait_idle(timeout)
        return self.last_ai()

    def interrupt(self) -> None:
        self.page.press("#input", "Escape")

    def permission(self, allow: bool = True) -> None:
        self.page.click(f'.chat.on .perm:not(.done) .btns button[data-ok="{1 if allow else 0}"]')

    # ---- levels (teacher → executor)
    def upgrade(self, recipe: str = "professor-executor", timeout: float = 90) -> List[str]:
        """⇅ add level on the current tab with the recipe; waits for the panel and the level above ready.

        Returns the levels of the panel, from the top down (e.g. ['professor', 'executor'])."""
        self.page.click("#lvl")
        self.page.wait_for_selector(f'#picker:not([hidden]) .p.lvp[data-id="{recipe}"]', timeout=30000)
        self.page.click(f'.p.lvp[data-id="{recipe}"]')
        self.page.wait_for_selector("#levels:not([hidden]) .lcard", timeout=timeout * 1000)
        # the tab starts talking to the level above; it leaves 'opening…' when the machine confirms
        self.wait_fn(
            "() => /waiting|working|permission/.test(document.getElementById('cstate').textContent)"
            " && document.querySelectorAll('#levels .lcard').length >= 2",
            timeout=timeout * 1000,
        )
        return self.levels()

    def levels(self) -> List[str]:
        """Levels of the panel, from the top down; [] with the panel hidden (the cards stay in the DOM)."""
        return self.page.evaluate(
            "document.getElementById('levels').hidden ? []"
            " : [...document.querySelectorAll('#levels .lcard b')].map((e) => e.textContent)"
        )

    def wires(self) -> List[dict]:
        """Wires of the panel: {frm: level that sent it, head, delivered (✓✓), text}.

        Every SendMessage becomes a wire of whoever sent it ('professor' delegated, 'executor' reported a
        milestone)."""
        return self.page.evaluate(
            "[...document.querySelectorAll('#side .wire')].map((e) => ({"
            "frm: e.querySelector('.wh b')?.textContent || '',"
            " head: e.querySelector('.wh').innerText.replace(/\\s+/g, ' ').trim(),"
            " delivered: e.classList.contains('delivered') || e.classList.contains('in'),"
            " text: (e.querySelector('pre')?.textContent || '').slice(0, 300)}))"
        )

    def wait_wire(self, frm: str, contains: str = "", delivered: bool = False, timeout: float = 180) -> dict:
        """Waits for a wire from the level `frm` whose text has `contains` (if given); with `delivered`, waits
        for the ✓✓ (the other side received it, which arrives a moment after the wire). Returns the last one
        that matches."""
        self.wait_fn(
            "([f, c, d]) => [...document.querySelectorAll('#side .wire')].some((e) =>"
            " e.querySelector('.wh b')?.textContent === f"
            " && (e.querySelector('pre')?.textContent || '').toLowerCase().includes(c.toLowerCase())"
            " && (!d || e.classList.contains('delivered') || e.classList.contains('in')))",
            arg=[frm, contains, delivered],
            timeout=timeout * 1000,
        )
        hits = [w for w in self.wires() if w["frm"] == frm and contains.lower() in w["text"].lower()]
        return ([w for w in hits if w["delivered"]] if delivered else hits)[-1]

    def downgrade(self, timeout: float = 60) -> None:
        """↓ undo (twice): closes the levels above; the tab goes back to the executor."""
        self.page.click("#levels #down")
        self.page.wait_for_timeout(400)
        self.page.click("#levels #down")
        self.page.wait_for_selector("#levels", state="hidden", timeout=timeout * 1000)

    def stop(self, label: Optional[str] = None, timeout: float = 45) -> None:
        """✕ twice on the tab; waits for the tab to vanish (the machine confirms the closing within ~15 s)."""
        label = label or self.current()
        if not label:
            raise ValueError("no tab open")
        x = f'.tab[data-l="{label}"] .x'
        self.page.click(x)
        self.page.wait_for_timeout(400)
        self.page.click(x)
        self.page.wait_for_selector(f'.tab[data-l="{label}"]', state="detached", timeout=timeout * 1000)

    def ensure_stopped(self, label: str, timeout: float = 60) -> None:
        """Through the API: closes the agent if it is alive (left over from another round) and waits for it
        to vanish for good.

        Also waits for the supervisor's socket to disappear (agent on this machine): reopening before that
        makes `orq-agent spawn` answer "already running" for an agent that is dying.
        """
        sock = STATE_ROOT / re.sub(r"[^a-zA-Z0-9_-]", "-", label) / "agent.sock"  # = config.state_dir
        # a server that just started has not listed the agents yet; the socket says if there is one to close
        if sock.exists() or any(a.get("label") == label for a in self.agents()):
            self.api(f"/api/agent/{quote(label, safe='')}/stop", {})
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not sock.exists() and not any(a.get("label") == label for a in self.agents()):
                return
            time.sleep(1)
        raise RuntimeError(f"{label} did not close within {timeout:.0f} s")

    def purge(self, spec: str, timeout: float = 60) -> List[str]:
        """Leaves a QA folder (`machine:/path`) as if it had never been used.

        Undoes the levels and closes every live agent in that folder (the base label, a `-2` from another
        round and the levels above), waits for each one to vanish and deletes the folder, the state and the
        conversations on its machine. Used before a test (start from scratch) and after (leave nothing
        behind). Returns the labels it closed."""
        host, _, path = spec.partition(":")
        name = qa_name(path)

        # labels of this folder: `orq-qa@h`, `orq-qa-2@h` (another round) and the levels `orq-qa~professor@h`
        own = re.compile(rf"^{re.escape(name)}(-\d+)?(~[^@]+)?@{re.escape(host)}$")

        def mine(a: dict) -> bool:
            return bool(own.match(a.get("label") or ""))

        live = [a for a in self.state().get("agents", []) if a.get("state") != "closed" and mine(a)]
        for a in live:
            if not a.get("parent"):
                self.api(f"/api/agent/{quote(a['label'], safe='')}/downgrade", {})
        stopped = [a["label"] for a in live]
        for lab in sorted(stopped, key=lambda x: "~" not in x):  # levels first (already closed by downgrade)
            self.ensure_stopped(lab, timeout)
        self.ensure_stopped(f"{name}@{host}", timeout)  # leftover of a server that has not listed it yet
        run_on(host, purge_command(path))
        return stopped

    # ---- automatic account switch
    def account_of(self, label: str) -> Optional[str]:
        return next((a.get("account") for a in self.agents() if a.get("label") == label), None)

    def force_quota(self, label: str, retry: str, msg: str = "You've hit your usage limit (forced by QA)"):
        """Forces "out of quota" from underneath: writes `state: quota` in the agent's state.json (on this
        machine), with the message to resend in `retry`, exactly as the supervisor does on seeing the
        `rate_limit_event`.

        On the next query to the machines (≤ 15 s) the server reopens the agent on another account, in the
        same conversation, and resends `retry`. Only the detection is skipped; the switch itself is the real
        one."""
        p = STATE_ROOT / re.sub(r"[^a-zA-Z0-9_-]", "-", label) / "state.json"
        st = json.loads(p.read_text())
        st.update(state="quota", msg=msg, retry=retry, ts=time.time())
        p.write_text(json.dumps(st))
        return p

    def wait_account_change(self, label: str, old: Optional[str], timeout: float = 150) -> str:
        """Waits for the agent to reappear on another account, confirmed by the machine (not only the
        provisional 'opening…' entry); returns the new account."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            a = next((x for x in self.agents() if x.get("label") == label), None)
            if a and a.get("account") != old and a.get("state") not in ("starting", "closed"):
                return a["account"]
            self.page.wait_for_timeout(2000)
        raise RuntimeError(
            f"{label} stayed on the account {old} for {timeout:.0f} s; migrations: {self.migrations()}"
        )

    def migrations(self) -> List[dict]:
        return self.state().get("migrations", [])

    # ---- links between agents (🔗)
    def link(self, to: str, timeout: float = 120) -> List[str]:
        """🔗 link on the current tab: picks `to` in the picker; waits for the link to appear in the panel."""
        base = self.current()
        self.page.click("#lnk")
        self.page.wait_for_selector("#picker:not([hidden]) #plist .p", timeout=20000)
        self.page.click(f'#plist .p[data-l="{to}"]')
        self.wait_fn(
            "(n) => !document.getElementById('links').hidden"
            " && [...document.querySelectorAll('#links .lcard')].some((e) => e.dataset.l === n)",
            arg=to,
            timeout=timeout * 1000,
        )
        assert self.current() == base
        return self.links()

    def links(self) -> List[str]:
        """Labels of the agents linked to the tab's one (links panel)."""
        return self.page.evaluate(
            "document.getElementById('links').hidden ? []"
            " : [...document.querySelectorAll('#links .lcard')].map((e) => e.dataset.l)"
        )

    def unlink(self, to: str, timeout: float = 60) -> None:
        self.page.click(f'#links .lcard[data-l="{to}"] .ul')
        self.page.click(f'#links .lcard[data-l="{to}"] .ul')
        self.wait_fn(
            "(n) => document.getElementById('links').hidden"
            " || ![...document.querySelectorAll('#links .lcard')].some((e) => e.dataset.l === n)",
            arg=to,
            timeout=timeout * 1000,
        )

    # ---- sets (⊞ at the top)
    def open_sets(self) -> List[dict]:
        """⊞ sets: opens the picker and returns the rows {id, text} (text = name, summary, already open)."""
        if self.page.is_hidden("#picker") or self.page.is_hidden("#setsnap"):
            self.page.click("#sets")
        self.page.wait_for_selector("#picker:not([hidden]) #setsnap", timeout=20000)
        return self.page.evaluate(
            "[...document.querySelectorAll('#plist .setp')]"
            ".map((e) => ({id: e.dataset.id, text: e.innerText}))"
        )

    def save_set(self, data: dict) -> str:
        """Saves a set through the API (the same POST as "upload JSON"); returns the id."""
        j = self.api("/api/conjuntos", data)
        assert "id" in j, j
        return j["id"]

    def set_job(self, sid: str) -> Optional[dict]:
        return next((j for j in self.state().get("conjuntos_abrindo", []) if j["id"] == sid), None)

    def open_set(self, sid: str, timeout: float = 300) -> dict:
        """Clicks the set in the picker and waits for the server to finish opening it (agents, links,
        teacher, tasks). Returns the progress record (feitos, erros, top)."""
        self.open_sets()
        self.page.click(f'#plist .setp[data-id="{sid}"]')
        self.wait_fn(
            "() => document.getElementById('picker').hidden"
            " || /already open|being opened|no longer valid/"
            ".test(document.getElementById('toast').textContent)",
            timeout=30000,
        )
        if not self.page.is_hidden("#picker"):
            raise AssertionError(f"the set {sid} did not open: {self.toast()}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            j = self.set_job(sid)
            if j and j["etapa"] == "pronto":
                return j
            self.page.wait_for_timeout(2000)
        raise AssertionError(f"the set {sid} did not finish opening: {self.set_job(sid)}")

    def snapshot_set(self, nome: str) -> dict:
        """📷 save open tabs: takes the snapshot, names it and saves. Returns the JSON that was saved."""
        self.open_sets()
        self.page.click("#setsnap")
        self.page.wait_for_selector("#picker:not([hidden]) #snapjson", timeout=20000)
        self.page.fill("#snapname", nome)
        data = json.loads(self.page.input_value("#snapjson"))
        data["nome"] = nome
        self.page.click("#snapsave")
        self.wait_fn("() => /saved/.test(document.getElementById('toast').textContent)", timeout=20000)
        self.page.click("#pclose")  # the list shows up again after saving; closes it to free the page
        return data

    def delete_set(self, sid: str) -> None:
        self.open_sets()
        for _ in range(2):  # ✕ twice, like closing a tab
            self.page.click(f'#plist .setp[data-id="{sid}"] [data-del]')
        self.wait_fn('(s) => !document.querySelector(`#plist .setp[data-id="${s}"]`)', arg=sid, timeout=20000)
        self.page.click("#pclose")

    # ---- files the agent shared (_outbox/)
    def open_files(self) -> None:
        """📂 on the chat bar: opens the file list in the side panel."""
        if self.page.is_hidden("#outbox"):
            self.page.click("#out")
        self.page.wait_for_selector("#outbox:not([hidden]) .lhead")

    def files(self) -> List[str]:
        """Names in the list (panel open), from newest to oldest."""
        return self.page.evaluate(
            "[...document.querySelectorAll('#outbox .frow b')].map((e) => e.textContent)"
        )

    def wait_file(self, name: str, timeout: float = 120) -> None:
        """Waits for `name` to appear in the list (the page rereads the _outbox/ at the end of each round and
        every 30 s)."""
        self.open_files()
        self.wait_fn(
            "(n) => [...document.querySelectorAll('#outbox .frow b')].some((e) => e.textContent === n)",
            arg=name,
            timeout=timeout * 1000,
        )

    def preview(self, name: str) -> str:
        """Clicks the file and returns the tag of what the preview shows (IMG, IFRAME, VIDEO, PRE, DIV)."""
        self.page.click(f'#outbox .frow[data-n="{name}"] .fmeta')
        self.page.wait_for_selector("#view:not([hidden]) .vbody > *")
        self.wait_fn(
            "() => !document.querySelector('#view .vbody').textContent.includes('loading')", timeout=30000
        )
        return self.page.evaluate("document.querySelector('#view .vbody > *').tagName")

    def close_preview(self) -> None:
        self.page.press("body", "Escape")
        self.page.wait_for_selector("#view", state="hidden")

    def download(self, name: str) -> bytes:
        """Downloads through the API, as the ⬇ of the list does (same link)."""
        label = self.current()
        headers = {"X-Orq-Token": self.token} if self.token else {}
        r = self.page.request.get(self.url(f"/api/agent/{label}/file?name={name}&dl=1"), headers=headers)
        assert r.status == 200, f"download of {name}: http {r.status}"
        return r.body()

    def answer_permissions(self, timeout: float = 240) -> str:
        """Waits for the turn to end, allowing everything claude asks on the way. Returns the final state."""
        end = time.time() + timeout
        while True:
            st = self.wait_idle(max(5.0, end - time.time()))
            if "permission" not in st:
                return st
            self.permission(True)
            self.page.wait_for_timeout(500)

    # ---- evidence
    def shot(self, name: str) -> Path:
        OUT.mkdir(exist_ok=True)
        p = OUT / f"{name}.png"
        self.page.screenshot(path=str(p), full_page=True)
        return p

    def dump(self, name: str) -> Path:
        OUT.mkdir(exist_ok=True)
        p = OUT / f"{name}.console.txt"
        p.write_text("\n".join(self.console) + ("\n" if self.console else ""))
        return p

    def problems(self) -> List[str]:
        """Page errors, failed requests and responses >= 400 seen so far."""
        return [
            c
            for c in self.console
            if c.startswith(("[pageerror]", "[requestfailed]", "[http ", "[error]"))
            and not ("/stream?" in c and "ERR_ABORTED" in c)  # stream closed on purpose (tab recreated)
            and not ("/file?" in c and "ERR_ABORTED" in c)  # preview closed before the file arrived
            and not c.startswith("[error] Failed to load resource")  # the [http N] line says which one
            and not ("[http 404]" in c and c.endswith("/files"))  # just opened: the page tries again
        ]


# ---------------------------------------------------------------- command line
def fmt_state(s: dict) -> str:
    lines = []
    for a in s.get("agents", []):
        if a.get("state") == "closed":
            continue
        extra = (" level " + a["level"] if a.get("level") else "") + (
            " · " + a["msg"] if a.get("msg") else ""
        )
        lines.append(
            f"  {a.get('label', '?'):32} {a.get('state', '?'):12} {a.get('account', '?'):10} "
            f"{a.get('kind', '')}{extra}"
        )
    us = s.get("usage", {})
    quota = ", ".join(f"{k} 5h {v.get('5h', '?')}% 7d {v.get('7d', '?')}%" for k, v in us.items())
    return ("agents:\n" + "\n".join(lines) if lines else "agents: none") + f"\nquota: {quota or '?'}"


def main(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "up":
        url, started = server_up()
        print(("started: " if started else "already up: ") + url)
        return 0
    if cmd == "down":
        print("stopped" if server_down() else "could not find the QA's orq-web (or it did not stop)")
        return 0
    video = bool(os.environ.get("ORQ_QA_VIDEO"))
    with sync_playwright() as pw:
        o = Orq.launch(pw, headed=bool(os.environ.get("ORQ_QA_HEADED")), video=video)
        label = os.environ.get("ORQ_QA_LABEL")
        if label and cmd in ("say", "esc", "allow", "deny", "stop", "shot", "text", "eval", "quota"):
            o.show(label)
        try:
            if cmd == "state":
                print(fmt_state(o.state()))
                print("current tab:", o.current())
            elif cmd == "shot":
                pass
            elif cmd == "open":
                lab = o.open_project(args[0])
                print(f"opened: {lab} · {o.cstate()} · toast: {o.toast()}")
            elif cmd == "show":
                o.show(args[0])
                print("current tab:", o.current())
            elif cmd == "say":
                reply = o.say(" ".join(args), wait="--no-wait" not in args)
                print(f"[{o.current()}] {o.cstate()}\n{reply}")
            elif cmd == "esc":
                o.interrupt()
                o.page.wait_for_timeout(2000)
                print(o.cstate())
            elif cmd in ("allow", "deny"):
                o.permission(cmd == "allow")
                o.page.wait_for_timeout(1500)
                print(o.cstate())
            elif cmd == "stop":
                lab = args[0] if args else o.current()
                o.stop(lab)
                print(f"closed: {lab}")
            elif cmd == "link":
                print("links:", ", ".join(o.link(argv[2])) or "none")
            elif cmd == "unlink":
                o.unlink(argv[2])
                print("links:", ", ".join(o.links()) or "none")
            elif cmd == "levels":
                print(
                    "levels:",
                    " → ".join(o.upgrade(args[0] if args else "professor-executor")),
                    "·",
                    o.cstate(),
                )
            elif cmd == "wires":
                for w in o.wires():
                    print(f"[{'✓✓' if w['delivered'] else '…'}] {w['head']}\n    {w['text'][:200]}")
                print("levels:", " → ".join(o.levels()) or "none")
            elif cmd == "sets":
                for r in o.open_sets():
                    print(f"  {r['id']:24} {r['text'].replace(chr(10), ' · ')[:120]}")
            elif cmd == "set":
                j = o.open_set(args[0])
                print(f"done: {', '.join(j['feitos']) or 'none'}; teacher: {j.get('top') or '-'}")
                for k, v in j["erros"].items():
                    print(f"  problem {k}: {v}")
            elif cmd == "purge":
                for spec in args:
                    gone = o.purge(spec)
                    print(f"{spec}: clean" + (f" (closed {', '.join(gone)})" if gone else ""))
            elif cmd == "undo":
                o.downgrade()
                print("levels closed ·", o.cstate())
            elif cmd == "quota":
                lab = o.current()
                old = o.account_of(lab)
                o.force_quota(lab, " ".join(args) or "Continue from where you stopped.")
                print(f"{lab}: account {old} marked out of quota; waiting for the switch…")
                new = o.wait_account_change(lab, old)
                print(f"reopened on the account {new} · {o.cstate()} · toast: {o.toast()}")
            elif cmd == "click":
                o.page.click(args[0])
                o.page.wait_for_timeout(800)
            elif cmd == "text":
                print(o.page.inner_text(args[0]))
            elif cmd == "eval":
                print(json.dumps(o.page.evaluate(" ".join(args)), ensure_ascii=False, indent=1))
            elif cmd == "watch":
                secs = float(args[0]) if args else 30
                print(f"listening for {secs:.0f} s… (tab {o.current()})")
                o.page.wait_for_timeout(secs * 1000)
            else:
                print(f"unknown command: {cmd}\n{__doc__}")
                return 2
        finally:
            name = f"{cmd}-{ts()}" if cmd != "shot" or not args else args[0]
            print(f"screenshot: {o.shot(name)}  console: {o.dump(name)}")
            probs = o.problems()
            if probs:
                print("problems in the console:\n  " + "\n  ".join(probs[-10:]))
            v = o.close(name if video else None)
            if v:
                print(f"video: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
