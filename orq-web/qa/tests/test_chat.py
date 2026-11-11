"""End-to-end conversation with the real claude, in the QA's throwaway folder (spends a little quota)."""

from conftest import PROJECT


def test_abre_projeto_e_responde(orq, agent):
    assert agent.endswith("@" + PROJECT.partition(":")[0]) and agent in orq.labels()
    assert "waiting" in orq.cstate(), orq.cstate()
    reply = orq.say("Answer only with the word pong, in lowercase, without punctuation.")
    orq.shot("chat-pong")
    assert "pong" in reply.lower(), f"reply: {reply!r} · state: {orq.cstate()}"
    kinds = [m["cls"] for m in orq.messages()]
    assert "me" in kinds and any(k.startswith("ai") for k in kinds), kinds
    assert not orq.problems(), orq.problems()


def test_interrompe_turno_longo(orq, agent):
    orq.say("Count from 1 to 300, one number per line, without summarising.", wait=False)
    # waits for claude to start writing (or calling a tool) and interrupts with Esc
    orq.wait_fn("() => document.querySelector('.chat.on .ai, .chat.on .chip') !== null", timeout=90000)
    orq.interrupt()
    st = orq.wait_idle(60)
    orq.shot("chat-interrompido")
    assert "waiting" in st or "error" in st, st
    assert any("interrupted" in m["text"] for m in orq.messages()) or "waiting" in st
