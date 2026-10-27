"""A conversa sobrevive à página e ao servidor: o supervisor guarda os eventos numerados, quem reconecta pede
"a partir do N" e recebe o que perdeu. Dois turnos com o claude real, cada um interrompido no meio:
`test_recarregar_no_meio_do_turno` recarrega a página enquanto o claude escreve (a aba nova pede desde 0: a
conversa inteira volta e o turno continua ao vivo); `test_servidor_reinicia_no_meio_do_turno` derruba e sobe o
orq-web do QA no meio da resposta (os agentes continuam; a aba reconecta o stream pedindo só o que faltou, e nada
se repete no chat)."""

import time
from urllib.parse import parse_qs, urlparse

from orqqa import server_down, server_up

N = 100
TASK = f"Conte de 1 até {N}, um número por linha, sem comentários nem resumo, e termine com a palavra FIM."


def wait_streaming(orq, timeout: float = 90) -> None:
    """O claude começou a escrever a resposta (texto ao vivo no chat)."""
    orq.wait_fn("() => document.querySelector('.chat.on .ai.live') !== null", timeout=timeout * 1000)


def numbers(text: str):
    return [int(l.strip()) for l in text.splitlines() if l.strip().isdigit()]


def check_reply(orq, task_count: int):
    """Uma mensagem sua por turno, a contagem inteira, sem repetição, e o FIM."""
    me = orq.page.evaluate("[...document.querySelectorAll('.chat.on .me')].map((e) => e.innerText)")
    assert len(me) == task_count and all(TASK in m for m in me), me
    txt = orq.last_ai()
    assert "FIM" in txt, txt[-200:]
    nums = numbers(txt)
    assert nums == list(range(1, N + 1)), f"contagem com falha ou repetição: {nums[:5]}…{nums[-5:]} ({len(nums)})"


def problems_outside_restart(orq):
    """Durante o reinício do servidor a página falha ao buscar estado e stream; isso é esperado."""
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
    # a aba nova pede desde 0: a conversa inteira volta, e o turno em andamento continua ao vivo
    orq.wait_fn("() => document.querySelectorAll('.chat.on .me').length === 1", timeout=30000)
    orq.page.wait_for_timeout(1500)  # pausas: o vídeo é para uma pessoa conferir
    orq.shot("recarga-depois")
    st = orq.wait_idle(180)
    assert "erro" not in st, st
    check_reply(orq, 1)
    assert len(numbers(orq.last_ai())) > before, "a resposta continuou depois da recarga"
    orq.shot("recarga-fim")
    assert not orq.problems(), orq.problems()


def test_servidor_reinicia_no_meio_do_turno(orq, agent):
    streams = []
    orq.page.on("request", lambda r: streams.append(r.url) if "/stream?" in r.url else None)
    orq.say(TASK, wait=False)
    wait_streaming(orq)
    n0 = len(streams)
    orq.shot("reinicio-antes")
    assert server_down(), "o orq-web do QA não caiu"
    orq.page.wait_for_timeout(2000)  # a página fica um instante sem servidor; o claude segue escrevendo
    url, started = server_up()
    assert started, url
    # a aba reconecta o stream pedindo "a partir do N": só o que perdeu, não a conversa inteira
    deadline = time.time() + 30
    while len(streams) == n0 and time.time() < deadline:
        orq.page.wait_for_timeout(300)
    assert len(streams) > n0, "a aba não reconectou o stream"
    since = int(parse_qs(urlparse(streams[-1]).query)["since"][0])
    assert since > 0, streams[-1]
    st = orq.wait_idle(180)
    assert "erro" not in st, st
    check_reply(orq, 1)
    orq.shot("reinicio-depois")
    orq.page.wait_for_timeout(1500)
    assert not problems_outside_restart(orq), problems_outside_restart(orq)
