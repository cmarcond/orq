"""Lado cliente do socket do agente: attach (ponte stdin/stdout), stop, spawn em tmux."""

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO, List, Optional

from .. import config
from .supervisor import socket_alive


def connect(sock_path: Path, since: int = 0, timeout: Optional[float] = None) -> socket.socket:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if timeout is not None:
        c.settimeout(timeout)
    c.connect(str(sock_path))
    c.sendall((json.dumps({"since": since}) + "\n").encode())
    return c


def send(c: socket.socket, obj: dict) -> None:
    c.sendall((json.dumps(obj) + "\n").encode())


def attach(sock_path: Path, since: int, stdin: BinaryIO, stdout: BinaryIO) -> int:
    """Ponte: eventos do socket -> stdout; linhas do stdin -> socket. Termina quando um lado fecha."""
    if not sock_path.exists():
        stdout.write(
            json.dumps(
                {
                    "type": "orq",
                    "level": "error",
                    "text": f"agente não está rodando ({sock_path.parent.name})",
                }
            ).encode()
            + b"\n"
        )
        stdout.flush()
        return 1
    c = connect(sock_path, since)
    closed = threading.Event()

    def down() -> None:
        try:
            while True:
                b = c.recv(65536)
                if not b:
                    break
                stdout.write(b)
                stdout.flush()
        except OSError:
            pass
        closed.set()

    t = threading.Thread(target=down, daemon=True)
    t.start()
    try:
        while not closed.is_set():
            line = stdin.readline()
            if not line:
                break
            c.sendall(line)
    except OSError:
        pass
    try:
        c.shutdown(socket.SHUT_WR)
    except OSError:
        pass
    closed.wait(0.5)
    return 0


def stop(sock_path: Path) -> bool:
    try:
        c = connect(sock_path, since=10**9, timeout=3)
        send(c, {"orq": "quit"})
        c.close()
        return True
    except OSError:
        return False


def start_command(label: str, folder: str, account: str, mode: str) -> List[str]:
    return [
        sys.executable,
        "-m",
        "orqweb.agent.cli",
        "start",
        "--dir",
        folder,
        "--account",
        account,
        "--label",
        label,
        "--mode",
        mode,
    ]


def spawn(label: str, folder: str, account: str, mode: str = "resume", wait: float = 20.0) -> str:
    """Garante o supervisor rodando: numa sessão tmux `orqs-<nome>` ou, sem tmux, em segundo plano.

    Devolve uma linha descrevendo o que fez. Levanta RuntimeError se o socket não aparecer.
    """
    d = config.state_dir(label)
    sock = d / "agent.sock"
    if socket_alive(sock):
        return "já rodando"
    cmd = start_command(label, folder, account, mode)
    env = dict(os.environ)
    env.pop("TMUX", None)
    Path(os.path.expanduser(folder)).mkdir(parents=True, exist_ok=True)
    tmux = shutil.which("tmux")
    if tmux:
        sess = "orqs-" + config.safe_label(label.split("@")[0])
        subprocess.run([tmux, "kill-session", "-t", "=" + sess], env=env, capture_output=True)
        subprocess.run(
            [tmux, "new-session", "-d", "-s", sess, "-c", os.path.expanduser(folder), shlex.join(cmd)],
            env=env,
            check=True,
        )
        how = f"aberto na sessão tmux {sess}"
    else:
        d.mkdir(parents=True, exist_ok=True)
        log = open(d / "supervisor.log", "ab")
        subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, env=env
        )
        how = f"aberto em segundo plano (sem tmux); log em {d / 'supervisor.log'}"
    deadline = time.time() + wait
    while time.time() < deadline:
        if sock.exists():
            return how
        time.sleep(0.2)
    raise RuntimeError(f"o supervisor não abriu o socket em {wait:.0f} s ({how})")
