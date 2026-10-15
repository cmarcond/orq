"""Níveis (professor → executor) com claude real: o hello world do HighLevel, pedido ao professor.

O professor nunca executa: delega pelo SendMessage (fio de saída no painel), o executor cria o hello.py na
pasta do QA e reporta marcos (fio de entrada), e o professor explica. Leva uns minutos e gasta cota."""

import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import PROJECT

TASK = (
    "Peça ao executor: crie um hello.py que recebe um nome por argumento e imprime 'Olá, <nome>!', "
    "com 3 testes."
)


def test_professor_delega_hello_world(orq, agent):
    folder = Path(os.path.expanduser(PROJECT.partition(":")[2]))  # a fixture a recriou vazia

    levels = orq.upgrade("professor-executor")
    assert levels == ["professor", "executor"], levels
    assert "professor" in orq.cstate(), orq.cstate()
    orq.shot("niveis-subiram")

    orq.say(TASK, wait=False)
    out = orq.wait_wire("professor", "hello", timeout=180)  # delegou pelo SendMessage
    assert "orq-qa" in out["head"], out  # destinatário pelo nome de sessão do executor

    # o executor trabalha sozinho: o arquivo aparece na pasta e os marcos voltam como fios dele
    deadline = time.time() + 300
    while time.time() < deadline and not (folder / "hello.py").exists():
        orq.page.wait_for_timeout(2000)
    assert (folder / "hello.py").exists(), f"hello.py não apareceu; fios: {orq.wires()}"
    r = subprocess.run(
        [sys.executable, "hello.py", "QA"], cwd=folder, capture_output=True, text=True, timeout=30
    )
    assert "Olá, QA!" in r.stdout, r

    done = orq.wait_wire("executor", "conclu", delivered=True, timeout=300)  # marco CONCLUÍDO, ✓✓
    assert done["delivered"], done
    orq.wait_idle(120)
    orq.shot("niveis-hello-world")
    assert any(c["cls"].startswith("ai") for c in orq.messages()), orq.messages()
    assert not orq.problems(), orq.problems()

    orq.downgrade()
    assert "professor" not in orq.cstate() and orq.levels() == []
    orq.shot("niveis-desfeitos")
