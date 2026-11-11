"""End to end: starts the real pipeline and asserts on the events it generated.

These tests cost time and tokens — each one opens two or three real Claude sessions and
waits for them to work. They are not unit tests; they are the proof that the whole system
does what it promises. Run with `python3 -m pytest tests/ -v -s`.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hl import pipelines                                     # noqa: E402
from hl.orchestrator import Registry, Run                    # noqa: E402

TAREFA = ("Create a hello.py that takes a name as a command-line argument and "
          "prints 'Hello, <name>!', with at least 3 automated tests, and run the "
          "tests at the end.")

LIMITE = 600          # seconds before giving up on a run
SILENCIO = 25         # seconds of everyone idle that count as "it is over"


def _esperar(run: Run, alvo: Path, limite: int = LIMITE) -> None:
    """Waits for the work to show up on disk and for the levels to settle."""
    fim = time.time() + limite
    ocioso_desde = None
    while time.time() < fim:
        time.sleep(3)
        if run.status == "erro":
            pytest.fail(f"run failed to start: {run.error}")
        estados = [s.activity() for s in run.sessions]
        if alvo.exists() and all(e == "ocioso" for e in estados):
            ocioso_desde = ocioso_desde or time.time()
            if time.time() - ocioso_desde >= SILENCIO:
                return
        else:
            ocioso_desde = None
    pytest.fail(f"the run did not finish in {limite}s (levels: {[s.activity() for s in run.sessions]})")


def _rodar(pipeline: str, cwd: Path, runs: Path) -> tuple[list[dict], Run]:
    registro = Registry(runs)
    run = registro.create(cwd=str(cwd), task=TAREFA, pipeline=pipeline, mode="auto")
    try:
        _esperar(run, cwd / "hello.py")
    finally:
        run.stop()
    linhas = (run.run_dir / "events.ndjson").read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in linhas if l.strip()], run


def _de(eventos: list[dict], kind: str, nivel: int | None = None) -> list[dict]:
    return [e for e in eventos
            if e["kind"] == kind and (nivel is None or e["level"] == nivel)]


def _digital(texto: str) -> str:
    return " ".join(str(texto).split())[:120]


# Every message shows up twice in the events: `wire_out` in the sender's transcript
# and `wire_in` in the receiver's. Comparing the two sets is much stronger than
# counting milestones — it detects a gap in the channel at any point of the chain.


def _sem_resposta(eventos: list[dict]) -> list[str]:
    """Messages that went out and the recipient never reacted — a real gap in the channel.

    Counting `wire_in` does not work as proof of delivery: when the recipient is in the middle
    of a turn, Claude Code **groups** the incoming peer messages, and three
    messages can become a single delivery. That is what showed up in a three-level run — the
    executor sent three milestones, the arquiteto recorded one `wire_in` and answered for all
    three. So what is checked here is weaker and truer: for every message
    sent, either the body appears as `wire_in` at the destination, or the destination produced some
    event after it — that is, it woke up.
    """
    nivel_de = {e["session"]: e["level"] for e in eventos if e.get("session")}
    chegadas = {_digital(e["payload"]["body"]) for e in _de(eventos, "wire_in")}
    orfas = []
    for envio in _de(eventos, "wire_out"):
        if _digital(envio["payload"]["message"]) in chegadas:
            continue
        destino = nivel_de.get(envio["payload"]["to"])
        acordou = any(e["level"] == destino and e["seq"] > envio["seq"]
                      for e in eventos if e["kind"] != "level")
        if not acordou:
            orfas.append(envio["payload"]["summary"] or envio["payload"]["message"][:60])
    return orfas


def _testes_passam(cwd: Path) -> bool:
    saida = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=cwd,
                           capture_output=True, text=True, timeout=120)
    return saida.returncode == 0 and "passed" in saida.stdout


# ----------------------------------------------------------------- two levels

@pytest.mark.slow
def test_dois_niveis(tmp_path: Path) -> None:
    trabalho = tmp_path / "trabalho"
    trabalho.mkdir()
    eventos, run = _rodar("professor-executor", trabalho, tmp_path / "runs")

    assert len(_de(eventos, "wire_out", 1)) >= 1, "the top level delegated nothing"
    assert len(_de(eventos, "wire_in", 1)) >= 1, "nothing came back from the bottom level"
    assert not _sem_resposta(eventos), "a message left a level and the destination never reacted"
    assert len(_de(eventos, "narration", 1)) >= 2, "the professor narrated too little"

    # How many milestones the executor sends is its own judgement, not a guarantee of the
    # system: in one of the runs it did the whole task in one go and reported a single time, with
    # the tests already green. Counting milestones would make the test hostage to the model's mood,
    # so what is asserted above is message delivery, not quantity.

    # The stage belongs to level 1: what the lower ones say exists in the events, but does not reach the screen.
    assert _de(eventos, "narration", 2), "the executor said nothing in its own transcript"

    assert (trabalho / "hello.py").exists()
    assert _testes_passam(trabalho), "the tests written by the executor do not pass"
    assert all(not s.alive for s in run.sessions), "a session was left alive after the stop"


# --------------------------------------------------------------- three levels

@pytest.mark.slow
def test_tres_niveis_sem_tocar_em_python(tmp_path: Path) -> None:
    """The same task with one more level — the only difference is which .toml was read."""
    trabalho = tmp_path / "trabalho"
    trabalho.mkdir()
    eventos, run = _rodar("professor-arquiteto-executor", trabalho, tmp_path / "runs")

    assert len(run.sessions) == 3
    assert len(_de(eventos, "wire_out", 1)) >= 1, "the professor did not delegate to the arquiteto"
    assert len(_de(eventos, "wire_out", 2)) >= 1, "the arquiteto did not delegate to the executor"
    assert len(_de(eventos, "wire_in", 2)) >= 1, "the arquiteto received no milestone from the executor"
    assert not _sem_resposta(eventos), "the three-level chain lost a message"
    assert len(_de(eventos, "narration", 1)) >= 2, "the professor narrated too little"

    assert (trabalho / "hello.py").exists()
    assert _testes_passam(trabalho)


# ------------------------------------------------------ runs that do not mix up

def test_sufixo_isola_runs_simultaneos() -> None:
    """Two runs at the same time must not compete for the name `executor`."""
    receita = pipelines.pegar("professor-executor")
    a = {lv.name for lv in receita.montar(tag="-aaaa")}
    b = {lv.name for lv in receita.montar(tag="-bbbb")}
    assert not (a & b), f"names collided between runs: {a & b}"
    # and each level only knows the neighbours of its own run
    for lv in receita.montar(tag="-aaaa"):
        assert "-bbbb" not in lv.system_prompt


def test_receitas_carregam() -> None:
    catalogo = pipelines.catalogo()
    assert "professor-executor" in catalogo
    assert [n["nome"] for n in catalogo["professor-arquiteto-executor"].niveis] == \
        ["professor", "arquiteto", "executor"]
    # every role has its neighbours resolved, with no placeholder left over
    for receita in catalogo.values():
        for lv in receita.montar(tag="-tttt"):
            assert "{acima}" not in lv.system_prompt
            assert "{abaixo}" not in lv.system_prompt
