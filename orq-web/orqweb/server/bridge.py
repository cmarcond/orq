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


def attach_command(
    label: str,
    since: int,
    bin_dir: Optional[Path] = None,
    local_host: Optional[str] = LAPTOP,
    ssh_config: Optional[str] = None,
) -> List[str]:
    host = config.host_of(label)
    if local_host and host == local_host:
        return [str((bin_dir or config.bin_dir()) / "orq-agent"), "attach", label, "--since", str(since)]
    remote = "~/.local/bin/orq-agent attach " + shlex.quote(label) + " --since " + str(since)
    return ssh_cmd(host, remote, 8, ssh_config)


def _with_keepalive(cmd: List[str]) -> List[str]:
    if cmd and cmd[0] == "ssh":
        return cmd[:1] + ["-o", "ServerAliveInterval=15"] + cmd[1:]
    return cmd


class Bridge:
    def __init__(
        self,
        label: str,
        since: int,
        bin_dir: Optional[Path] = None,
        command: Optional[List[str]] = None,
        local_host: Optional[str] = LAPTOP,
        ssh_config: Optional[str] = None,
    ):
        self.label = label
        self.p = subprocess.Popen(
            command or _with_keepalive(attach_command(label, since, bin_dir, local_host, ssh_config)),
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

    def end_input(self) -> None:
        """Fecha só o stdin (EOF): o attach entrega o que já recebeu e termina sozinho."""
        try:
            if self.p.stdin:
                self.p.stdin.close()
        except OSError:
            pass

    def close(self) -> None:
        for fn in (lambda: self.p.stdin and self.p.stdin.close(), self.p.kill):
            try:
                fn()
            except OSError:
                pass


class BridgeRegistry:
    """Uma ponte por rótulo (a da aba aberta). `deliver` usa a da aba ou abre uma passageira."""

    def __init__(
        self,
        bin_dir: Optional[Path] = None,
        factory=None,
        local_host: Optional[str] = LAPTOP,
        ssh_config: Optional[str] = None,
    ):
        self.bin_dir = bin_dir
        self.factory = factory or (
            lambda label, since, bd: Bridge(label, since, bd, local_host=local_host, ssh_config=ssh_config)
        )
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

    def deliver(self, label: str, obj: dict, linger: float = 30.0, fresh: bool = False) -> Optional[str]:
        """Entrega `obj` ao agente. Erro em texto, ou None.

        `fresh` ignora a ponte da aba e abre uma passageira: depois de o agente ser reaberto (outra conta,
        outro pid), a ponte da aba pode ainda estar presa ao supervisor anterior. A passageira recebe a
        mensagem e o EOF e termina por si quando o attach entregou; `linger` é só o seguro que a mata se
        ela não terminar. Matá-la cedo (1,5 s, como era) perdia a mensagem por ssh: o handshake com a outra
        máquina ainda não tinha acabado quando o ssh morria, e o conjunto não acusava erro."""
        with self._lock:
            b = None if fresh else self._b.get(label)
        temp = b is None or not b.alive()
        if temp:
            b = self.factory(label, FAR_FUTURE, self.bin_dir)
        try:
            b.send(obj)
        except (OSError, ValueError) as e:
            return f"não consegui falar com o agente ({e})"
        if temp:
            b.end_input()
            threading.Timer(linger, b.close).start()
        return None
