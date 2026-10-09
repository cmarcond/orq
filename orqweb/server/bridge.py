"""Ponte entre o servidor e um agente: um `orq-agent attach` (local ou por ssh) por aba aberta."""

import json
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .. import config
from .external import LAPTOP, ssh_cmd

FAR_FUTURE = 10**9  # since "no futuro": a ponte não recebe replay, só serve para entregar algo


def attach_command(label: str, since: int, bin_dir: Optional[Path] = None) -> List[str]:
    host = config.host_of(label)
    if host == LAPTOP:
        return [str((bin_dir or config.bin_dir()) / "orq-agent"), "attach", label, "--since", str(since)]
    remote = "~/.local/bin/orq-agent attach " + shlex.quote(label) + " --since " + str(since)
    return ssh_cmd(host, remote, 8)


def _with_keepalive(cmd: List[str]) -> List[str]:
    if cmd and cmd[0] == "ssh":
        return cmd[:1] + ["-o", "ServerAliveInterval=15"] + cmd[1:]
    return cmd


class Bridge:
    def __init__(
        self, label: str, since: int, bin_dir: Optional[Path] = None, command: Optional[List[str]] = None
    ):
        self.label = label
        self.p = subprocess.Popen(
            command or _with_keepalive(attach_command(label, since, bin_dir)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._wl = threading.Lock()

    def send(self, obj: dict) -> None:
        assert self.p.stdin is not None
        with self._wl:
            self.p.stdin.write((json.dumps(obj) + "\n").encode())
            self.p.stdin.flush()

    def lines(self) -> Iterator[bytes]:
        assert self.p.stdout is not None
        yield from self.p.stdout

    def alive(self) -> bool:
        return self.p.poll() is None

    def close(self) -> None:
        for fn in (lambda: self.p.stdin and self.p.stdin.close(), self.p.kill):
            try:
                fn()
            except OSError:
                pass


class BridgeRegistry:
    """Uma ponte por rótulo (a da aba aberta). `deliver` usa a da aba ou abre uma passageira."""

    def __init__(self, bin_dir: Optional[Path] = None, factory=Bridge):
        self.bin_dir = bin_dir
        self.factory = factory
        self._b: Dict[str, Bridge] = {}
        self._lock = threading.Lock()

    def open(self, label: str, since: int) -> Tuple[Bridge, Optional[Bridge]]:
        """Abre a ponte da aba; devolve (nova, antiga a fechar)."""
        b = self.factory(label, since, self.bin_dir)
        with self._lock:
            old = self._b.get(label)
            self._b[label] = b
        return b, old

    def release(self, label: str, b: Bridge) -> None:
        with self._lock:
            if self._b.get(label) is b:
                del self._b[label]
        b.close()

    def deliver(self, label: str, obj: dict, linger: float = 1.5) -> Optional[str]:
        """Entrega `obj` ao agente. Erro em texto, ou None."""
        with self._lock:
            b = self._b.get(label)
        temp = b is None or not b.alive()
        if temp:
            b = self.factory(label, FAR_FUTURE, self.bin_dir)
        try:
            b.send(obj)
        except (OSError, ValueError) as e:
            return f"não consegui falar com o agente ({e})"
        if temp:
            threading.Timer(linger, b.close).start()
        return None
