"""Login por senha: unitários do Auth e do check_bind, e o fluxo HTTP completo com cookie."""

import http.client
import json
import threading
from pathlib import Path

import pytest

from orqweb import config
from orqweb.server import handlers as H
from orqweb.server.app import Server, check_bind, local_host_from_env
from orqweb.server.auth import Auth, login_page
from orqweb.server.bridge import BridgeRegistry
from orqweb.server.external import External
from orqweb.server.state import StateCache


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_auth(tmp_path, password="segredo", **kw):
    f = tmp_path / "password"
    if password is not None:
        f.write_text(password + "\n")
    slept = []
    a = Auth(f, sleep=slept.append, clock=kw.pop("clock", Clock()), **kw)
    return a, slept


# ---------------------------------------------------------------- unitários
def test_disabled_without_file_or_empty(tmp_path):
    a, _ = make_auth(tmp_path, password=None)
    assert not a.enabled and a.login("x", "ip") is None
    a2, _ = make_auth(tmp_path, password="   ")
    assert not a2.enabled
    assert not Auth(None).enabled


def test_login_right_and_wrong(tmp_path):
    a, slept = make_auth(tmp_path)
    sid = a.login("segredo", "1.2.3.4")
    assert sid and a.valid(sid) and slept == []
    assert a.login("errada", "1.2.3.4") is None and slept == [1.0], "falha dorme 1 s"
    assert not a.valid("inexistente") and not a.valid(None)


def test_block_after_five_failures_and_release(tmp_path):
    clk = Clock()
    a, _ = make_auth(tmp_path, clock=clk)
    for _ in range(5):
        a.login("errada", "ip")
    assert a.blocked("ip")
    assert a.login("segredo", "ip") is None, "bloqueado não entra nem com a senha certa"
    assert not a.blocked("outro-ip")
    clk.t += 61
    assert not a.blocked("ip") and a.login("segredo", "ip")


def test_session_ttl_and_logout(tmp_path):
    clk = Clock()
    a, _ = make_auth(tmp_path, clock=clk, ttl=100)
    sid = a.login("segredo", "ip")
    clk.t += 90
    assert a.valid(sid), "uso renova"
    clk.t += 90
    assert a.valid(sid)
    clk.t += 101
    assert not a.valid(sid), "expirou sem uso"
    sid2 = a.login("segredo", "ip")
    a.logout(sid2)
    assert not a.valid(sid2)


def test_cookie_helpers(tmp_path):
    a, _ = make_auth(tmp_path)
    h = a.cookie_header("abc")
    assert h.startswith("orq=abc;") and "HttpOnly" in h and "SameSite=Strict" in h
    assert Auth.sid_from_cookie("x=1; orq=abc; y=2") == "abc"
    assert Auth.sid_from_cookie("x=1") is None and Auth.sid_from_cookie(None) is None
    assert "Max-Age=0" in Auth.clear_cookie_header()


def test_login_page_escapes_nothing_dangerous_and_shows_message():
    assert "<form" in login_page() and "senha incorreta" in login_page("senha incorreta")


def test_check_bind_and_local_host(monkeypatch):
    assert check_bind("127.0.0.1", False) is None
    assert check_bind("10.0.0.10", True) is None
    assert "recusado" in check_bind("10.0.0.10", False)
    assert "recusado" in check_bind("0.0.0.0", False)
    monkeypatch.delenv("ORQ_LOCAL_HOST", raising=False)
    assert local_host_from_env() == "laptop"
    for v in ("none", "nenhum", "-", ""):
        monkeypatch.setenv("ORQ_LOCAL_HOST", v)
        assert local_host_from_env() is None, v
    monkeypatch.setenv("ORQ_LOCAL_HOST", "server0")
    assert local_host_from_env() == "server0"


# ---------------------------------------------------------------- HTTP
class FakeExt:
    def usage(self, host=None):
        return {}

    def all_agents(self, hosts):
        return [], {}

    def all_projects(self, hosts, limit=30):
        return [], {}

    def spawn(self, *a):
        return None


@pytest.fixture
def server(tmp_path):
    (tmp_path / "password").write_text("segredo\n")
    conf = config.parse(
        {"accounts": [{"name": "gmail", "config_dir": "~/.claude-gmail"}], "orq_hosts": ["server0"]}
    )
    ext = FakeExt()
    ctx = H.Context(conf, StateCache(conf, ext), BridgeRegistry(), ext.spawn, "tok")
    ctx.auth = Auth(tmp_path / "password", sleep=lambda s: None)
    ctx.allowed_hosts |= {"10.0.0.10"}
    ctx.local_host = None
    srv = Server(("127.0.0.1", 0), H.make_handler(ctx))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], ctx
    srv.shutdown()
    srv.server_close()


def req(port, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = dict(headers or {})
    if host:
        h["Host"] = host
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data, r


def login(port, password="segredo"):
    status, _, r = req(
        port,
        "POST",
        "/login",
        body=f"password={password}".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return status, r.getheader("Set-Cookie"), r.getheader("Location")


def test_page_redirects_to_login_without_credentials(server):
    port, _ = server
    status, _, r = req(port, "GET", "/")
    assert status == 303 and r.getheader("Location") == "/login"
    status, body, r = req(port, "GET", "/login")
    assert status == 200 and b"<form" in body
    csp = r.getheader("Content-Security-Policy")
    assert "form-action 'self'" in csp, (
        "form-action 'none' bloqueia o próprio formulário de login no navegador"
    )
    assert req(port, "GET", "/api/state")[0] == 403


def test_login_flow_sets_cookie_and_grants_api_and_sse(server):
    port, _ = server
    status, cookie, loc = login(port)
    assert status == 303 and loc == "/" and cookie.startswith("orq=") and "HttpOnly" in cookie
    sid_cookie = cookie.split(";")[0]
    status, body, _ = req(port, "GET", "/api/state", headers={"Cookie": sid_cookie})
    assert status == 200 and json.loads(body)["accounts"] == ["gmail"]
    assert req(port, "GET", "/", headers={"Cookie": sid_cookie})[0] == 200
    # SSE por cookie, sem ?t=: o handler aceita e abre o stream (a ponte vai falhar, mas o 200 vem antes)
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/api/agent/x%40server0/stream?since=0", headers={"Cookie": sid_cookie})
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
    c.close()


def test_wrong_password_and_block(server):
    port, _ = server
    status, cookie, _ = login(port, "errada")
    assert status == 403 and cookie is None
    for _ in range(4):
        login(port, "errada")
    status, _, _ = login(port, "segredo")
    assert status == 429, "quinta falha bloqueia o IP por um minuto"


def test_logout_invalidates_cookie(server):
    port, _ = server
    _, cookie, _ = login(port)
    sid_cookie = cookie.split(";")[0]
    status, _, r = req(port, "POST", "/logout", headers={"Cookie": sid_cookie})
    assert status == 303 and "Max-Age=0" in r.getheader("Set-Cookie")
    assert req(port, "GET", "/api/state", headers={"Cookie": sid_cookie})[0] == 403


def test_token_still_works_and_host_allowlist(server):
    port, _ = server
    assert req(port, "GET", "/api/state", headers={"X-Orq-Token": "tok"})[0] == 200
    assert (
        req(port, "GET", "/api/state", headers={"X-Orq-Token": "tok"}, host="10.0.0.10:8765")[0] == 200
    )
    assert req(port, "GET", "/api/state", headers={"X-Orq-Token": "tok"}, host="evil.example")[0] == 403
    assert req(port, "GET", "/login", host="evil.example")[0] == 403
    status, _, _ = req(port, "POST", "/login", body=b"password=segredo", host="evil.example")
    assert status == 403


def test_without_password_file_login_is_off_and_page_is_open(tmp_path):
    conf = config.parse({"accounts": []})
    ext = FakeExt()
    ctx = H.Context(conf, StateCache(conf, ext), BridgeRegistry(), ext.spawn, "tok")
    ctx.auth = Auth(tmp_path / "nao-existe")
    srv = Server(("127.0.0.1", 0), H.make_handler(ctx))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        assert req(port, "GET", "/")[0] == 200, (
            "sem senha configurada, a página abre como hoje (API segue pedindo o token)"
        )
        assert req(port, "POST", "/login", body=b"password=x")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------- host local configurável
def test_external_without_local_host_uses_ssh_for_everything_and_F_config():
    calls = []

    def run(cmd, timeout):
        calls.append(cmd)
        import subprocess

        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    ext = External(Path("/b"), run, local_host=None, ssh_config="/cfg/ssh_config")
    assert ext.all_hosts(["server0", "laptop"]) == ["server0", "laptop"]
    ext.all_agents(["server0", "laptop"])
    assert all(c[0] == "ssh" and "-F" in c and "/cfg/ssh_config" in c for c in calls), calls
    assert ext.spawn("server0", "p@server0", "/p", "gmail", "new") is None and calls[-1][0] == "ssh"
    ext2 = External(Path("/b"), run, local_host="server0")
    assert ext2.all_hosts(["laptop"]) == ["server0", "laptop"]
    ext2.spawn("server0", "p@server0", "/p", "gmail", "new")
    assert calls[-1][0] == "/b/orq-agent", "host local roda direto"


def test_pick_host_and_label_respects_local_host():
    from orqweb.server.state import pick_host_and_label

    assert (
        pick_host_and_label({"host": "laptop", "cwd": "/x"}, ["server0"], set(), local=None)[1]
        == "projeto inválido"
    )
    p, err = pick_host_and_label({"host": "laptop", "cwd": "/x"}, ["server0", "laptop"], set(), local=None)
    assert err is None and p["label"] == "x@laptop"


def test_saved_token_comes_from_url_file(tmp_path, monkeypatch):
    from orqweb.server import app as A

    monkeypatch.setenv("ORQ_STATE_DIR", str(tmp_path))
    assert A.saved_token(8765) is None
    A.url_file(8765).write_text("http://127.0.0.1:8765/?t=abc123")
    assert A.saved_token(8765) == "abc123"
    A.url_file(8765).write_text("lixo")
    assert A.saved_token(8765) is None
