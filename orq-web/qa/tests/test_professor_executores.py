"""Receita professor → executores: o nível de cima coordena o agente da aba e os vinculados a ele.

Dois agentes reais na máquina do servidor (contas diferentes), vínculo pela página, ⇅ com a receita multi; o
professor pergunta a cada executor a pasta de trabalho e explica as duas. Gasta cota: uns 5 turnos.
"""


def test_professor_coordena_dois_executores(orq, agent, agent2):
    assert orq.link(agent2) == [agent2]
    orq.page.click("#lvl")
    orq.page.wait_for_selector('#picker:not([hidden]) .p.lvp[data-id="professor-executores"]', timeout=30000)
    row = orq.page.inner_text('.p.lvp[data-id="professor-executores"]')
    assert "2 executores" in row and "1 vinculados" in row, row
    orq.page.click("#pclose")
    levels = orq.upgrade("professor-executores")
    assert levels == ["professor", "executor"], levels
    orq.shot("prof-executores-subiu")
    # o professor ficou vinculado ao segundo executor; o vínculo da base continua
    links = orq.state()["links"]
    prof = agent.replace("@", "~professor@")
    assert any(lk["local"] == prof and lk["remote"] == agent2 and lk["up"] for lk in links), links
    orq.say(
        "Pergunte a cada um dos seus executores, pelo SendMessage, qual é a pasta de trabalho dele, "
        "e quando os dois responderem me diga os dois caminhos, um por linha.",
        wait=False,
    )
    st = orq.answer_permissions(300)
    assert "erro" not in st, st
    # as duas respostas chegam pelos fios; a explicação final cita as duas pastas
    orq.wait_fn(
        "() => [...document.querySelectorAll('#side .wire.in, #side .wire.delivered')].length >= 2",
        timeout=240000,
    )
    orq.wait_idle(180)
    orq.shot("prof-executores-respostas")
    txt = orq.last_ai()
    assert "orq-qa2" in txt and "orq-qa" in txt.replace("orq-qa2", ""), txt
    assert not orq.problems(), orq.problems()
    orq.downgrade()
    assert orq.levels() == []
    links = orq.state()["links"]
    assert not any(lk["local"] == prof and lk["up"] for lk in links), "o vínculo do professor foi desfeito"
    assert any({lk["local"], lk["remote"]} == {agent, agent2} and lk["up"] for lk in links), "o da base fica"
    orq.unlink(agent2)
