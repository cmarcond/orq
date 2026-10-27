"""Conversa de ponta a ponta com o claude real, na pasta descartável do QA (gasta um pouco de cota)."""

from conftest import PROJECT



def test_abre_projeto_e_responde(orq, agent):
    assert agent.endswith("@" + PROJECT.partition(":")[0]) and agent in orq.labels()
    assert "esperando" in orq.cstate(), orq.cstate()
    reply = orq.say("Responda apenas com a palavra pong, em minúsculas, sem pontuação.")
    orq.shot("chat-pong")
    assert "pong" in reply.lower(), f"resposta: {reply!r} · estado: {orq.cstate()}"
    kinds = [m["cls"] for m in orq.messages()]
    assert "me" in kinds and any(k.startswith("ai") for k in kinds), kinds
    assert not orq.problems(), orq.problems()


def test_interrompe_turno_longo(orq, agent):
    orq.say("Conte de 1 até 300, um número por linha, sem resumir.", wait=False)
    # espera o claude começar a escrever (ou a chamar ferramenta) e interrompe pelo Esc
    orq.wait_fn("() => document.querySelector('.chat.on .ai, .chat.on .chip') !== null", timeout=90000)
    orq.interrupt()
    st = orq.wait_idle(60)
    orq.shot("chat-interrompido")
    assert "esperando" in st or "erro" in st, st
    assert any("interromp" in m["text"] for m in orq.messages()) or "esperando" in st
