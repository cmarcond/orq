"""Login por senha, como na página da rede: um segundo orq-web só deste teste sobe na porta ORQ_QA_LOGIN_PORT
(8791) com ORQ_PASSWORD_FILE apontando para uma senha descartável, e o navegador do QA entra nele. Sem cota:
sem senha não se entra (página e API); senha errada avisa; cinco erros seguidos bloqueiam por um minuto; a
senha certa dá o cookie e volta para a página pedida (a do celular, /m, quando foi por ela); sair invalida o
cookie. Cada teste ganha um servidor novo (sessões e bloqueios ficam em memória)."""

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
            raise RuntimeError(f"orq-web com senha saiu com {p.returncode}; veja {OUT / f'orq-web-{PORT}.log'}")
        time.sleep(0.3)
    else:
        raise RuntimeError(f"orq-web com senha não respondeu na porta {PORT}")
    try:
        yield BASE
    finally:
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(10)
        log.close()
        (STATE_ROOT / f"web-{PORT}.url").unlink(missing_ok=True)


def api_status(orq, path: str) -> int:
    """Status da API pelo navegador do QA (mesmos cookies da aba), sem o token."""
    return orq.page.request.get(BASE + path).status


def tenta_senha(orq, senha: str) -> None:
    orq.page.wait_for_selector("#p")
    orq.page.fill("#p", senha)
    orq.page.click("button[type=submit]")
    orq.page.wait_for_load_state()


def test_sem_senha_nao_entra(orq, servidor_com_senha):
    orq.page.goto(BASE + "/")
    assert orq.page.url.rstrip("/").endswith("/login"), orq.page.url
    assert orq.page.title() == "orq · entrar" and orq.page.is_visible("#p")
    assert api_status(orq, "/api/state") == 403, "a API também exige entrar"
    orq.shot("login-pagina")
    orq.page.wait_for_timeout(1500)  # pausas: o vídeo é para uma pessoa conferir
    tenta_senha(orq, "errada")
    assert "senha incorreta" in orq.page.inner_text("body")
    assert orq.page.url.rstrip("/").endswith("/login") and api_status(orq, "/api/state") == 403
    orq.shot("login-senha-errada")
    orq.page.wait_for_timeout(1500)


def test_cinco_erros_bloqueiam(orq, servidor_com_senha):
    orq.page.goto(BASE + "/")
    for i in range(5):  # cada erro custa 1 s de espera no servidor; o quinto bloqueia o IP por um minuto
        tenta_senha(orq, f"errada-{i}")
        assert "senha incorreta" in orq.page.inner_text("body"), i
    tenta_senha(orq, SENHA)  # a senha certa não entra enquanto o bloqueio durar
    assert "muitas tentativas" in orq.page.inner_text("body")
    assert api_status(orq, "/api/state") == 403
    orq.shot("login-bloqueado")
    orq.page.wait_for_timeout(2000)


def test_entra_pelo_celular_e_sai(orq, servidor_com_senha):
    orq.page.goto(BASE + "/m")  # pedida pela página do celular: o login volta para ela
    assert orq.page.url.endswith("/login?next=/m"), orq.page.url
    tenta_senha(orq, SENHA)
    assert orq.page.url.rstrip("/").endswith("/m") and orq.page.title() == "orq · celular", orq.page.url
    orq.page.wait_for_selector("#tabs .tab.plus", timeout=20000)
    assert api_status(orq, "/api/state") == 200, "o cookie vale para a API"
    orq.shot("login-entrou-celular")
    orq.page.wait_for_timeout(1500)
    orq.page.goto(BASE + "/")  # a página normal abre com o mesmo cookie, sem pedir senha de novo
    orq.page.wait_for_selector("#tabs .tab.plus", timeout=20000)
    assert orq.page.title() == "orq"
    orq.shot("login-entrou")
    orq.page.wait_for_timeout(1500)
    # sair: o cookie deixa de valer
    orq.page.evaluate("() => fetch('/logout', { method: 'POST' })")
    orq.page.wait_for_timeout(500)
    orq.page.goto(BASE + "/")
    assert orq.page.url.rstrip("/").endswith("/login"), orq.page.url
    assert api_status(orq, "/api/state") == 403
    orq.shot("login-saiu")
    orq.page.wait_for_timeout(1500)
