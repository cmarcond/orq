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


def test_professor_arquiteto_executor(orq, agent):
    """Três níveis: o professor delega ao arquiteto, que decide como fazer e instrui o executor; os marcos
    sobem pelo caminho inverso (executor → arquiteto → professor) e o professor explica. Leva uns minutos."""
    folder = Path(os.path.expanduser(PROJECT.partition(":")[2]))
    levels = orq.upgrade("professor-arquiteto-executor")
    orq.wait_fn("() => document.querySelectorAll('#levels .lcard').length === 3", timeout=60000)
    assert orq.levels() == ["professor", "arquiteto", "executor"], orq.levels()
    assert "professor" in orq.cstate(), orq.cstate()
    orq.shot("niveis3-subiram")
    orq.page.wait_for_timeout(1500)  # pausas: o vídeo é para uma pessoa conferir
    orq.say(TASK, wait=False)
    # o professor delega ao arquiteto (nunca ao executor); o arquiteto instrui o executor
    out = orq.wait_wire("professor", "hello", timeout=180)
    assert "arquiteto" in out["head"], out
    out = orq.wait_wire("arquiteto", "hello", timeout=240)
    assert "orq-qa" in out["head"] and "arquiteto" not in out["head"].split("→")[-1], out
    arq_before = len([w for w in orq.wires() if w["frm"] == "arquiteto"])
    orq.shot("niveis3-delegado")
    deadline = time.time() + 300
    while time.time() < deadline and not (folder / "hello.py").exists():
        orq.page.wait_for_timeout(2000)
    assert (folder / "hello.py").exists(), f"hello.py não apareceu; fios: {orq.wires()}"
    r = subprocess.run(
        [sys.executable, "hello.py", "QA"], cwd=folder, capture_output=True, text=True, timeout=30
    )
    assert "Olá, QA!" in r.stdout, r
    # o executor reporta CONCLUÍDO ao arquiteto, e o arquiteto repassa a leitura dele ao professor
    done = orq.wait_wire("executor", "conclu", delivered=True, timeout=300)
    assert done["delivered"], done
    deadline = time.time() + 240
    while time.time() < deadline:
        ups = [w for w in orq.wires() if w["frm"] == "arquiteto"]
        if len(ups) > arq_before and ups[-1]["delivered"]:
            break
        orq.page.wait_for_timeout(2000)
    else:
        raise AssertionError(f"o arquiteto não repassou o marco ao professor; fios: {orq.wires()}")
    orq.wait_idle(180)
    orq.shot("niveis3-concluido")
    assert any(c["cls"].startswith("ai") for c in orq.messages()), orq.messages()
    # o cartão do nível abre o chat cru dele: o arquiteto recebeu a tarefa do professor
    orq.page.click('#levels .lcard[data-l*="~arquiteto@"]')
    orq.wait_fn("() => document.getElementById('cstate').textContent.includes('arquiteto')", timeout=20000)
    orq.wait_fn("() => document.querySelector('.chat.on .inner')?.innerText.includes('hello')", timeout=20000)
    orq.shot("niveis3-chat-arquiteto")
    orq.page.wait_for_timeout(2000)
    orq.page.click('#levels .lcard[data-l*="~professor@"]')
    orq.wait_fn("() => document.getElementById('cstate').textContent.includes('professor')", timeout=20000)
    assert not orq.problems(), orq.problems()
    orq.downgrade()
    assert "professor" not in orq.cstate() and orq.levels() == []
    assert not any("~" in a["label"] for a in orq.agents()), orq.agents()
    orq.shot("niveis3-desfeitos")
