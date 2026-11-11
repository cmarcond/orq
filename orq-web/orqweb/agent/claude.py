"""How the supervisor invokes `claude`: command, binary and session to resume."""

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional


def find_claude(bin_dir: Path) -> str:
    return shutil.which("claude") or str(bin_dir / "claude")


def build_command(
    claude: str,
    permission_mode: str,
    resume: Optional[str] = None,
    name: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> List[str]:
    """`claude -p` with stream-json in both directions, with permissions over stdio (Agent SDK protocol).

    --include-partial-messages: text word by word; --replay-user-messages: claude echoes the
    messages it receives, so everything the page shows went through the same stream and is persisted.
    `name` (-n) is the name by which other sessions find this one in ListAgents/SendMessage; `system_prompt`
    (--append-system-prompt) is the role of a level (teacher, architect…).
    """
    cmd = [
        claude,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--replay-user-messages",
        "--permission-prompt-tool",
        "stdio",
        "--permission-mode",
        permission_mode,
    ]
    if resume:
        cmd += ["--resume", resume]
    if name:
        cmd += ["-n", name]
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    return cmd


def pick_session(bin_dir: Path, account: str, folder: Path) -> Optional[str]:
    """Most recent conversation of the folder, copied to the account if it belongs to another one
    (claude-last --pick --as).

    None when there is no conversation or the utility is not available.
    """
    try:
        r = subprocess.run(
            [str(bin_dir / "claude-last"), "--pick", f"--as={account}", str(folder)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    parts = r.stdout.split()
    return parts[1] if len(parts) >= 2 else None
