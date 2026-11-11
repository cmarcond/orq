"""Supervisor: one `claude -p` in stream-json behind a Unix socket.

Clients connect, send `{"since": N}` and receive the events from N on, then the live ones.
Whatever they send next goes to claude's stdin; `{"orq": "quit"}` shuts it down. `stream_event`
events (partials) only go live, they do not enter the sequence.
"""

import json
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import events as ev
from .state import StateFile

QUIT_GRACE = 15  # s between closing claude's stdin and killing it


def socket_alive(path: Path, timeout: float = 2.0) -> bool:
    """Is there a supervisor serving on this socket?"""
    if not path.exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


class Supervisor:
    def __init__(
        self,
        label: str,
        folder: Path,
        account: str,
        command: List[str],
        env: Dict[str, str],
        state_dir: Path,
        sid: Optional[str] = None,
        transcript: Optional[Path] = None,
        log: Callable[[str], None] = print,
        name: Optional[str] = None,
        parent: Optional[str] = None,
        level: Optional[str] = None,
        permission: Optional[str] = None,
        role: Optional[str] = None,
        set_id: Optional[str] = None,
        pipeline: Optional[str] = None,
    ):
        self.label, self.folder, self.account = label, folder, account
        self.command, self.env, self.sid = command, env, sid
        self.transcript = transcript
        self.last_user = ""  # last page message: goes into state.json if the quota runs out, to be resent
        self.name = name  # session name (-n): how other sessions address it in SendMessage
        self.parent = parent  # level above another agent: label of the agent right below
        self.level = level  # name of the level in the recipe (professor, arquiteto…)
        self.permission = permission  # permission mode claude started with (--permission-mode)
        self.role = role  # extra prompt (role of a level, or the own prompt of a set's agent)
        self.set_id = set_id  # set (orqweb/server/sets.py) this agent came from
        self.pipeline = pipeline  # recipe (orqweb/pipelines/*.toml) this level came from
        self.dir = state_dir
        self.sock_path = state_dir / "agent.sock"
        self.state = StateFile(state_dir / "state.json")
        self.events: Optional[ev.EventLog] = None
        self.sent = ev.SentTexts()
        self.clients: List[socket.socket] = []
        self.clients_lock = threading.Lock()
        self.proc: Optional[subprocess.Popen] = None
        self.quitting = False
        self.log = log
        self._srv: Optional[socket.socket] = None
        self._done = threading.Event()

    # ------------------------------------------------------------ state
    def put_state(self, st: str, msg: Optional[str] = None) -> None:
        fields: Dict[str, Any] = dict(
            label=self.label,
            dir=str(self.folder),
            account=self.account,
            pid=os.getpid(),
            state=st,
            sid=self.sid,
            kind="sdk",
            sock=str(self.sock_path),
        )
        if self.name:
            fields["name"] = self.name
        if self.parent:
            fields["parent"] = self.parent
        if self.level:
            fields["level"] = self.level
        if self.permission:
            fields["permission"] = self.permission
        if self.role:
            fields["role"] = self.role
        if self.set_id:
            fields["set"] = self.set_id
        if self.pipeline:
            fields["pipeline"] = self.pipeline
        if msg is not None:
            fields["msg"] = msg
        try:
            self.state.write(**fields)
        except OSError as e:  # the state is informational; it never brings down the agent or a client
            self.log(f"warning: could not write state.json ({e})")

    # ------------------------------------------------------------ events
    def push(self, line: bytes, persist: bool = True) -> None:
        if persist and self.events is not None:
            self.events.append(line)
        with self.clients_lock:
            dead = []
            for c in self.clients:
                try:
                    c.sendall(line)
                except OSError:
                    dead.append(c)
            for c in dead:
                self.clients.remove(c)

    def note(self, text: str, level: str = "info") -> None:
        self.push(ev.orq_event(level, text))

    def _reader(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "stream_event":
                self.push(line, persist=False)
                continue
            if e.get("type") == "system" and e.get("subtype") == "init":
                self.sid = e.get("session_id") or self.sid
                self.put_state("waiting", None)
            if e.get("type") == "user":
                stamped = ev.stamp_origin(e, self.sent)
                if "orq_origin" in stamped:
                    line = ev.encode(stamped)
            st = ev.derive_state(e)
            if st:
                if st[0] == "quota":
                    self.state.write(retry=self.last_user)  # the server resends it on the new account
                    self.note(
                        f"account {self.account} ran out of quota; the page will switch accounts", "error"
                    )
                elif st[0] == "waiting":
                    self.state.write(retry="")
                self.put_state(*st)
            self.push(line)
        code = self.proc.wait()
        if self.quitting:
            self.put_state("closed", "")
            self.note("agent closed")
        else:
            self.put_state("closed", f"claude exited (code {code})")
            self.note(f"claude exited with code {code}", "error")
        self._shutdown()

    # ------------------------------------------------------------ clients
    def _serve(self, c: socket.socket) -> None:
        f = c.makefile("rb")
        try:
            try:
                first = json.loads(f.readline() or b"{}")
            except ValueError:
                first = {}
            since = max(0, int(first.get("since", 0)))
            with self.clients_lock:
                for line in self.events.since(since) if self.events else []:
                    c.sendall(line)
                self.clients.append(c)
            for raw in f:
                try:
                    o = json.loads(raw)
                except ValueError:
                    continue
                self.handle_client_message(o, raw)
        except OSError:
            pass
        finally:
            with self.clients_lock:
                if c in self.clients:
                    self.clients.remove(c)
            try:
                c.close()
            except OSError:
                pass

    def handle_client_message(self, o: Dict[str, Any], raw: bytes) -> None:
        if o.get("orq") == "quit":
            self.quit()
            return
        if o.get("type") == "user":
            txt = ev.text_of(o.get("message"))
            self.sent.add(txt)
            if txt:
                self.last_user = txt
        answered = ev.permission_answered(o)
        if answered:
            self.push(answered)
        st = ev.derive_state_from_client(o)
        if st:
            self.put_state(*st)
        if self.proc is not None and self.proc.stdin is not None:
            try:
                self.proc.stdin.write(raw if raw.endswith(b"\n") else raw + b"\n")
                self.proc.stdin.flush()
            except OSError:
                pass

    # ------------------------------------------------------------ life cycle
    def quit(self, *_: Any) -> None:
        if self.quitting:
            return
        self.quitting = True
        try:
            if self.proc is not None and self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass

        def hard() -> None:
            time.sleep(QUIT_GRACE)
            try:
                if self.proc is not None:
                    self.proc.kill()
            except OSError:
                pass

        threading.Thread(target=hard, daemon=True).start()

    def _shutdown(self) -> None:
        try:
            if self._srv is not None:
                self._srv.close()
        except OSError:
            pass
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass
        self._done.set()

    def start(self) -> None:
        """Starts the process and the socket; does not block. `wait()` waits for the end."""
        self.folder.mkdir(parents=True, exist_ok=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        if socket_alive(self.sock_path):
            raise RuntimeError(f"an agent {self.label} is already running.")
        if self.sock_path.exists():
            os.unlink(self.sock_path)
        self.events = ev.EventLog(self.dir / "events.jsonl")
        self.put_state("starting", "")
        self.proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=str(self.folder),
            env=self.env,
            bufsize=0,
        )
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(str(self.sock_path))
        self._srv.listen(8)
        os.chmod(str(self.sock_path), 0o600)
        how = f"resuming {self.sid[:8]}" if self.sid else "new conversation"
        if self.level and self.parent:
            how += f"  level {self.level} above {self.parent}"
        self.note(f"{self.label}  account {self.account}  {how}")
        if self.sid and self.transcript is not None:
            for entry in ev.transcript_entries(self.transcript):
                self.push(ev.encode(entry))
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._accept, daemon=True).start()
        self.put_state("waiting", "")
        self.log(f"▶ {self.label}  account {self.account}  folder {self.folder}  {how}")
        self.log(f"  socket {self.sock_path}  (Ctrl-C shuts the agent down; the conversation stays saved)")

    def _accept(self) -> None:
        assert self._srv is not None
        while True:
            try:
                c, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._done.wait(timeout)

    def run(self) -> None:
        """start() + wait, with Ctrl-C and SIGTERM shutting claude down cleanly."""
        self.start()
        signal.signal(signal.SIGTERM, self.quit)
        signal.signal(signal.SIGINT, self.quit)
        self.wait()
