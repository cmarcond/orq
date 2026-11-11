"""QA fixtures: a real orq-web just for the QA (or ORQ_QA_URL), one tab in Chromium per test, screenshot
and console written to qa/out/ at the end of each test (with -FALHOU in the name when it fails).

The real agents start in throwaway folders (ORQ_QA_PROJECT, ORQ_QA_PROJECT2), always named orq-qa… or
orq-demo…: before and after each test, `Orq.purge` undoes the levels, closes the agents of that folder and
deletes the folder, the state in ~/.cache/orq and claude's conversations, on the agent's machine. Nothing is
left behind, not even when the test fails."""

import os

import pytest
from orqqa import Orq, server_down, server_up
from playwright.sync_api import sync_playwright

PROJECT = os.environ.get("ORQ_QA_PROJECT", "laptop:~/Projects/orq-qa")
PROJECT2 = os.environ.get("ORQ_QA_PROJECT2", "laptop:~/Projects/orq-qa2")  # second agent (links)


@pytest.fixture(scope="session")
def orq_server():
    """Link of the target orq-web. Without ORQ_QA_URL, starts one on the QA port and takes it down at the
    end (ORQ_QA_KEEP=1 keeps it)."""
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
            v = o.close(name)  # video of the whole session: qa/out/<test>.mp4 (or .webm)
            if v:
                print(f"\nvideo: {v}")


def qa_agent(orq, spec):
    """Opens a real agent in `spec` (throwaway folder, cleaned beforehand) and, at the end, closes the tab
    through ✕ and cleans everything again, even if the test failed."""
    orq.purge(spec)
    label = orq.open_project(spec)
    try:
        yield label
    finally:
        try:
            orq.stop(label)  # ✕ on the tab, as a person would
        except Exception:
            pass  # the cleanup below closes it through the API
        orq.purge(spec)


@pytest.fixture
def agent(orq):
    """Real agent (a real claude) in the QA's throwaway folder; closed and deleted at the end of the test."""
    yield from qa_agent(orq, PROJECT)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    out = yield
    rep = out.get_result()
    setattr(item, "rep_" + rep.when, rep)


@pytest.fixture
def agent2(orq, agent):
    """A second real agent, in another throwaway folder, for the links; the tab goes back to the first."""
    for label in qa_agent(orq, PROJECT2):
        orq.show(agent)
        yield label
