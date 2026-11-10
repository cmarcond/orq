"""Fixtures do QA: um orq-web real só do QA (ou ORQ_QA_URL), uma aba no Chromium por teste, captura de
tela e console gravados em qa/out/ no fim de cada teste (com -FALHOU no nome quando falha).

Os agentes reais sobem em pastas descartáveis (ORQ_QA_PROJECT, ORQ_QA_PROJECT2), sempre com nome orq-qa… ou
orq-demo…: antes e depois de cada teste, `Orq.purge` desfaz os níveis, encerra os agentes daquela pasta e
apaga a pasta, o estado em ~/.cache/orq e as conversas do claude, na máquina do agente. Nada fica para trás,
nem quando o teste falha."""

import os

import pytest
from orqqa import Orq, server_down, server_up
from playwright.sync_api import sync_playwright

PROJECT = os.environ.get("ORQ_QA_PROJECT", "laptop:~/Projects/orq-qa")
PROJECT2 = os.environ.get("ORQ_QA_PROJECT2", "laptop:~/Projects/orq-qa2")  # segundo agente (vínculos)


@pytest.fixture(scope="session")
def orq_server():
    """Link do orq-web alvo. Sem ORQ_QA_URL, sobe um na porta do QA e derruba no fim (ORQ_QA_KEEP=1 deixa)."""
    if os.environ.get("ORQ_QA_URL"):
        yield os.environ["ORQ_QA_URL"]
        return
    url, started = server_up()
    yield url
    if started and not os.environ.get("ORQ_QA_KEEP"):
        server_down()


@pytest.fixture
def orq(orq_server, request):
    with sync_playwright() as pw:
        o = Orq.launch(pw, headed=bool(os.environ.get("ORQ_QA_HEADED")))
        yield o
        rep = getattr(request.node, "rep_call", None)
        name = request.node.name + ("-FALHOU" if rep is not None and rep.failed else "")
        try:
            o.shot(name)
            o.dump(name)
        finally:
            v = o.close(name)  # vídeo da sessão inteira: qa/out/<teste>.mp4 (ou .webm)
            if v:
                print(f"\nvídeo: {v}")


def qa_agent(orq, spec):
    """Abre um agente real em `spec` (pasta descartável, limpa antes) e, no fim, fecha a aba pelo ✕ e limpa
    tudo de novo, mesmo se o teste falhou."""
    orq.purge(spec)
    label = orq.open_project(spec)
    try:
        yield label
    finally:
        try:
            orq.stop(label)  # ✕ na aba, como a pessoa faria
        except Exception:
            pass  # a limpeza abaixo encerra pela API
        orq.purge(spec)


@pytest.fixture
def agent(orq):
    """Agente real (claude de verdade) na pasta descartável do QA; fechado e apagado no fim do teste."""
    yield from qa_agent(orq, PROJECT)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    out = yield
    rep = out.get_result()
    setattr(item, "rep_" + rep.when, rep)


@pytest.fixture
def agent2(orq, agent):
    """Um segundo agente real, noutra pasta descartável, para os vínculos; a aba volta ao primeiro."""
    for label in qa_agent(orq, PROJECT2):
        orq.show(agent)
        yield label
