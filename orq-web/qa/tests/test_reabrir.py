"""↻ reopen with a name: an agent opened before agents were started with `-n` has no name, and the levels
would not be able to talk to it through SendMessage. The QA simulates that by removing the `name` from the
state.json of a real agent (the supervisor only rewrites it when the state changes), waits for the page to
notice, opens ⇅ and uses the ↻ it offers in place of the recipes: the agent closes and reopens in the same
conversation (`spawn --mode resume --restart`), with a new pid and a name, remembering the keyword kept
before. Two short turns."""

import json
import os
import re
import time

import pytest
from conftest import PROJECT
from orqqa import STATE_ROOT, local_host_name

SECRET = "pitanga-905"


def entry_of(orq, label: str) -> dict:
    return next((a for a in orq.agents() if a["label"] == label), {})


def test_reabrir_com_nome(orq, agent):
    if PROJECT.partition(":")[0] != local_host_name():
        pytest.skip(
            f"the test touches the state.json on this machine ({local_host_name()}), and ORQ_QA_PROJECT is "
            "on another one"
        )
    reply = orq.say(f"Keep the keyword {SECRET}. Answer only: stored.")
    assert "stored" in reply.lower(), reply
    before = entry_of(orq, agent)
    assert before.get("name") == agent.replace("@", "-") and before.get("pid"), before
    # like an agent from before the names: the state.json without `name`
    sf = STATE_ROOT / re.sub(r"[^a-zA-Z0-9_-]", "-", agent) / "state.json"
    d = json.loads(sf.read_text())
    d.pop("name", None)
    tmp = sf.with_suffix(".qa.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    os.replace(tmp, sf)
    deadline = time.time() + 45  # the query to the machines is every 15 s
    while time.time() < deadline and entry_of(orq, agent).get("name"):
        orq.page.wait_for_timeout(1000)
    assert not entry_of(orq, agent).get("name"), entry_of(orq, agent)
    orq.page.wait_for_timeout(6000)  # the page rereads the state every 5 s
    # ⇅ offers no recipes: it offers to reopen
    orq.page.click("#lvl")
    orq.page.wait_for_selector("#picker:not([hidden]) #plist .ok", timeout=30000)
    assert "Reopen with a name" in orq.page.inner_text("#plist")
    assert orq.page.locator("#plist .lvp").count() == 0, "no recipes while it has no name"
    orq.shot("reabrir-oferta")
    orq.page.wait_for_timeout(2000)  # pauses: the video is for a person to check
    orq.page.click("#plist .ok")
    orq.wait_fn(
        "() => document.getElementById('toast').textContent.includes('reopened with a name')", timeout=120000
    )
    orq.wait_fn(
        "(l) => document.querySelector('.tab.on')?.dataset.l === l"
        " && document.getElementById('cstate').textContent.includes('waiting')",
        arg=agent,
        timeout=120000,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        e = entry_of(orq, agent)
        if e.get("name") and e.get("pid") != before["pid"]:
            break
        orq.page.wait_for_timeout(1000)
    after = entry_of(orq, agent)
    assert after.get("name") == before["name"] and after.get("pid") != before["pid"], (before, after)
    assert after.get("account") == before.get("account"), "same account"
    orq.shot("reabrir-reaberto")
    orq.page.wait_for_timeout(1500)
    # the same conversation: the new agent remembers what the old one kept
    reply = orq.say("What is the keyword I asked you to keep? Answer only with it.")
    assert SECRET in reply, reply
    orq.shot("reabrir-lembra")
    # now ⇅ offers the recipes
    orq.page.click("#lvl")
    orq.page.wait_for_selector("#picker:not([hidden]) .p.lvp", timeout=30000)
    assert orq.page.locator("#plist .lvp").count() >= 2 and orq.page.locator("#plist .ok").count() == 0
    orq.shot("reabrir-receitas")
    orq.page.wait_for_timeout(1500)
    orq.page.click("#pclose")
    assert not orq.problems(), orq.problems()
