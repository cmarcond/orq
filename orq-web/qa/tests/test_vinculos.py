"""Link between two agents of the page (🔗): one asks the other through SendMessage and receives the reply.

Both are on the server's machine (local link, no tunnel); the ssh path is the same (`orq-agent link`
underneath, tested separately. Spends a little quota: two short rounds of the real claude.
"""

import time


def test_vincula_pede_e_recebe(orq, agent, agent2):
    assert orq.links() == []
    linked = orq.link(agent2)
    assert linked == [agent2], linked
    orq.shot("vinculo-criado")
    # the note reaches both as a machine chip ([orq] …), not as something you said
    orq.wait_fn(
        "() => [...document.querySelectorAll('.chat.on .chip.sysm')]"
        ".some((e) => e.innerText.includes('Link created'))",
        timeout=30000,
    )
    other = agent2.replace("@", "-")  # session name of the other one (sanitised label)
    orq.say(
        f"Ask the agent {other}, through SendMessage, what its working folder is (ask it to reply "
        "through SendMessage to you). When the reply arrives, tell me only the path it answered.",
        wait=False,
    )
    out = orq.wait_wire("executor", "", timeout=180)  # the request went out through the wire
    assert other in out["head"], out
    deadline = time.time() + 240
    while time.time() < deadline and not any(w["delivered"] for w in orq.wires()):
        orq.page.wait_for_timeout(2000)
    st = orq.answer_permissions(240)
    assert "error" not in st, st
    orq.wait_idle(120)
    orq.shot("vinculo-resposta")
    assert "orq-qa2" in orq.last_ai(), orq.last_ai()
    assert not orq.problems(), orq.problems()
    orq.unlink(agent2)
    assert orq.links() == []
    orq.shot("vinculo-desfeito")
