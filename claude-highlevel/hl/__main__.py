"""Entry point for `python -m hl`.

`hl` needs 3.11+ (`tomllib` is in the standard library only from that version
on). Since the `python3` on many machines is still old, instead of dying with a
traceback this module looks for an interpreter that is new enough and re-executes
itself in it — `python3 -m hl serve` simply works.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

MINIMO = (3, 11)
_FLAG = "HL_REEXECUTADO"


def _serve(executavel):
    """Does the interpreter exist and is it 3.11+?"""
    try:
        saida = subprocess.run(
            [executavel, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if saida.returncode != 0:
        return False
    try:
        versao = tuple(int(p) for p in saida.stdout.strip().split("."))
    except ValueError:
        return False
    return versao >= MINIMO


def _candidatos(raiz):
    """Interpreters to try, from the most project-specific to the most generic."""
    yield raiz / ".venv" / "bin" / "python"
    for menor in range(20, MINIMO[1] - 1, -1):
        achado = shutil.which(f"python3.{menor}")
        if achado:
            yield Path(achado)
    for nome in ("python3", "python"):
        achado = shutil.which(nome)
        if achado:
            yield Path(achado)


def _reexecutar():
    """Looks for a Python 3.11+ and hands the process over to it. Returns only on failure."""
    if os.environ.get(_FLAG):  # we already tried once; do not loop
        return
    raiz = Path(__file__).resolve().parent.parent
    atual = os.path.realpath(sys.executable)
    for candidato in _candidatos(raiz):
        if not candidato.exists() or os.path.realpath(candidato) == atual:
            continue
        if not _serve(str(candidato)):
            continue
        ambiente = dict(os.environ, **{_FLAG: "1"})
        caminho = ambiente.get("PYTHONPATH", "")
        ambiente["PYTHONPATH"] = f"{raiz}{os.pathsep}{caminho}" if caminho else str(raiz)
        os.execve(str(candidato), [str(candidato), "-m", "hl", *sys.argv[1:]], ambiente)
    return


if sys.version_info < MINIMO:
    _reexecutar()  # if it returned, there is no 3.11+ on this machine
    sys.exit(
        f"hl requires Python 3.11+ (`tomllib` has been in the standard library since 3.11); "
        f"this is {sys.version.split()[0]} at {sys.executable}, and I found no newer "
        f"interpreter on the PATH.\n"
        f"Install a 3.11+ or create the project venv: python3.11 -m venv .venv"
    )

from .cli import main

sys.exit(main())
