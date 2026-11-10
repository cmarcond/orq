"""Ponta a ponta: sobe o pipeline de verdade e afirma sobre os eventos que ele gerou.

Estes testes gastam tempo e tokens — cada um abre duas ou três sessões Claude reais e
espera elas trabalharem. Não são testes de unidade; são a prova de que o sistema inteiro
faz o que promete. Rode com `python3 -m pytest tests/ -v -s`.
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

TAREFA = ("Crie um hello.py que recebe um nome como argumento de linha de comando e "
          "imprime 'Olá, <nome>!', com pelo menos 3 testes automatizados, e rode os "
          "testes no final.")

LIMITE = 600          # segundos até desistir de um run
SILENCIO = 25         # segundos de todos ociosos que contam como "acabou"


def _esperar(run: Run, alvo: Path, limite: int = LIMITE) -> None:
    """Espera o trabalho aparecer no disco e os níveis sossegarem."""
    fim = time.time() + limite
    ocioso_desde = None
    while time.time() < fim:
        time.sleep(3)
        if run.status == "erro":
            pytest.fail(f"run falhou ao subir: {run.error}")
        estados = [s.activity() for s in run.sessions]
        if alvo.exists() and all(e == "ocioso" for e in estados):
            ocioso_desde = ocioso_desde or time.time()
            if time.time() - ocioso_desde >= SILENCIO:
                return
        else:
            ocioso_desde = None
    pytest.fail(f"o run não terminou em {limite}s (níveis: {[s.activity() for s in run.sessions]})")


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


# Toda mensagem aparece duas vezes nos eventos: `wire_out` no transcript de quem mandou
# e `wire_in` no de quem recebeu. Comparar os dois conjuntos é bem mais forte do que
# contar marcos — detecta furo no canal em qualquer ponto da corrente.


def _sem_resposta(eventos: list[dict]) -> list[str]:
    """Mensagens que saíram e o destinatário nunca reagiu — furo de verdade no canal.

    Contar `wire_in` não serve como prova de entrega: quando o destinatário está no meio
    de um turno, o Claude Code **agrupa** as mensagens de peer que chegam, e três
    mensagens podem virar uma entrega só. Foi o que apareceu num run de três níveis — o
    executor mandou três marcos, o arquiteto registrou um `wire_in` e respondeu pelos
    três. Então o que se verifica aqui é mais fraco e mais verdadeiro: para toda mensagem
    enviada, ou o corpo aparece como `wire_in` no destino, ou o destino produziu algum
    evento depois dela — ou seja, acordou.
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


# ---------------------------------------------------------------- dois níveis

@pytest.mark.slow
def test_dois_niveis(tmp_path: Path) -> None:
    trabalho = tmp_path / "trabalho"
    trabalho.mkdir()
    eventos, run = _rodar("professor-executor", trabalho, tmp_path / "runs")

    assert len(_de(eventos, "wire_out", 1)) >= 1, "o nível de cima não delegou nada"
    assert len(_de(eventos, "wire_in", 1)) >= 1, "nada voltou do nível de baixo"
    assert not _sem_resposta(eventos), "mensagem saiu de um nível e o destino nunca reagiu"
    assert len(_de(eventos, "narration", 1)) >= 2, "o professor narrou de menos"

    # Quantos marcos o executor manda é juízo dele, não garantia do sistema: numa das
    # execuções ele fez a tarefa inteira de uma vez e reportou uma única vez, com os
    # testes já verdes. Contar marcos transformaria o teste em refém do humor do modelo,
    # então o que se afirma acima é entrega de mensagem, não quantidade.

    # O palco é do nível 1: a fala dos de baixo existe nos eventos, mas não sobe à tela.
    assert _de(eventos, "narration", 2), "o executor não falou nada no próprio transcript"

    assert (trabalho / "hello.py").exists()
    assert _testes_passam(trabalho), "os testes escritos pelo executor não passam"
    assert all(not s.alive for s in run.sessions), "sobrou sessão viva depois do stop"


# ---------------------------------------------------------------- três níveis

@pytest.mark.slow
def test_tres_niveis_sem_tocar_em_python(tmp_path: Path) -> None:
    """A mesma tarefa com um nível a mais — a única diferença é qual .toml foi lido."""
    trabalho = tmp_path / "trabalho"
    trabalho.mkdir()
    eventos, run = _rodar("professor-arquiteto-executor", trabalho, tmp_path / "runs")

    assert len(run.sessions) == 3
    assert len(_de(eventos, "wire_out", 1)) >= 1, "o professor não delegou ao arquiteto"
    assert len(_de(eventos, "wire_out", 2)) >= 1, "o arquiteto não delegou ao executor"
    assert len(_de(eventos, "wire_in", 2)) >= 1, "o arquiteto não recebeu marco do executor"
    assert not _sem_resposta(eventos), "a corrente de três níveis perdeu mensagem"
    assert len(_de(eventos, "narration", 1)) >= 2, "o professor narrou de menos"

    assert (trabalho / "hello.py").exists()
    assert _testes_passam(trabalho)


# ------------------------------------------------------- runs sem se confundir

def test_sufixo_isola_runs_simultaneos() -> None:
    """Dois runs ao mesmo tempo não podem disputar o nome `executor`."""
    receita = pipelines.pegar("professor-executor")
    a = {lv.name for lv in receita.montar(tag="-aaaa")}
    b = {lv.name for lv in receita.montar(tag="-bbbb")}
    assert not (a & b), f"nomes colidiram entre runs: {a & b}"
    # e cada nível só conhece os vizinhos do próprio run
    for lv in receita.montar(tag="-aaaa"):
        assert "-bbbb" not in lv.system_prompt


def test_receitas_carregam() -> None:
    catalogo = pipelines.catalogo()
    assert "professor-executor" in catalogo
    assert [n["nome"] for n in catalogo["professor-arquiteto-executor"].niveis] == \
        ["professor", "arquiteto", "executor"]
    # todo papel tem os vizinhos resolvidos, sem placeholder sobrando
    for receita in catalogo.values():
        for lv in receita.montar(tag="-tttt"):
            assert "{acima}" not in lv.system_prompt
            assert "{abaixo}" not in lv.system_prompt
