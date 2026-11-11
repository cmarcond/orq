"""Bridge between the server and an agent: one `orq-agent attach` (local or over ssh) per open tab."""

import json
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .. import config
from .external import LAPTOP, ssh_cmd

FAR_FUTURE = 10**9  # since "in the future": the bridge gets no replay, it only serves to deliver something


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
        """Closes only stdin (EOF): the attach delivers what it has already received and ends by itself."""
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
    """One bridge per label (the open tab's). `deliver` uses the tab's one or opens a temporary one."""

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
        """Opens the tab's bridge; returns (new, old one to close)."""
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
        """Delivers `obj` to the agent. Error as text, or None.

        `fresh` ignores the tab's bridge and opens a temporary one: after the agent is reopened (another
        account, another pid), the tab's bridge may still be stuck to the previous supervisor. The temporary
        one receives the message and the EOF and ends by itself once the attach has delivered; `linger` is
        only the safeguard that kills it if it does not end. Killing it early (1.5 s, as it used to be) lost
        the message over ssh: the handshake with the other machine had not finished yet when the ssh died,
        and the set reported no error."""
        with self._lock:
            b = None if fresh else self._b.get(label)
        temp = b is None or not b.alive()
        if temp:
            b = self.factory(label, FAR_FUTURE, self.bin_dir)
        try:
            b.send(obj)
        except (OSError, ValueError) as e:
            return f"could not talk to the agent ({e})"
        if temp:
            b.end_input()
            threading.Timer(linger, b.close).start()
        return None
