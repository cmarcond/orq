"""Supervisor: um `claude -p` em stream-json atrás de um socket Unix.

Clientes conectam, mandam `{"since": N}` e recebem os eventos a partir de N, depois os ao vivo.
O que mandarem em seguida vai para o stdin do claude; `{"orq": "quit"}` encerra. Eventos
`stream_event` (parciais) só vão ao vivo, não entram na sequência.
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

QUIT_GRACE = 15  # s entre fechar o stdin do claude e matá-lo


def socket_alive(path: Path, timeout: float = 2.0) -> bool:
    """Há um supervisor atendendo neste socket?"""
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
    ):
        self.label, self.folder, self.account = label, folder, account
        self.command, self.env, self.sid = command, env, sid
        self.transcript = transcript
        self.last_user = ""  # última mensagem da página: vai no state.json se a cota acabar, para reenvio
        self.name = name  # nome da sessão (-n): como outras sessões a endereçam no SendMessage
        self.parent = parent  # nível acima de outro agente: rótulo do agente logo abaixo
        self.level = level  # nome do nível na receita (professor, arquiteto…)
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

    # ------------------------------------------------------------ estado
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
        if msg is not None:
            fields["msg"] = msg
        try:
            self.state.write(**fields)
        except OSError as e:  # estado é informativo; nunca derruba o agente nem um cliente
            self.log(f"aviso: não consegui gravar state.json ({e})")

    # ------------------------------------------------------------ eventos
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
                    self.state.write(retry=self.last_user)  # o servidor reenvia na conta nova
                    self.note(f"a conta {self.account} ficou sem cota; a página vai trocar de conta", "error")
                elif st[0] == "waiting":
                    self.state.write(retry="")
                self.put_state(*st)
            self.push(line)
        code = self.proc.wait()
        if self.quitting:
            self.put_state("closed", "")
            self.note("agente encerrado")
        else:
            self.put_state("closed", f"o claude saiu (código {code})")
            self.note(f"o claude saiu com código {code}", "error")
        self._shutdown()

    # ------------------------------------------------------------ clientes
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

    # ------------------------------------------------------------ ciclo de vida
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
        """Sobe o processo e o socket; não bloqueia. `wait()` espera o fim."""
        self.folder.mkdir(parents=True, exist_ok=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        if socket_alive(self.sock_path):
            raise RuntimeError(f"já existe um agente {self.label} rodando.")
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
        how = f"retomando {self.sid[:8]}" if self.sid else "conversa nova"
        if self.level and self.parent:
            how += f"  nível {self.level} sobre {self.parent}"
        self.note(f"{self.label}  conta {self.account}  {how}")
        if self.sid and self.transcript is not None:
            for entry in ev.transcript_entries(self.transcript):
                self.push(ev.encode(entry))
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._accept, daemon=True).start()
        self.put_state("waiting", "")
        self.log(f"▶ {self.label}  conta {self.account}  pasta {self.folder}  {how}")
        self.log(f"  socket {self.sock_path}  (Ctrl-C encerra o agente; a conversa fica salva)")

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
        """start() + espera, com Ctrl-C e SIGTERM encerrando o claude de forma limpa."""
        self.start()
        signal.signal(signal.SIGTERM, self.quit)
        signal.signal(signal.SIGINT, self.quit)
        self.wait()
