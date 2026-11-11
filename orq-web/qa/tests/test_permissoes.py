"""Permissions and questions through the page, with the real claude. `test_pergunta_do_agente`: the
`AskUserQuestion` becomes a form in the chat; the QA answers by choosing an option, then through "Other…", and
finally cancels (three short turns, in the normal mode of accounts.json). `test_modo_manual_negar_e_permitir`:
a set of a single agent with `permissao: manual` (everything asks); claude asks to write a file, the QA denies
(nothing is written), it asks again and the QA allows (two turns)."""

import os
import time
from pathlib import Path

import pytest
from conftest import PROJECT
from orqqa import local_host_name

FRUTAS = ("apple", "banana", "grape")


def ask_card(orq):
    """The pending form: {pergunta, opcoes} (question, options)."""
    return orq.page.evaluate(
        """() => {
      const p = document.querySelector('.chat.on .perm.ask:not(.done)');
      if (!p) return null;
      return { pergunta: p.querySelector('.qt').textContent,
               opcoes: [...p.querySelectorAll('.opts .opt b')].map((b) => b.textContent) };
    }"""
    )


def wait_ask(orq, timeout: float = 180):
    orq.page.wait_for_selector(".chat.on .perm.ask:not(.done)", timeout=timeout * 1000)
    return ask_card(orq)


def test_pergunta_do_agente(orq, agent):
    # 1) a question with options: the QA picks one
    orq.say(
        "Use the AskUserQuestion tool to ask me which fruit I prefer, with exactly these three "
        f"options: {', '.join(FRUTAS)} (a single answer). Then answer only: you chose <fruit>.",
        wait=False,
    )
    card = wait_ask(orq)
    assert "fruit" in card["pergunta"].lower(), card
    assert all(any(f in o.lower() for o in card["opcoes"]) for f in FRUTAS), card
    assert "question" in orq.cstate() or "permission" in orq.cstate(), orq.cstate()
    orq.shot("pergunta-formulario")
    orq.page.wait_for_timeout(2000)  # pauses: the video is for a person to check
    orq.page.click('.chat.on .perm.ask:not(.done) .opt:has(b:text-is("grape")) input')
    orq.page.click(".chat.on .perm.ask:not(.done) .btns .ok")
    orq.page.wait_for_selector(".chat.on .perm.ask.done")
    assert "answered" in orq.page.inner_text(".chat.on .perm.ask.done .btns")
    st = orq.wait_idle(120)
    assert "error" not in st, st
    assert "grape" in orq.last_ai().lower(), orq.last_ai()
    orq.shot("pergunta-respondida")
    # 2) "Other…": free text instead of the options
    orq.say(
        "Use AskUserQuestion again: ask which color I prefer, with the options blue and green. "
        "Then answer only: you chose <color>.",
        wait=False,
    )
    card = wait_ask(orq)
    assert "color" in card["pergunta"].lower(), card
    orq.page.fill(".chat.on .perm.ask:not(.done) .otxt", "purple")  # focusing the field ticks "Other…"
    orq.page.wait_for_timeout(1500)
    orq.shot("pergunta-outro")
    orq.page.click(".chat.on .perm.ask:not(.done) .btns .ok")
    st = orq.wait_idle(120)
    assert "error" not in st, st
    assert "purple" in orq.last_ai().lower(), orq.last_ai()
    # 3) cancel: claude is left without an answer and says so
    orq.say(
        "Use AskUserQuestion once more: ask whether I want to continue, with the options yes and no. "
        "If the question is canceled or denied, answer only: question canceled.",
        wait=False,
    )
    wait_ask(orq)
    orq.page.click(".chat.on .perm.ask:not(.done) .btns .no")
    orq.wait_fn(
        "() => [...document.querySelectorAll('.chat.on .perm.ask.done .btns')]"
        ".some((e) => e.textContent.includes('canceled'))"
    )
    st = orq.wait_idle(120)
    assert "error" not in st, st
    assert "cancel" in orq.last_ai().lower(), orq.last_ai()
    orq.shot("pergunta-cancelada")
    assert not orq.problems(), orq.problems()


def deny_all(orq, timeout: float = 180) -> str:
    """Waits for the turn to end denying everything claude asks on the way. Returns the final state."""
    end = time.time() + timeout
    while True:
        st = orq.wait_idle(max(5.0, end - time.time()))
        if "permission" not in st:
            return st
        orq.permission(False)
        orq.page.wait_for_timeout(500)


def test_modo_manual_negar_e_permitir(orq):
    host, _, path = PROJECT.partition(":")
    if host != local_host_name():
        pytest.skip(
            f"the test checks the file on this machine ({local_host_name()}), and ORQ_QA_PROJECT is on {host}"
        )
    name = path.rstrip("/").split("/")[-1]
    label = f"{name}@{host}"
    folder = Path(os.path.expanduser(path))
    sid = "qa-manual"
    orq.purge(PROJECT)
    orq.api(f"/api/conjuntos/{sid}/delete", {})
    try:
        _manual(orq, host, path, label, folder, sid)
    finally:
        orq.api(f"/api/conjuntos/{sid}/delete", {})
        orq.purge(PROJECT)


def _manual(orq, host, path, label, folder, sid):
    assert (
        orq.save_set(
            {
                "nome": "QA manual",
                "descricao": "an agent that asks everything",
                "permissao": "manual",
                "agentes": [{"maquina": host, "pasta": path, "modo": "new"}],
            }
        )
        == sid
    )
    job = orq.open_set(sid)
    assert job["erros"] == {} and job["feitos"] == [label], job
    orq.show(label)
    orq.wait_fn("() => document.getElementById('cstate').textContent.includes('waiting')", timeout=60000)
    assert next(a for a in orq.agents() if a["label"] == label).get("permission") == "manual", orq.agents()
    # 1) deny: claude asks to write, the page denies, nothing is written
    orq.say(
        "Create the file oi.txt in this folder with the text 'oi'. If the permission is denied, do not try "
        "another way: answer only 'permission denied'.",
        wait=False,
    )
    orq.page.wait_for_selector(".chat.on .perm:not(.term):not(.ask):not(.done)", timeout=180000)
    card = orq.page.inner_text(".chat.on .perm:not(.term):not(.ask):not(.done)")
    assert card.startswith("Permission:") and "oi.txt" in card, card
    assert "permission" in orq.cstate(), orq.cstate()
    orq.shot("manual-pede")
    orq.page.wait_for_timeout(2000)
    orq.permission(False)
    orq.page.wait_for_selector(".chat.on .perm.done")
    assert "denied" in orq.page.inner_text(".chat.on .perm.done .btns")
    st = deny_all(orq)
    assert "error" not in st, st
    assert not (folder / "oi.txt").exists(), "denied: nothing written"
    assert "denied" in orq.last_ai().lower(), orq.last_ai()
    orq.shot("manual-negado")
    orq.page.wait_for_timeout(1500)
    # 2) allow: the same request, now accepted
    orq.say("Now try again to create oi.txt with the text 'oi'. Answer only 'created'.", wait=False)
    orq.page.wait_for_selector(".chat.on .perm:not(.term):not(.ask):not(.done)", timeout=180000)
    orq.shot("manual-pede-de-novo")
    orq.page.wait_for_timeout(1500)
    st = orq.answer_permissions(180)
    assert "error" not in st, st
    assert orq.page.locator(".chat.on .perm.done").count() >= 2
    last = orq.page.evaluate("[...document.querySelectorAll('.chat.on .perm.done .btns')].pop().textContent")
    assert "allowed" in last, last
    assert (folder / "oi.txt").read_text().strip() == "oi"
    orq.shot("manual-permitido")
    orq.page.wait_for_timeout(1500)
    assert not orq.problems(), orq.problems()
