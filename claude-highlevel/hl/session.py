"""Uma sessão Claude Code viva, dentro de um PTY que o app controla."""
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

SCREEN_WINDOW = 8000        # bytes recentes que representam "a tela agora"
QUIETUDE = 2.5              # segundos sem repintar que já contam como sessão parada
RING = 64_000


@dataclass
class LevelSpec:
    """O que define um nível do pipeline."""
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
    on_gate: object = None          # callable(Session, Gate) — só para log

    # ---------------------------------------------------------------- ciclo

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
            start_new_session=True,      # grupo próprio: dá para matar a árvore toda
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
        """Pronta = registrada como peer e sem diálogo na tela.

        O transcript NÃO serve como sinal: o JSONL só nasce no primeiro turno, e um
        nível que ainda não recebeu mensagem nenhuma nunca teria um. Quem aparece no
        instante em que a sessão entra no ar é o socket de peer, nomeado pelo pid.
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
        """A caixa de entrada já foi desenhada — só o socket não garante isso.

        Procurar o ❯ na tela normalizada não serve: ele é também o cursor de seleção
        dos diálogos, e um "❯ No, exit" passava por caixa de entrada. Quem sabe a
        diferença é `gates.prompt_box_visible`, sobre os bytes crus.
        """
        return gates.prompt_box_visible(self._raw_screen())

    def screen_is_clear(self) -> bool:
        """Nenhum diálogo de boot esperando resposta na tela."""
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

    # ---------------------------------------------------------------- estado

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
        """ocioso · trabalhando · bloqueado · morto — o chip de estado da interface.

        Procurar o rodapé "esc to interrupt" na tela não basta: ele fica preso no buffer
        depois que a sessão para, e o nível ficaria "trabalhando" para sempre. E o rodapé
        de ocioso não serve de contraprova, porque muda conforme o modo de permissão.

        O sinal confiável é o próprio ritmo do terminal: enquanto há turno em andamento a
        TUI repinta o tempo todo (o spinner); quando acaba, ela emudece. Então trabalhando
        é o rodapé na tela **e** saída recente no PTY.
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
        """O que está travando a sessão agora, se está travada."""
        return gates.find_block(self.screen())

    # ---------------------------------------------------------------- escrita

    def send_text(self, text: str) -> None:
        """Manda uma mensagem como se você tivesse digitado.

        Texto comum vai em bracketed paste: sem isso, uma quebra de linha submete a
        mensagem pela metade. Slash command vai como digitação normal, que é o caminho
        que o próprio Claude Code espera para reconhecer o comando.
        """
        if text.lstrip().startswith("/"):
            self.send_keys(text.encode())
        else:
            self.send_keys(b"\x1b[200~" + text.encode() + b"\x1b[201~")
        time.sleep(0.35)
        self.send_keys(b"\r")

    def send_keys(self, data: bytes) -> None:
        if self._master is None:
            raise RuntimeError(f"sessão {self.spec.name} não está aberta")
        try:
            os.write(self._master, data)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise

    def interrupt(self) -> None:
        self.send_keys(gates.ESC)

    # ---------------------------------------------------------------- leitura

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
