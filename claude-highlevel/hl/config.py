"""Paths, child process environment and transcript location."""
from __future__ import annotations

import os
import re
from pathlib import Path

# Markers that Claude Code injects into the environment of the sessions it opens itself.
# If any of them leaks into the child, it considers itself a child session and
# TURNS OFF transcript recording — the JSONL never appears and the viewer goes blind.
# An explicit list on purpose: sweeping every CLAUDE_* would also take legitimate
# user configuration with it, such as CLAUDE_CONFIG_DIR.
SESSION_MARKERS = frozenset({
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_EFFORT",
    "CLAUDE_PID",
    "AI_AGENT",
})

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _socket_dirs() -> tuple[Path, ...]:
    """Where Claude Code puts the peer sockets, from the most reliable to the least.

    The directory moved between versions: today it is `$XDG_RUNTIME_DIR/cc-socks`,
    before it was `/tmp/cc-socks`. Hard-coding just one makes `wait_ready` wait forever on
    an empty directory, so we look in all the candidates. The most authoritative is the
    one of our own session, when we are running inside a Claude Code: the child
    inherits the same `XDG_RUNTIME_DIR` and ends up exactly there.
    """
    candidatos = []
    proprio = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
    if proprio:
        candidatos.append(Path(proprio).parent)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        candidatos.append(Path(runtime) / "cc-socks")
    candidatos.append(Path("/tmp/cc-socks"))

    vistos: set[str] = set()
    unicos = []
    for caminho in candidatos:
        if str(caminho) not in vistos:
            vistos.add(str(caminho))
            unicos.append(caminho)
    return tuple(unicos)


SOCKET_DIRS = _socket_dirs()
SOCKET_DIR = SOCKET_DIRS[0]      # the likely one; `socket_for_pid` covers the others


def child_env(cols: int = 110, rows: int = 40) -> dict[str, str]:
    """Clean environment for a child session."""
    env = {k: v for k, v in os.environ.items() if k not in SESSION_MARKERS}
    env.update({"TERM": "xterm-256color", "COLUMNS": str(cols), "LINES": str(rows)})
    return env


def resolve_cwd(cwd: str | os.PathLike) -> Path:
    """`/tmp/x` and `/private/tmp/x` are the same directory, but only one becomes the log key."""
    return Path(cwd).expanduser().resolve()


def project_key(cwd: str | os.PathLike) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(resolve_cwd(cwd)))


def project_log_dir(cwd: str | os.PathLike) -> Path:
    return PROJECTS_DIR / project_key(cwd)


def find_transcript(session_id: str, cwd: str | os.PathLike | None = None) -> Path | None:
    """Path of the session's JSONL, or None if it has not been created yet.

    Shortcut through the computed key; if that fails, looks for the UUID in every project —
    the id is unique, so this is immune to any naming rule we do not know about.
    """
    if cwd is not None:
        direct = project_log_dir(cwd) / f"{session_id}.jsonl"
        if direct.exists():
            return direct
    hits = list(PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def socket_for_pid(pid: int) -> Path:
    """The socket of this pid, looked up in all the candidate directories."""
    nome = f"{pid}.sock"
    for diretorio in SOCKET_DIRS:
        caminho = diretorio / nome
        if caminho.exists():
            return caminho
    return SOCKET_DIRS[0] / nome
