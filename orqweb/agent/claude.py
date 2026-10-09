"""Como o supervisor invoca o `claude`: comando, binário e sessão a retomar."""

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional


def find_claude(bin_dir: Path) -> str:
    return shutil.which("claude") or str(bin_dir / "claude")


def build_command(claude: str, permission_mode: str, resume: Optional[str] = None) -> List[str]:
    """`claude -p` em stream-json nos dois sentidos, com permissões pelo stdio (protocolo do Agent SDK).

    --include-partial-messages: texto palavra a palavra; --replay-user-messages: o claude ecoa as
    mensagens que recebe, assim tudo que a página mostra passou pelo mesmo fluxo e fica persistido.
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
    return cmd


def pick_session(bin_dir: Path, account: str, folder: Path) -> Optional[str]:
    """Conversa mais recente da pasta, copiada para a conta se for de outra (claude-last --pick --as).

    None quando não há conversa ou o utilitário não está disponível.
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
