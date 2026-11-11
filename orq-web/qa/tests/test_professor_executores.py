"""Recipe teacher → executors: the level above coordinates the tab's agent and the ones linked to it.

Two real agents on the server's machine (different accounts), link through the page, ⇅ with the multi recipe;
the teacher asks each executor for its working folder and explains both. Spends quota: about 5 turns.
"""


def test_professor_coordena_dois_executores(orq, agent, agent2):
    assert orq.link(agent2) == [agent2]
    orq.page.click("#lvl")
    orq.page.wait_for_selector('#picker:not([hidden]) .p.lvp[data-id="professor-executores"]', timeout=30000)
    row = orq.page.inner_text('.p.lvp[data-id="professor-executores"]')
    assert "2 executors" in row and "1 linked" in row, row
    orq.page.click("#pclose")
    levels = orq.upgrade("professor-executores")
    assert levels == ["professor", "executor"], levels
    orq.shot("prof-executores-subiu")
    # the teacher got linked to the second executor; the base's link remains
    links = orq.state()["links"]
    prof = agent.replace("@", "~professor@")
    assert any(lk["local"] == prof and lk["remote"] == agent2 and lk["up"] for lk in links), links
    orq.say(
        "Ask each of your executors, through SendMessage, what its working folder is, "
        "and when both have answered tell me the two paths, one per line.",
        wait=False,
    )
    st = orq.answer_permissions(300)
    assert "error" not in st, st
    # the two replies arrive through the wires; the final explanation mentions both folders
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
    assert not any(lk["local"] == prof and lk["up"] for lk in links), "the teacher's link was removed"
    assert any({lk["local"], lk["remote"]} == {agent, agent2} and lk["up"] for lk in links), (
        "the base's one stays"
    )
    orq.unlink(agent2)
