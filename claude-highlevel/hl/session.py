"""A live Claude Code session, inside a PTY that the app controls."""
from __future__ import annotations

import errno
import os
import pty
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import config, gates

SCREEN_WINDOW = 8000        # recent bytes that stand for "the screen right now"
QUIETUDE = 2.5              # seconds without repainting that already count as a stopped session
RING = 64_000


@dataclass
class LevelSpec:
    """What defines a level of the pipeline."""
    name: str
    system_prompt: str
    permission_mode: str = "acceptEdits"
    model: str | None = None
    effort: str | None = None
    color: str = "37"


@dataclass
class Session:
    spec: LevelSpec
    cwd: Path
    level: int
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    raw_log: Path | None = None

    proc: subprocess.Popen | None = None
    _master: int | None = None
    _buf: bytearray = field(default_factory=bytearray)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _answered: set[str] = field(default_factory=set)
    _reader: threading.Thread | None = None
    _sockets_before: set[str] = field(default_factory=set)
    _ultima_saida: float = 0.0
    on_gate: object = None          # callable(Session, Gate) — for logging only

    # ----------------------------------------------------------- life cycle

    def argv(self) -> list[str]:
        cmd = [
            "claude",
            "-n", self.spec.name,
            "--session-id", self.session_id,
            "--permission-mode", self.spec.permission_mode,
            "--append-system-prompt", self.spec.system_prompt,
        ]
        if self.spec.model:
            cmd += ["--model", self.spec.model]
        if self.spec.effort:
            cmd += ["--effort", self.spec.effort]
        return cmd

    def start(self) -> None:
        self._sockets_before = self._sockets()
        master, slave = pty.openpty()
        self._master = master
        self.proc = subprocess.Popen(
            self.argv(),
            cwd=str(self.cwd),
            stdin=slave, stdout=slave, stderr=slave,
            env=config.child_env(),
            start_new_session=True,      # its own group: the whole tree can be killed
        )
        os.close(slave)
        self._reader = threading.Thread(target=self._pump, name=f"pty-{self.spec.name}", daemon=True)
        self._reader.start()

    def _sockets(self) -> set[str]:
        nomes: set[str] = set()
        for diretorio in config.SOCKET_DIRS:
            try:
                nomes |= {p.name for p in diretorio.iterdir()}
            except OSError:
                continue
        return nomes

    def wait_ready(self, timeout: float = 45.0) -> bool:
        """Ready = registered as a peer and with no dialog on screen.

        The transcript does NOT work as a signal: the JSONL is only born on the first turn, and a
        level that has not received any message yet would never have one. What shows up at the
        instant the session goes live is the peer socket, named after the pid.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.alive:
                return False
            registered = (
                config.socket_for_pid(self.proc.pid).exists()
                or bool(self._sockets() - self._sockets_before)
            )
            if registered and self.prompt_visible() and self.screen_is_clear():
                return True
            time.sleep(0.4)
        return False

    def prompt_visible(self) -> bool:
        """The input box has already been drawn — the socket alone does not guarantee that.

        Looking for the ❯ on the normalized screen does not work: it is also the selection
        cursor of the dialogs, and a "❯ No, exit" used to pass for an input box. The one that knows
        the difference is `gates.prompt_box_visible`, over the raw bytes.
        """
        return gates.prompt_box_visible(self._raw_screen())

    def screen_is_clear(self) -> bool:
        """No boot dialog waiting for an answer on screen."""
        text = self.screen()
        return not any(gate.matches(text) for gate in gates.BOOT_GATES)

    def stop(self, timeout: float = 6.0) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if self._master is not None:
            try:
                os.close(self._master)
            except OSError:
                pass
            self._master = None

    # ----------------------------------------------------------------- state

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def transcript(self) -> Path | None:
        return config.find_transcript(self.session_id, self.cwd)

    def _raw_screen(self) -> bytes:
        with self._lock:
            return bytes(self._buf[-SCREEN_WINDOW:])

    def screen(self) -> str:
        return gates.screen_text(self._raw_screen())

    def activity(self) -> str:
        """ocioso (idle) · trabalhando (working) · bloqueado (blocked) · morto (dead) — the
        state chip of the interface.

        Looking for the "esc to interrupt" footer on screen is not enough: it stays stuck in the
        buffer after the session stops, and the level would be "trabalhando" forever. And the idle
        footer does not work as a cross-check, because it changes with the permission mode.

        The reliable signal is the rhythm of the terminal itself: while a turn is in progress the
        TUI repaints all the time (the spinner); when it ends, it goes quiet. So trabalhando
        is the footer on screen **and** recent output on the PTY.
        """
        if not self.alive:
            return "morto"
        if self.blocked_on():
            return "bloqueado"
        recente = time.time() - self._ultima_saida < QUIETUDE
        if recente and gates.WORKING_MARK in gates.compact(self.screen()):
            return "trabalhando"
        return "ocioso"

    def blocked_on(self) -> "gates.Block | None":
        """What is blocking the session right now, if it is blocked."""
        return gates.find_block(self.screen())

    # ---------------------------------------------------------------- writing

    def send_text(self, text: str) -> None:
        """Sends a message as if you had typed it.

        Ordinary text goes in bracketed paste: without it, a line break submits the
        message half-way. A slash command goes as normal typing, which is the path
        that Claude Code itself expects in order to recognize the command.
        """
        if text.lstrip().startswith("/"):
            self.send_keys(text.encode())
        else:
            self.send_keys(b"\x1b[200~" + text.encode() + b"\x1b[201~")
        time.sleep(0.35)
        self.send_keys(b"\r")

    def send_keys(self, data: bytes) -> None:
        if self._master is None:
            raise RuntimeError(f"session {self.spec.name} is not open")
        try:
            os.write(self._master, data)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise

    def interrupt(self) -> None:
        self.send_keys(gates.ESC)

    # ---------------------------------------------------------------- reading

    def _pump(self) -> None:
        sink = open(self.raw_log, "wb") if self.raw_log else None
        try:
            while True:
                try:
                    chunk = os.read(self._master, 65536)
                except (OSError, TypeError):
                    break
                if not chunk:
                    break
                self._ultima_saida = time.time()
                if sink:
                    sink.write(chunk)
                    sink.flush()
                with self._lock:
                    self._buf += chunk
                    del self._buf[:-RING]
                self._answer_boot_gate()
        finally:
            if sink:
                sink.close()

    def _answer_boot_gate(self) -> None:
        gate = gates.find_boot_gate(self.screen(), self._answered)
        if gate is None:
            return
        self._answered.add(gate.name)
        if callable(self.on_gate):
            self.on_gate(self, gate)

        def respond(keys: bytes = gate.keys) -> None:
            for part in (keys[:-1], keys[-1:]) if len(keys) > 1 else (keys,):
                if part:
                    self.send_keys(part)
                    time.sleep(0.25)
            with self._lock:
                self._buf.clear()

        threading.Timer(0.6, respond).start()
