"""↻ reabrir com nome: um agente aberto antes de os agentes subirem com `-n` não tem nome, e os níveis não
conseguiriam falar com ele pelo SendMessage. O QA simula isso tirando o `name` do state.json de um agente real
(o supervisor só o regrava quando o estado muda), espera a página perceber, abre ⇅ e usa o ↻ que ela oferece no
lugar das receitas: o agente fecha e reabre na mesma conversa (`spawn --mode resume --restart`), com pid novo e
nome, lembrando a palavra-chave guardada antes. Dois turnos curtos."""

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
        pytest.skip(f"o teste mexe no state.json nesta máquina ({local_host_name()}), e ORQ_QA_PROJECT está noutra")
    reply = orq.say(f"Guarde a palavra-chave {SECRET}. Responda apenas: guardado.")
    assert "guardado" in reply.lower(), reply
    before = entry_of(orq, agent)
    assert before.get("name") == agent.replace("@", "-") and before.get("pid"), before
    # como um agente de antes dos nomes: o state.json sem `name`
    sf = STATE_ROOT / re.sub(r"[^a-zA-Z0-9_-]", "-", agent) / "state.json"
    d = json.loads(sf.read_text())
    d.pop("name", None)
    tmp = sf.with_suffix(".qa.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    os.replace(tmp, sf)
    deadline = time.time() + 45  # a consulta às máquinas é a cada 15 s
    while time.time() < deadline and entry_of(orq, agent).get("name"):
        orq.page.wait_for_timeout(1000)
    assert not entry_of(orq, agent).get("name"), entry_of(orq, agent)
    orq.page.wait_for_timeout(6000)  # a página relê o estado a cada 5 s
    # ⇅ não oferece receitas: oferece reabrir
    orq.page.click("#lvl")
    orq.page.wait_for_selector("#picker:not([hidden]) #plist .ok", timeout=30000)
    assert "Reabrir com nome" in orq.page.inner_text("#plist") and orq.page.locator("#plist .lvp").count() == 0
    orq.shot("reabrir-oferta")
    orq.page.wait_for_timeout(2000)  # pausas: o vídeo é para uma pessoa conferir
    orq.page.click("#plist .ok")
    orq.wait_fn("() => document.getElementById('toast').textContent.includes('reaberto com nome')", timeout=120000)
    orq.wait_fn(
        "(l) => document.querySelector('.tab.on')?.dataset.l === l"
        " && document.getElementById('cstate').textContent.includes('esperando')",
        arg=agent,
        timeout=120000,
    )
    deadline = time.time() + 60
    while time.time() < deadline and not (entry_of(orq, agent).get("name") and entry_of(orq, agent).get("pid") != before["pid"]):
        orq.page.wait_for_timeout(1000)
    after = entry_of(orq, agent)
    assert after.get("name") == before["name"] and after.get("pid") != before["pid"], (before, after)
    assert after.get("account") == before.get("account"), "mesma conta"
    orq.shot("reabrir-reaberto")
    orq.page.wait_for_timeout(1500)
    # a mesma conversa: o agente novo lembra o que o antigo guardou
    reply = orq.say("Qual é a palavra-chave que pedi para guardar? Responda só com ela.")
    assert SECRET in reply, reply
    orq.shot("reabrir-lembra")
    # agora ⇅ oferece as receitas
    orq.page.click("#lvl")
    orq.page.wait_for_selector("#picker:not([hidden]) .p.lvp", timeout=30000)
    assert orq.page.locator("#plist .lvp").count() >= 2 and orq.page.locator("#plist .ok").count() == 0
    orq.shot("reabrir-receitas")
    orq.page.wait_for_timeout(1500)
    orq.page.click("#pclose")
    assert not orq.problems(), orq.problems()
