"""Vínculo entre dois agentes da página (🔗): um pede ao outro pelo SendMessage e recebe a resposta.

Os dois ficam no laptop (vínculo local, sem túnel); o caminho por ssh é o mesmo com `orq-agent link`
por baixo, testado à parte. Gasta um pouco de cota: duas rodadas curtas do claude real.
"""

import time


def test_vincula_pede_e_recebe(orq, agent, agent2):
    assert orq.links() == []
    linked = orq.link(agent2)
    assert linked == [agent2], linked
    orq.shot("vinculo-criado")
    # a nota chega aos dois como chip de máquina ([orq] …), não como fala sua
    orq.page.wait_for_function(
        "() => [...document.querySelectorAll('.chat.on .chip.sysm')]"
        ".some((e) => e.innerText.includes('Vínculo criado'))",
        timeout=30000,
    )
    other = agent2.split("@")[0] + "-laptop"  # nome de sessão do outro (rótulo saneado)
    orq.say(
        f"Pergunte ao agente {other}, pelo SendMessage, qual é a pasta de trabalho dele (peça que responda "
        "pelo SendMessage a você). Quando a resposta chegar, me diga só o caminho que ele respondeu.",
        wait=False,
    )
    out = orq.wait_wire("executor", "", timeout=180)  # o pedido saiu pelo fio
    assert other in out["head"], out
    deadline = time.time() + 240
    while time.time() < deadline and not any(w["delivered"] for w in orq.wires()):
        orq.page.wait_for_timeout(2000)
    st = orq.answer_permissions(240)
    assert "erro" not in st, st
    orq.wait_idle(120)
    orq.shot("vinculo-resposta")
    assert "orq-qa2" in orq.last_ai(), orq.last_ai()
    assert not orq.problems(), orq.problems()
    orq.unlink(agent2)
    assert orq.links() == []
    orq.shot("vinculo-desfeito")
