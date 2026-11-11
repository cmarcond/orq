"""Converts an interactive `claude` running in a tmux on this machine into a page agent.

The conversation is already on disk (Claude Code writes every message), so converting means: closing the
interactive program without violence, and starting the supervisor in the same folder and account with
`--mode resume`. The closing sequence escalates: /exit, double Ctrl-C, SIGTERM. Never SIGKILL.
"""

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .. import config

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _run(cmd: List[str], **kw) -> "subprocess.CompletedProcess[str]":
    env = dict(os.environ)
    env.pop("TMUX", None)  # talks to the default tmux server, even from inside another tmux
    return subprocess.run(cmd, capture_output=True, text=True, env=env, **kw)


class Tmux:
    """The little bit of tmux the conversion needs, through a runner that tests can swap."""

    def __init__(self, run: Runner = _run, binary: str = "tmux"):
        self.run, self.bin = run, binary

    def has_session(self, sess: str) -> bool:
        return self.run([self.bin, "has-session", "-t", "=" + sess]).returncode == 0

    def display(self, sess: str, fmt: str) -> str:
        r = self.run([self.bin, "display-message", "-p", "-t", sess, fmt])
        return r.stdout.strip() if r.returncode == 0 else ""

    def pane_command(self, sess: str) -> str:
        return self.display(sess, "#{pane_current_command}")

    def pane_pid(self, sess: str) -> Optional[int]:
        s = self.display(sess, "#{pane_pid}")
        return int(s) if s.isdigit() else None

    def send(self, sess: str, *keys: str, literal: bool = False) -> None:
        self.run([self.bin, "send-keys", "-t", sess] + (["-l"] if literal else []) + list(keys))

    def kill_session(self, sess: str) -> None:
        self.run([self.bin, "kill-session", "-t", "=" + sess])


VERSION_NAME = re.compile(r"^\d+(\.\d+)+$")


def is_claude(name: str) -> bool:
    """The Claude Code process is called `claude` (Linux) or by the version number, the name of the real
    binary under ~/.local/share/claude/versions (macOS)."""
    base = os.path.basename(name or "")
    return base.startswith("claude") or bool(VERSION_NAME.match(base))


def claude_pids_under(pane_pid: int, run: Runner = _run) -> List[int]:
    """PIDs of `claude` processes descending from the pane (Claude Code may be under a shell)."""
    r = run(["ps", "-eo", "pid=,ppid=,comm="])
    kids: Dict[int, List[int]] = {}
    names: Dict[int, str] = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, comm = int(parts[0]), int(parts[1]), parts[2]
        kids.setdefault(ppid, []).append(pid)
        names[pid] = comm
    out, stack = [], [pane_pid]
    while stack:
        p = stack.pop()
        if is_claude(names.get(p, "")):
            out.append(p)
        stack += kids.get(p, [])
    return out


def claude_gone(tmux: Tmux, sess: str) -> bool:
    """The interactive claude has exited: the session ended, or the pane went back to a shell."""
    return not tmux.has_session(sess) or not is_claude(tmux.pane_command(sess))


def close_claude(
    tmux: Tmux,
    sess: str,
    wait: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    kill: Callable[[int, int], None] = os.kill,
    run: Runner = _run,
) -> str:
    """Closes the claude in tmux `sess`, escalating. Returns how: 'exit', 'ctrl-c', 'sigterm' or 'failed'."""
    if claude_gone(tmux, sess):
        return "exit"

    def settle(how: str) -> Optional[str]:
        for _ in range(int(wait * 2)):
            if claude_gone(tmux, sess):
                return how
            sleep(0.5)
        return None

    tmux.send(sess, "Escape")
    sleep(0.3)
    tmux.send(sess, "/exit", literal=True)
    sleep(0.3)
    tmux.send(sess, "Enter")
    how = settle("exit")
    if how:
        return how
    tmux.send(sess, "C-c")
    sleep(0.4)
    tmux.send(sess, "C-c")
    how = settle("ctrl-c")
    if how:
        return how
    pane = tmux.pane_pid(sess)
    for pid in claude_pids_under(pane, run) if pane else []:
        try:
            kill(pid, signal.SIGTERM)
        except OSError:
            pass
    return settle("sigterm") or "failed"


def terminal_sessions(status: List[dict]) -> List[dict]:
    """Entries of `orq --status-json` that are an interactive claude in tmux (not page agents)."""
    return [a for a in status if a.get("kind") != "sdk" and a.get("tmux") and a.get("state") != "closed"]


def others_in_folder(status: List[dict], entry: dict) -> List[dict]:
    """Other live interactive claudes in the same folder: they make the conversation to resume ambiguous."""
    d = os.path.realpath(entry.get("dir") or "")
    return [
        a
        for a in terminal_sessions(status)
        if a is not entry and a.get("tmux") != entry.get("tmux") and os.path.realpath(a.get("dir") or "") == d
    ]


def live_terminal_in_folder(status: List[dict], folder: str) -> List[dict]:
    """Live interactive claude in this folder (any tmux): resuming on top of it is dangerous."""
    d = os.path.realpath(folder)
    return [a for a in terminal_sessions(status) if os.path.realpath(a.get("dir") or "") == d]


def account_for(dir_: str, account: Optional[str], bin_dir: Path, run: Runner = _run) -> Optional[str]:
    """Account of the session: that of the process, or that of the most recent conversation of the folder
    (claude-last --projects)."""
    if account and account != "?":
        return account
    try:
        r = run([str(bin_dir / "claude-last"), "--projects"])
        for p in json.loads(r.stdout):
            if os.path.realpath(p.get("cwd", "")) == os.path.realpath(dir_):
                return p.get("account")
    except (OSError, ValueError):
        pass
    return None


def convert(
    entry: dict,
    host: str,
    spawn: Callable[[str, str, str, str], str],
    tmux: Optional[Tmux] = None,
    bin_dir: Optional[Path] = None,
    others: Optional[List[dict]] = None,
    **close_kw,
) -> dict:
    """Converts an entry of terminal_sessions(). Returns {ok, label, how, msg}.

    `others`: other live interactive claudes in the same folder. With them, the most recent conversation
    of the folder may belong to another process that is still open, so the conversion is refused without
    closing anything.
    """
    tmux = tmux or Tmux()
    bin_dir = bin_dir or config.bin_dir()
    sess, dir_ = entry["tmux"], entry.get("dir") or ""
    base = os.path.basename(dir_.rstrip("/")) or "home"
    label = f"{base}@{host}"
    if others:
        names = ", ".join(a.get("tmux", "?") for a in others)
        return {
            "ok": False,
            "label": label,
            "how": "",
            "msg": f"there is another claude open in this folder (tmux {names}); close it or convert it "
            "first, otherwise there is no way to tell which conversation belongs to which",
        }
    acc = account_for(dir_, entry.get("account"), bin_dir)
    if not acc:
        return {
            "ok": False,
            "label": label,
            "how": "",
            "msg": f"could not find out the account of session {sess}; pass --account",
        }
    how = close_claude(tmux, sess, **close_kw)
    if how == "failed":
        return {
            "ok": False,
            "label": label,
            "how": how,
            "msg": f"the claude in {sess} did not close; nothing was opened",
        }
    if tmux.has_session(sess):
        tmux.kill_session(sess)  # the alias's shell was left over; the new session is orqs-<folder>
    try:
        msg = spawn(label, dir_, acc, "resume")
    except RuntimeError as e:
        return {"ok": False, "label": label, "how": how, "msg": str(e)}
    return {"ok": True, "label": label, "how": how, "msg": f"{msg}; account {acc}, folder {dir_}"}
