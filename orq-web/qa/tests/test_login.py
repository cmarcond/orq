"""Password login, as on the page on the network: a second orq-web just for this test starts on port
ORQ_QA_LOGIN_PORT (8791) with ORQ_PASSWORD_FILE pointing at a throwaway password, and the QA browser signs in
to it. No quota: without the password there is no getting in (page and API); a wrong password warns; five
errors in a row block for one minute; the right password gives the cookie and returns to the requested page
(the phone one, /m, when it came through it); logging out invalidates the cookie. Each test gets a new server
(sessions and blocks are kept in memory)."""

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
from orqqa import OUT, ROOT, STATE_ROOT

PORT = int(os.environ.get("ORQ_QA_LOGIN_PORT", "8791"))
BASE = f"http://127.0.0.1:{PORT}"
SENHA = "segredo-do-qa"


@pytest.fixture
def servidor_com_senha(tmp_path):
    pw = tmp_path / "senha"
    pw.write_text(SENHA + "\n")
    log = open(OUT / f"orq-web-{PORT}.log", "ab")
    p = subprocess.Popen(
        [sys.executable, "-m", "orqweb.server.cli", "--port", str(PORT), "--no-open"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "ORQ_PASSWORD_FILE": str(pw), "ORQ_NO_OPEN": "1", "PYTHONUNBUFFERED": "1"},
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if urllib.request.urlopen(BASE + "/login", timeout=2).status == 200:
                break
        except urllib.error.HTTPError:
            break
        except OSError:
            pass
        if p.poll() is not None:
            raise RuntimeError(
                f"orq-web with password exited with {p.returncode}; see {OUT / f'orq-web-{PORT}.log'}"
            )
        time.sleep(0.3)
    else:
        raise RuntimeError(f"orq-web with password did not respond on port {PORT}")
    try:
        yield BASE
    finally:
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(10)
        log.close()
        (STATE_ROOT / f"web-{PORT}.url").unlink(missing_ok=True)


def api_status(orq, path: str) -> int:
    """Status of the API through the QA browser (same cookies as the tab), without the token."""
    return orq.page.request.get(BASE + path).status


def tenta_senha(orq, senha: str) -> None:
    orq.page.wait_for_selector("#p")
    orq.page.fill("#p", senha)
    orq.page.click("button[type=submit]")
    orq.page.wait_for_load_state()


def test_sem_senha_nao_entra(orq, servidor_com_senha):
    orq.page.goto(BASE + "/")
    assert orq.page.url.rstrip("/").endswith("/login"), orq.page.url
    assert orq.page.title() == "orq · sign in" and orq.page.is_visible("#p")
    assert api_status(orq, "/api/state") == 403, "the API also requires signing in"
    orq.shot("login-pagina")
    orq.page.wait_for_timeout(1500)  # pauses: the video is for a person to check
    tenta_senha(orq, "wrong")
    assert "wrong password" in orq.page.inner_text("body")
    assert orq.page.url.rstrip("/").endswith("/login") and api_status(orq, "/api/state") == 403
    orq.shot("login-senha-errada")
    orq.page.wait_for_timeout(1500)


def test_cinco_erros_bloqueiam(orq, servidor_com_senha):
    orq.page.goto(BASE + "/")
    for i in range(5):  # each error costs a 1 s wait on the server; the fifth blocks the IP for one minute
        tenta_senha(orq, f"wrong-{i}")
        assert "wrong password" in orq.page.inner_text("body"), i
    tenta_senha(orq, SENHA)  # the right password does not get in while the block lasts
    assert "too many attempts" in orq.page.inner_text("body")
    assert api_status(orq, "/api/state") == 403
    orq.shot("login-bloqueado")
    orq.page.wait_for_timeout(2000)


def test_entra_pelo_celular_e_sai(orq, servidor_com_senha):
    orq.page.goto(BASE + "/m")  # requested through the phone page: the login returns to it
    assert orq.page.url.endswith("/login?next=/m"), orq.page.url
    tenta_senha(orq, SENHA)
    assert orq.page.url.rstrip("/").endswith("/m") and orq.page.title() == "orq · phone", orq.page.url
    orq.page.wait_for_selector("#tabs .tab.plus", timeout=20000)
    assert api_status(orq, "/api/state") == 200, "the cookie is valid for the API"
    orq.shot("login-entrou-celular")
    orq.page.wait_for_timeout(1500)
    orq.page.goto(BASE + "/")  # the normal page opens with the same cookie, no password asked again
    orq.page.wait_for_selector("#tabs .tab.plus", timeout=20000)
    assert orq.page.title() == "orq"
    orq.shot("login-entrou")
    orq.page.wait_for_timeout(1500)
    # log out: the cookie stops being valid
    orq.page.evaluate("() => fetch('/logout', { method: 'POST' })")
    orq.page.wait_for_timeout(500)
    orq.page.goto(BASE + "/")
    assert orq.page.url.rstrip("/").endswith("/login"), orq.page.url
    assert api_status(orq, "/api/state") == 403
    orq.shot("login-saiu")
    orq.page.wait_for_timeout(1500)
