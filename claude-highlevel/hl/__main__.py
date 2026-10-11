"""Ponto de entrada do `python -m hl`.

O `hl` precisa de 3.11+ (o `tomllib` é da biblioteca padrão só a partir dessa
versão). Como o `python3` de muitas máquinas ainda é antigo, em vez de morrer com
um traceback este módulo procura um interpretador novo o bastante e se re-executa
nele — `python3 -m hl serve` simplesmente funciona.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

MINIMO = (3, 11)
_FLAG = "HL_REEXECUTADO"


def _serve(executavel):
    """O interpretador existe e é 3.11+?"""
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
    """Interpretadores a tentar, do mais específico do projeto ao mais genérico."""
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
    """Procura um Python 3.11+ e entrega o processo a ele. Só volta se falhar."""
    if os.environ.get(_FLAG):  # já tentamos uma vez; não entra em laço
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
    _reexecutar()  # se voltou daqui, não há 3.11+ nesta máquina
    sys.exit(
        f"hl exige Python 3.11+ (o `tomllib` é da biblioteca padrão desde o 3.11); "
        f"este é o {sys.version.split()[0]} em {sys.executable}, e não achei nenhum "
        f"interpretador mais novo no PATH.\n"
        f"Instale um 3.11+ ou crie o venv do projeto: python3.11 -m venv .venv"
    )

from .cli import main

sys.exit(main())
