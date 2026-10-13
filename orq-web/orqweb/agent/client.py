"""Lado cliente do socket do agente: attach (ponte stdin/stdout), stop, spawn em tmux."""

import json
import os
import shlex
import shutil
import signal
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


def stop_and_wait(sock_path: Path, timeout: float = 40.0) -> bool:
    """Manda encerrar e espera o socket sumir (o claude tem QUIT_GRACE s para sair). False se não sumiu."""
    if not stop(sock_path):
        return not socket_alive(sock_path)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not sock_path.exists():
            return True
        time.sleep(0.3)
    return False


def stop(sock_path: Path) -> bool:
    try:
        c = connect(sock_path, since=10**9, timeout=3)
        send(c, {"orq": "quit"})
        c.close()
        return True
    except OSError:
        return False


def start_command(
    label: str,
    folder: str,
    account: str,
    mode: str,
    role_file: Optional[str] = None,
    parent: Optional[str] = None,
    level: Optional[str] = None,
) -> List[str]:
    cmd = [
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
    if role_file:
        cmd += ["--role-file", role_file]
    if parent:
        cmd += ["--parent", parent]
    if level:
        cmd += ["--level", level]
    return cmd


def spawn_detached(cmd: List[str], sess: str, log: Path, cwd: str) -> str:
    """Roda `cmd` solto: numa sessão tmux `sess` (matando uma homônima) ou, sem tmux, em segundo plano.

    O stderr vai para `log`: o que o processo diz ao sair some com a sessão tmux, e fica lá para
    diagnóstico. Devolve uma linha dizendo como abriu."""
    env = dict(os.environ)
    env.pop("TMUX", None)
    tmux = shutil.which("tmux")
    if not tmux:
        with open(log, "ab") as fh:
            subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, start_new_session=True, env=env
            )
        return f"em segundo plano (sem tmux); log em {log}"
    subprocess.run([tmux, "kill-session", "-t", "=" + sess], env=env, capture_output=True)
    shell = tmux_shell(cmd, env) + " 2>>" + shlex.quote(str(log))
    subprocess.run(
        [tmux, "new-session", "-d", "-s", sess, "-c", cwd, shell],
        env=env,
        check=True,
        cwd=os.path.expanduser("~"),  # o servidor tmux herda a pasta de quem o cria; a pessoal nunca some
    )
    return f"na sessão tmux {sess}"


def tmux_shell(cmd: List[str], env: dict) -> str:
    """Linha de shell para o tmux: leva o PYTHONPATH junto.

    Se já existe um servidor tmux (aberto por outra coisa), a sessão nova herda o ambiente DELE, não o
    nosso, e o `python3 -m orqweb…` do wrapper de ~/.local/bin não acharia o pacote.
    """
    pp = env.get("PYTHONPATH")
    return shlex.join((["env", f"PYTHONPATH={pp}"] if pp else []) + cmd)


def spawn(
    label: str,
    folder: str,
    account: str,
    mode: str = "resume",
    wait: float = 20.0,
    role_file: Optional[str] = None,
    parent: Optional[str] = None,
    level: Optional[str] = None,
    restart: bool = False,
) -> str:
    """Garante o supervisor rodando: numa sessão tmux `orqs-<nome>` ou, sem tmux, em segundo plano.

    `restart` fecha o que estiver rodando com esse rótulo e sobe de novo (para o agente ganhar nome,
    trocar de conta etc.; com --mode resume a conversa continua a mesma).
    Devolve uma linha descrevendo o que fez. Levanta RuntimeError se o socket não aparecer.
    """
    d = config.state_dir(label)
    sock = d / "agent.sock"
    if socket_alive(sock):
        if not restart:
            return "já rodando"
        if not stop_and_wait(sock):
            raise RuntimeError(f"o agente {label} não encerrou para ser reaberto")
    cmd = start_command(label, folder, account, mode, role_file, parent, level)
    Path(os.path.expanduser(folder)).mkdir(parents=True, exist_ok=True)
    d.mkdir(parents=True, exist_ok=True)
    how = spawn_detached(
        cmd,
        "orqs-" + config.safe_label(label.split("@")[0]),
        d / "supervisor.log",
        os.path.expanduser(folder),
    )
    deadline = time.time() + wait
    while time.time() < deadline:
        if sock.exists():
            return how
        time.sleep(0.2)
    raise RuntimeError(f"o supervisor não abriu o socket em {wait:.0f} s ({how})")


# ---------------------------------------------------------------- vínculos (orq-agent link)
def spawn_link(local_label: str, remote_label: str, wait: float = 100.0) -> str:
    """Sobe `orq-agent link-run` numa sessão tmux `orql-<a>--<b>` (sem tmux: em segundo plano) e espera o
    vínculo ficar de pé. Na mesma máquina não há processo: só os espelhos entre contas e o registro.
    Devolve uma linha; RuntimeError se não subiu (com o motivo gravado pelo link)."""
    from . import link as L

    st = L.StateFile(L.link_file(local_label, remote_label))
    cur = st.read()
    if L.same_host(local_label, remote_label):
        for lab in (local_label, remote_label):
            if not socket_alive(config.state_dir(lab) / "agent.sock"):
                raise RuntimeError(f"{lab} não está rodando")
        if cur.get("resources"):  # sobras de um vínculo anterior entre os mesmos dois
            st.write(up=False)
            L.release(cur["resources"], L.config.state_root(), st.path)
        state = L.local_link(local_label, remote_label)
        st.write(**state)
        n = len(state["resources"])
        return "vínculo local (mesma máquina): " + (
            "os dois já se enxergavam" if not n else f"{n} cartões espelhados entre as contas"
        )
    if L.pid_alive(cur.get("pid")):  # de pé, ou ainda subindo: não derruba no meio
        return "já de pé" if cur.get("up") else "ainda subindo"
    try:
        st.path.unlink()
    except OSError:
        pass
    cmd = [sys.executable, "-m", "orqweb.agent.cli", "link-run", local_label, remote_label]
    d = L.links_dir(local_label)
    d.mkdir(parents=True, exist_ok=True)
    how = "vínculo " + spawn_detached(
        cmd,
        link_session(local_label, remote_label),
        d / (config.safe_label(remote_label) + ".log"),
        os.path.expanduser("~"),
    )
    deadline = time.time() + wait
    while time.time() < deadline:
        cur = st.read()
        if cur.get("up"):
            return how
        if cur and not cur.get("up"):
            raise RuntimeError(cur.get("why") or "o vínculo não subiu")
        time.sleep(0.3)
    raise RuntimeError(f"o vínculo não subiu em {wait:.0f} s ({how})")


def link_session(local_label: str, remote_label: str) -> str:
    return "orql-" + config.safe_label(local_label.split("@")[0]) + "--" + config.safe_label(remote_label)


def unlink(local_label: str, remote_label: str, wait: float = 20.0) -> str:
    """Manda o processo do vínculo sair (ele desfaz os espelhos e fecha o túnel) e espera."""
    from . import link as L

    st = L.StateFile(L.link_file(local_label, remote_label))
    cur = st.read()
    if cur.get("kind") == "local":
        st.write(up=False, why="unlink")
        kept = L.release(cur.get("resources") or [], L.config.state_root(), st.path)
        st.write(resources=kept)
        return "vínculo desfeito"
    pid = cur.get("pid")
    if not (cur.get("up") and L.pid_alive(pid)):
        return "não havia vínculo de pé"
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return "não havia vínculo de pé"
    deadline = time.time() + wait
    while time.time() < deadline:
        if not st.read().get("up"):
            return "vínculo desfeito"
        time.sleep(0.3)
    return f"o vínculo (pid {pid}) não respondeu em {wait:.0f} s"
