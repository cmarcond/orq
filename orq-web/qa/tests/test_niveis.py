"""Levels (teacher → executor) with the real claude: the hello world of HighLevel, asked of the teacher.

The teacher never executes: it delegates through SendMessage (outgoing wire in the panel), the executor
creates hello.py in the QA folder and reports milestones (incoming wire), and the teacher explains. Takes a
few minutes and spends quota."""

import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import PROJECT

TASK = (
    "Ask the executor: create a hello.py that receives a name as an argument and prints 'Hello, <name>!', "
    "with 3 tests."
)


def test_professor_delega_hello_world(orq, agent):
    folder = Path(os.path.expanduser(PROJECT.partition(":")[2]))  # the fixture recreated it empty

    levels = orq.upgrade("professor-executor")
    assert levels == ["professor", "executor"], levels
    assert "professor" in orq.cstate(), orq.cstate()
    orq.shot("niveis-subiram")

    orq.say(TASK, wait=False)
    out = orq.wait_wire("professor", "hello", timeout=180)  # delegated through SendMessage
    assert "orq-qa" in out["head"], out  # recipient by the executor's session name

    # the executor works on its own: the file appears in the folder and the milestones come back as its wires
    deadline = time.time() + 300
    while time.time() < deadline and not (folder / "hello.py").exists():
        orq.page.wait_for_timeout(2000)
    assert (folder / "hello.py").exists(), f"hello.py did not appear; wires: {orq.wires()}"
    r = subprocess.run(
        [sys.executable, "hello.py", "QA"], cwd=folder, capture_output=True, text=True, timeout=30
    )
    assert "Hello, QA!" in r.stdout, r

    done = orq.wait_wire("executor", "done", delivered=True, timeout=300)  # DONE milestone, ✓✓
    assert done["delivered"], done
    orq.wait_idle(120)
    orq.shot("niveis-hello-world")
    assert any(c["cls"].startswith("ai") for c in orq.messages()), orq.messages()
    assert not orq.problems(), orq.problems()

    orq.downgrade()
    assert "professor" not in orq.cstate() and orq.levels() == []
    orq.shot("niveis-desfeitos")


def test_professor_arquiteto_executor(orq, agent):
    """Three levels: the teacher delegates to the architect, which decides how to do it and instructs the
    executor; the milestones go up the reverse path (executor → architect → teacher) and the teacher explains.
    Takes a few minutes."""
    folder = Path(os.path.expanduser(PROJECT.partition(":")[2]))
    orq.upgrade("professor-arquiteto-executor")
    orq.wait_fn("() => document.querySelectorAll('#levels .lcard').length === 3", timeout=60000)
    assert orq.levels() == ["professor", "arquiteto", "executor"], orq.levels()
    assert "professor" in orq.cstate(), orq.cstate()
    orq.shot("niveis3-subiram")
    orq.page.wait_for_timeout(1500)  # pauses: the video is for a person to check
    orq.say(TASK, wait=False)
    # the teacher delegates to the architect (never to the executor); the architect instructs the executor
    out = orq.wait_wire("professor", "hello", timeout=180)
    assert "arquiteto" in out["head"], out
    out = orq.wait_wire("arquiteto", "hello", timeout=240)
    assert "orq-qa" in out["head"] and "arquiteto" not in out["head"].split("→")[-1], out
    arq_before = len([w for w in orq.wires() if w["frm"] == "arquiteto"])
    orq.shot("niveis3-delegado")
    deadline = time.time() + 300
    while time.time() < deadline and not (folder / "hello.py").exists():
        orq.page.wait_for_timeout(2000)
    assert (folder / "hello.py").exists(), f"hello.py did not appear; wires: {orq.wires()}"
    r = subprocess.run(
        [sys.executable, "hello.py", "QA"], cwd=folder, capture_output=True, text=True, timeout=30
    )
    assert "Hello, QA!" in r.stdout, r
    # the executor reports DONE to the architect, and the architect passes its reading on to the teacher
    done = orq.wait_wire("executor", "done", delivered=True, timeout=300)
    assert done["delivered"], done
    deadline = time.time() + 240
    while time.time() < deadline:
        ups = [w for w in orq.wires() if w["frm"] == "arquiteto"]
        if len(ups) > arq_before and ups[-1]["delivered"]:
            break
        orq.page.wait_for_timeout(2000)
    else:
        raise AssertionError(f"the architect did not pass the milestone to the teacher; wires: {orq.wires()}")
    orq.wait_idle(180)
    orq.shot("niveis3-concluido")
    assert any(c["cls"].startswith("ai") for c in orq.messages()), orq.messages()
    # the level's card opens its raw chat: the architect received the task from the teacher
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
