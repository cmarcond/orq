"""Client side of the agent's socket: attach (stdin/stdout bridge), stop, spawn in tmux."""

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
    """Bridge: socket events -> stdout; stdin lines -> socket. Ends when one side closes."""
    if not sock_path.exists():
        stdout.write(
            json.dumps(
                {
                    "type": "orq",
                    "level": "error",
                    "text": f"agent is not running ({sock_path.parent.name})",
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
    """Tells it to quit and waits for the socket to vanish (claude has QUIT_GRACE s to exit). False if not."""
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
    permission: Optional[str] = None,
    set_id: Optional[str] = None,
    pipeline: Optional[str] = None,
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
    if permission:
        cmd += ["--permission-mode", permission]
    if set_id:
        cmd += ["--set", set_id]
    if pipeline:
        cmd += ["--pipeline", pipeline]
    return cmd


def spawn_detached(cmd: List[str], sess: str, log: Path, cwd: str) -> str:
    """Runs `cmd` detached: in a tmux session `sess` (killing one with the same name) or, without tmux,
    in the background.

    The stderr goes to `log`: what the process says on exit vanishes with the tmux session, and stays
    there for diagnosis. Returns a line saying how it was opened."""
    env = dict(os.environ)
    env.pop("TMUX", None)
    tmux = shutil.which("tmux")
    if not tmux:
        with open(log, "ab") as fh:
            subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, start_new_session=True, env=env
            )
        return f"in the background (no tmux); log at {log}"
    subprocess.run([tmux, "kill-session", "-t", "=" + sess], env=env, capture_output=True)
    shell = tmux_shell(cmd, env) + " 2>>" + shlex.quote(str(log))
    subprocess.run(
        [tmux, "new-session", "-d", "-s", sess, "-c", cwd, shell],
        env=env,
        check=True,
        cwd=os.path.expanduser("~"),  # the tmux server inherits its creator's folder; home never goes away
    )
    return f"in tmux session {sess}"


def tmux_shell(cmd: List[str], env: dict) -> str:
    """Shell line for tmux: carries PYTHONPATH along.

    If a tmux server already exists (opened by something else), the new session inherits ITS environment,
    not ours, and the `python3 -m orqweb…` of the wrapper in ~/.local/bin would not find the package.
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
    permission: Optional[str] = None,
    set_id: Optional[str] = None,
    pipeline: Optional[str] = None,
) -> str:
    """Makes sure the supervisor is running: in a tmux session `orqs-<name>` or, without tmux, in the
    background.

    `restart` closes whatever is running with this label and starts it again (so the agent gets a name,
    switches account etc.; with --mode resume the conversation stays the same).
    Returns a line describing what it did. Raises RuntimeError if the socket does not appear.
    """
    d = config.state_dir(label)
    sock = d / "agent.sock"
    if socket_alive(sock):
        if not restart:
            return "already running"
        if not stop_and_wait(sock):
            raise RuntimeError(f"agent {label} did not shut down to be reopened")
    cmd = start_command(label, folder, account, mode, role_file, parent, level, permission, set_id, pipeline)
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
    raise RuntimeError(f"the supervisor did not open the socket within {wait:.0f} s ({how})")


# ---------------------------------------------------------------- links (orq-agent link)
def spawn_link(local_label: str, remote_label: str, wait: float = 100.0) -> str:
    """Starts `orq-agent link-run` in a tmux session `orql-<a>--<b>` (without tmux: in the background) and
    waits for the link to come up. On the same machine there is no process: only the mirrors between
    accounts and the record. Returns a line; RuntimeError if it did not come up (with the reason recorded
    by the link)."""
    from . import link as L

    st = L.StateFile(L.link_file(local_label, remote_label))
    cur = st.read()
    if L.same_host(local_label, remote_label):
        for lab in (local_label, remote_label):
            if not socket_alive(config.state_dir(lab) / "agent.sock"):
                raise RuntimeError(f"{lab} is not running")
        if cur.get("resources"):  # leftovers of a previous link between the same two
            st.write(up=False)
            L.release(cur["resources"], L.config.state_root(), st.path)
        state = L.local_link(local_label, remote_label)
        st.write(**state)
        n = len(state["resources"])
        return "local link (same machine): " + (
            "the two already saw each other" if not n else f"{n} cards mirrored between the accounts"
        )
    if L.pid_alive(cur.get("pid")):  # up, or still coming up: do not tear it down midway
        return "already up" if cur.get("up") else "still coming up"
    try:
        st.path.unlink()
    except OSError:
        pass
    cmd = [sys.executable, "-m", "orqweb.agent.cli", "link-run", local_label, remote_label]
    d = L.links_dir(local_label)
    d.mkdir(parents=True, exist_ok=True)
    how = "link " + spawn_detached(
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
            raise RuntimeError(cur.get("why") or "the link did not come up")
        time.sleep(0.3)
    raise RuntimeError(f"the link did not come up within {wait:.0f} s ({how})")


def link_session(local_label: str, remote_label: str) -> str:
    return "orql-" + config.safe_label(local_label.split("@")[0]) + "--" + config.safe_label(remote_label)


def unlink(local_label: str, remote_label: str, wait: float = 20.0) -> str:
    """Tells the link's process to exit (it undoes the mirrors and closes the tunnel) and waits."""
    from . import link as L

    st = L.StateFile(L.link_file(local_label, remote_label))
    cur = st.read()
    if cur.get("kind") == "local":
        st.write(up=False, why="unlink")
        kept = L.release(cur.get("resources") or [], L.config.state_root(), st.path)
        st.write(resources=kept)
        return "link undone"
    pid = cur.get("pid")
    if not (cur.get("up") and L.pid_alive(pid)):
        return "there was no link up"
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return "there was no link up"
    deadline = time.time() + wait
    while time.time() < deadline:
        if not st.read().get("up"):
            return "link undone"
        time.sleep(0.3)
    return f"the link (pid {pid}) did not respond within {wait:.0f} s"
