"""The conversation survives the page and the server: the supervisor keeps the numbered events, whoever
reconnects asks "from N on" and receives what it missed. Two turns with the real claude, each interrupted in
the middle: `test_recarregar_no_meio_do_turno` reloads the page while claude writes (the new tab asks from 0:
the whole conversation comes back and the turn continues live); `test_servidor_reinicia_no_meio_do_turno`
takes down and brings up the QA's orq-web in the middle of the reply (the agents keep running; the tab
reconnects the stream asking only for what was missing, and nothing is repeated in the chat)."""

import time
from urllib.parse import parse_qs, urlparse

from orqqa import server_down, server_up

N = 100
TASK = f"Count from 1 to {N}, one number per line, without comments or summary, and end with the word FIM."


def wait_streaming(orq, timeout: float = 90) -> None:
    """Claude has started writing the reply (live text in the chat)."""
    orq.wait_fn("() => document.querySelector('.chat.on .ai.live') !== null", timeout=timeout * 1000)


def numbers(text: str):
    return [int(ln.strip()) for ln in text.splitlines() if ln.strip().isdigit()]


def check_reply(orq, task_count: int):
    """One message of yours per turn, the whole count, without repetition, and the FIM."""
    me = orq.page.evaluate("[...document.querySelectorAll('.chat.on .me')].map((e) => e.innerText)")
    assert len(me) == task_count and all(TASK in m for m in me), me
    txt = orq.last_ai()
    assert "FIM" in txt, txt[-200:]
    nums = numbers(txt)
    msg = f"count with a gap or repetition: {nums[:5]}…{nums[-5:]} ({len(nums)})"
    assert nums == list(range(1, N + 1)), msg


def problems_outside_restart(orq):
    """During the server restart the page fails to fetch state and stream; that is expected."""
    return [p for p in orq.problems() if "/api/state" not in p and "/stream?" not in p]


def test_recarregar_no_meio_do_turno(orq, agent):
    orq.say(TASK, wait=False)
    wait_streaming(orq)
    before = len(numbers(orq.last_ai()))
    orq.shot("recarga-antes")
    orq.page.reload()
    orq.page.wait_for_selector("#tabs .tab.plus", timeout=20000)
    orq.page.wait_for_selector(f'.tab[data-l="{agent}"]', timeout=30000)
    if orq.current() != agent:
        orq.show(agent)
    # the new tab asks from 0: the whole conversation comes back, and the turn in progress continues live
    orq.wait_fn("() => document.querySelectorAll('.chat.on .me').length === 1", timeout=30000)
    orq.page.wait_for_timeout(1500)  # pauses: the video is for a person to check
    orq.shot("recarga-depois")
    st = orq.wait_idle(180)
    assert "error" not in st, st
    check_reply(orq, 1)
    assert len(numbers(orq.last_ai())) > before, "the reply continued after the reload"
    orq.shot("recarga-fim")
    assert not orq.problems(), orq.problems()


def test_servidor_reinicia_no_meio_do_turno(orq, agent):
    streams = []
    orq.page.on("request", lambda r: streams.append(r.url) if "/stream?" in r.url else None)
    orq.say(TASK, wait=False)
    wait_streaming(orq)
    n0 = len(streams)
    orq.shot("reinicio-antes")
    assert server_down(), "the QA's orq-web did not go down"
    orq.page.wait_for_timeout(2000)  # the page is without a server for a moment; claude keeps writing
    url, started = server_up()
    assert started, url
    # the tab reconnects the stream asking "from N on": only what it missed, not the whole conversation
    deadline = time.time() + 30
    while len(streams) == n0 and time.time() < deadline:
        orq.page.wait_for_timeout(300)
    assert len(streams) > n0, "the tab did not reconnect the stream"
    since = int(parse_qs(urlparse(streams[-1]).query)["since"][0])
    assert since > 0, streams[-1]
    st = orq.wait_idle(180)
    assert "error" not in st, st
    check_reply(orq, 1)
    orq.shot("reinicio-depois")
    orq.page.wait_for_timeout(1500)
    assert not problems_outside_restart(orq), problems_outside_restart(orq)
