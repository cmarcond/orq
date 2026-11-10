"""Caminhos, ambiente do processo filho e localização do transcript."""
from __future__ import annotations

import os
import re
from pathlib import Path

# Marcadores que o Claude Code injeta no ambiente das sessões que ele mesmo abre.
# Se qualquer um deles vazar para o filho, ele se considera sessão-filha e
# DESLIGA a gravação do transcript — o JSONL nunca aparece e o viewer fica cego.
# Lista explícita de propósito: varrer todo CLAUDE_* levaria junto configuração
# legítima do usuário, como CLAUDE_CONFIG_DIR.
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
    """Onde o Claude Code deposita os sockets de peer, do mais confiável ao menos.

    O diretório mudou de lugar entre versões: hoje é `$XDG_RUNTIME_DIR/cc-socks`,
    antes era `/tmp/cc-socks`. Cravar um só faz `wait_ready` esperar para sempre num
    diretório vazio, então procuramos em todos os candidatos. O mais autoritativo é o
    da nossa própria sessão, quando estamos rodando dentro de um Claude Code: o filho
    herda o mesmo `XDG_RUNTIME_DIR` e vai parar exatamente ali.
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
SOCKET_DIR = SOCKET_DIRS[0]      # o provável; `socket_for_pid` cobre os outros


def child_env(cols: int = 110, rows: int = 40) -> dict[str, str]:
    """Ambiente limpo para uma sessão filha."""
    env = {k: v for k, v in os.environ.items() if k not in SESSION_MARKERS}
    env.update({"TERM": "xterm-256color", "COLUMNS": str(cols), "LINES": str(rows)})
    return env


def resolve_cwd(cwd: str | os.PathLike) -> Path:
    """`/tmp/x` e `/private/tmp/x` são o mesmo diretório, mas só um vira chave de log."""
    return Path(cwd).expanduser().resolve()


def project_key(cwd: str | os.PathLike) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(resolve_cwd(cwd)))


def project_log_dir(cwd: str | os.PathLike) -> Path:
    return PROJECTS_DIR / project_key(cwd)


def find_transcript(session_id: str, cwd: str | os.PathLike | None = None) -> Path | None:
    """Caminho do JSONL da sessão, ou None se ainda não foi criado.

    Atalho pela chave calculada; se falhar, procura pelo UUID em todos os projetos —
    o id é único, então isso é imune a qualquer regra de nome que a gente não conheça.
    """
    if cwd is not None:
        direct = project_log_dir(cwd) / f"{session_id}.jsonl"
        if direct.exists():
            return direct
    hits = list(PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def socket_for_pid(pid: int) -> Path:
    """O socket desse pid, procurado em todos os diretórios candidatos."""
    nome = f"{pid}.sock"
    for diretorio in SOCKET_DIRS:
        caminho = diretorio / nome
        if caminho.exists():
            return caminho
    return SOCKET_DIRS[0] / nome
