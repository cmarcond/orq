"""Fixtures do QA: um orq-web real só do QA (ou ORQ_QA_URL), uma aba no Chromium por teste, captura de
tela e console gravados em qa/out/ no fim de cada teste (com -FALHOU no nome quando falha)."""

import os

import pytest
from orqqa import Orq, server_down, server_up
from playwright.sync_api import sync_playwright

PROJECT = os.environ.get("ORQ_QA_PROJECT", "laptop:~/Projects/orq-qa")


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


@pytest.fixture
def agent(orq):
    """Agente real (claude de verdade) na pasta descartável do QA; encerrado no fim do teste."""
    host, _, path = PROJECT.partition(":")
    orq.ensure_stopped(f"{path.rstrip('/').split('/')[-1]}@{host}")
    label = orq.open_project(PROJECT)
    yield label
    try:
        orq.stop(label)
    except Exception:
        orq.ensure_stopped(label)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    out = yield
    rep = out.get_result()
    setattr(item, "rep_" + rep.when, rep)
