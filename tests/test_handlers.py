"""Integração HTTP: servidor real numa porta efêmera, supervisor real com claude falso atrás da ponte."""

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from orqweb import config
from orqweb.agent.supervisor import Supervisor
from orqweb.server import handlers as H
from orqweb.server.app import Server
from orqweb.server.bridge import Bridge, BridgeRegistry
from orqweb.server.state import StateCache

FAKE = Path(__file__).with_name("fake_claude.py")
TOKEN = "tok-de-teste"


class FakeExt:
    def __init__(self):
        self.spawned = []

    def usage(self, script):
        return (
            {"gmail": {"5h": 1, "7d": 10, "7d_pace": 50, "7d_left": 100}}
            if "claude" in script
            else {"err": "x"}
        )

    def all_agents(self, hosts):
        return [], {}

    def all_projects(self, hosts, limit=30):
        return [{"cwd": "/p", "host": "laptop", "mtime": 1, "account": "gmail", "title": "t"}], {}

    def spawn(self, host, label, cwd, account, mode):
        self.spawned.append((host, label, cwd, account, mode))
        return "falhou de propósito" if cwd == "/falha" else None


@pytest.fixture
def short_tmp():
    d = Path(tempfile.mkdtemp(prefix="orqh", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def agent(short_tmp, monkeypatch):
    """Supervisor local com claude falso; ORQ_STATE_DIR aponta para ele, como o attach espera."""
    monkeypatch.setenv("ORQ_STATE_DIR", str(short_tmp))
    s = Supervisor(
        label="proj@laptop",
        folder=short_tmp / "proj",
        account="gmail",
        command=[sys.executable, str(FAKE)],
        env=dict(os.environ),
        state_dir=config.state_dir("proj@laptop"),
        log=lambda *_: None,
    )
    s.start()
    yield s
    s.quit()
    s.wait(10)


def bridge_factory(label, since, bin_dir=None):
    cmd = [sys.executable, "-m", "orqweb.agent.cli", "attach", label, "--since", str(since)]
    return Bridge(label, since, bin_dir, command=cmd)


@pytest.fixture
def server(agent):
    conf = config.parse(
        {"accounts": [{"name": "gmail", "config_dir": "~/.claude-gmail"}], "orq_hosts": ["node1"]}
    )
    ext = FakeExt()
    state = StateCache(conf, ext)
    state.refresh_usage()
    ctx = H.Context(conf, state, BridgeRegistry(factory=bridge_factory), ext.spawn, TOKEN)
    srv = Server(
        ("127.0.0.1", 0), H.make_handler(ctx)
    )  # a mesma classe do app: ignora conexões fechadas pelo cliente
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    CTX[srv.server_address[1]] = ctx
    yield srv.server_address[1], ext, state
    srv.shutdown()
    srv.server_close()


CTX = {}


def server_ctx(port):
    return CTX[port]


def req(port, method, path, body=None, token=TOKEN, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"X-Orq-Token": token, "Content-Type": "application/json"}
    if host:
        headers["Host"] = host
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        return r.status, json.loads(data), r
    except ValueError:
        return r.status, data, r


class SSE:
    def __init__(self, port, label, since=0):
        self.c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        self.c.request("GET", f"/api/agent/{label}/stream?since={since}&t={TOKEN}")
        self.r = self.c.getresponse()
        self.buf = b""
        self.seq = since

    def until(self, pred, timeout=15):
        got = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            while b"\n\n" in self.buf:
                chunk, self.buf = self.buf.split(b"\n\n", 1)
                for line in chunk.split(b"\n"):
                    if line.startswith(b"data: "):
                        e = json.loads(line[6:])
                        if e.get("type") != "stream_event":
                            self.seq += 1
                        got.append(e)
                        if pred(e):
                            return got
            more = self.r.read1(65536)
            if not more:
                raise AssertionError(f"stream fechou; vi {[g.get('type') for g in got]}")
            self.buf += more
        raise AssertionError(f"timeout; vi {[g.get('type') for g in got]}")

    def close(self):
        self.c.close()


# ---------------------------------------------------------------- segurança e estáticos
def test_requires_token_and_local_host(server):
    port, _, _ = server
    assert req(port, "GET", "/api/state", token="errado")[0] == 403
    assert req(port, "GET", "/api/state", host="evil.example")[0] == 403
    assert req(port, "POST", "/api/agent/x/send", {"text": "a"}, token="")[0] == 403
    status, body, r = req(port, "GET", "/api/state")
    assert status == 200 and body["accounts"] == ["gmail"] and body["usage"]["gmail"]["7d"] == 10
    assert "script-src 'self' https://cdnjs.cloudflare.com" in r.getheader("Content-Security-Policy")


def test_static_traversal_is_blocked(server):
    port, _, _ = server
    assert req(port, "GET", "/static/../pyproject.toml")[0] == 404
    assert req(port, "GET", "/static/nao-existe.js")[0] == 404


def test_projects_endpoint(server):
    port, _, _ = server
    status, body, _ = req(port, "GET", "/api/projects")
    assert status == 200 and body["items"][0]["cwd"] == "/p"


# ---------------------------------------------------------------- abrir agente
def test_open_agent_assigns_account_and_spawns(server):
    port, ext, state = server
    status, body, _ = req(
        port, "POST", "/api/agent/open", {"host": "laptop", "cwd": "/novo/x", "mode": "new"}
    )
    assert status == 200 and body["label"] == "x@laptop" and body["acc"] == "gmail"
    assert ext.spawned == [("laptop", "x@laptop", "/novo/x", "gmail", "new")]
    assert any(a["label"] == "x@laptop" and a["state"] == "starting" for a in state.snapshot()["agents"])
    # mesmo nome de pasta de novo ganha sufixo
    status, body, _ = req(port, "POST", "/api/agent/open", {"host": "laptop", "cwd": "/outro/x"})
    assert body["label"] == "x-2@laptop"


def test_open_agent_errors(server):
    port, ext, _ = server
    assert req(port, "POST", "/api/agent/open", {"host": "nope", "cwd": "/x"})[0] == 400
    status, body, _ = req(port, "POST", "/api/agent/open", {"host": "laptop", "cwd": "/falha"})
    assert status == 500 and body["err"] == "falhou de propósito"
    assert req(port, "POST", "/api/agent/x/send", {"text": "   "})[0] == 400
    assert req(port, "POST", "/api/agent/x/nada", {})[0] == 404


# ---------------------------------------------------------------- chat de ponta a ponta
def test_stream_send_permission_interrupt_stop(server, agent):
    port, _, _ = server
    s = SSE(port, "proj%40laptop")
    got = s.until(lambda e: e.get("subtype") == "init")
    assert got[0]["type"] == "orq"

    assert req(port, "POST", "/api/agent/proj%40laptop/send", {"text": "olá"})[0] == 200
    got = s.until(lambda e: e.get("type") == "result")
    echo = next(e for e in got if e["type"] == "user")
    assert echo["orq_origin"] == "page"
    assert any(e["type"] == "stream_event" for e in got)

    req(port, "POST", "/api/agent/proj%40laptop/send", {"text": "use a ferramenta"})
    perm = s.until(lambda e: e.get("type") == "control_request")[-1]
    status, _, _ = req(
        port,
        "POST",
        "/api/agent/proj%40laptop/permission",
        {"request_id": perm["request_id"], "allow": True, "input": perm["request"]["input"]},
    )
    assert status == 200
    got = s.until(lambda e: e.get("type") == "result")
    assert any(e.get("subtype") == "permission_answered" and e["behavior"] == "allow" for e in got)

    req(port, "POST", "/api/agent/proj%40laptop/send", {"text": "demore"})
    s.until(lambda e: e.get("type") == "assistant")
    assert req(port, "POST", "/api/agent/proj%40laptop/interrupt")[0] == 200
    got = s.until(lambda e: e.get("type") == "result")
    assert got[-1]["is_error"] is True

    status, body, _ = req(port, "POST", "/api/agent/proj%40laptop/stop")
    assert status == 200 and "encerrando" in body["msg"]
    assert agent.wait(10)
    s.close()


def test_reconnect_stream_with_since(server, agent):
    port, _, _ = server
    s = SSE(port, "proj%40laptop")
    s.until(lambda e: e.get("subtype") == "init")
    seq = s.seq
    req(port, "POST", "/api/agent/proj%40laptop/send", {"text": "perdida"})
    s.close()
    time.sleep(1.0)
    s2 = SSE(port, "proj%40laptop", since=seq)
    got = s2.until(lambda e: e.get("type") == "result")
    assert got[0]["type"] == "user" and got[0]["message"]["content"] == "perdida"
    s2.close()


def test_deliver_without_open_tab_uses_temporary_bridge(server, agent):
    port, _, _ = server
    assert req(port, "POST", "/api/agent/proj%40laptop/send", {"text": "sem aba"})[0] == 200
    time.sleep(1.5)
    s = SSE(port, "proj%40laptop")
    got = s.until(lambda e: e.get("type") == "result")
    assert any(e["type"] == "user" and e["message"]["content"] == "sem aba" for e in got)
    s.close()


def test_send_to_missing_agent_fails_cleanly(server):
    port, _, _ = server
    status, body, _ = req(port, "POST", "/api/agent/nada%40laptop/send", {"text": "oi"})
    assert status in (200, 500)  # a ponte passageira pode aceitar a escrita antes de o attach morrer
    if status == 500:
        assert "agente" in body["err"] or "falar" in body["err"]


# ---------------------------------------------------------------- build_message
def test_build_message_shapes():
    assert H.build_message("send", {"text": " oi "}) == {
        "type": "user",
        "message": {"role": "user", "content": "oi"},
    }
    assert H.build_message("send", {"text": ""}) is None
    m = H.build_message("permission", {"request_id": "r", "allow": True, "input": {"a": 1}})
    assert m["response"]["response"] == {"behavior": "allow", "updatedInput": {"a": 1}}
    m = H.build_message("permission", {"request_id": "r", "allow": False})
    assert m["response"]["response"]["behavior"] == "deny"
    assert H.build_message("permission", {"allow": True}) is None
    assert H.build_message("interrupt", {})["request"] == {"subtype": "interrupt"}
    assert H.build_message("stop", {}) == {"orq": "quit"}
    assert H.build_message("x", {}) is None


# ---------------------------------------------------------------- página e estáticos
def test_index_and_static_are_served_with_types(server):
    port, _, _ = server
    status, body, r = req(port, "GET", "/")
    assert status == 200 and r.getheader("Content-Type").startswith("text/html")
    assert b'<script type="module" src="/static/js/app.js">' in body
    for path, ctype in (("/static/app.css", "text/css"), ("/static/js/app.js", "application/javascript")):
        status, _, r = req(port, "GET", path)
        assert status == 200 and r.getheader("Content-Type").startswith(ctype), path
    assert req(port, "GET", "/static/js/events.js")[0] == 200


# ---------------------------------------------------------------- converter claude de terminal
def test_convert_endpoint(server):
    port, ext, state = server
    calls = []
    # injeta um claude de terminal no cache e um conversor falso no contexto
    state.add_provisional(
        {
            "label": "kaggle@server0",
            "host": "server0",
            "account": "gmail",
            "dir": "/k",
            "state": "waiting",
            "tmux": "claude-gmail-kaggle",
        }
    )
    srv_ctx = server_ctx(port)
    srv_ctx.conf.hosts.append("server0")
    srv_ctx.convert = lambda host, label: (
        calls.append((host, label)) or {"ok": True, "label": label, "how": "exit", "msg": "aberto"}
    )
    status, body, _ = req(port, "POST", "/api/agent/convert", {"label": "kaggle@server0"})
    assert status == 200 and body["label"] == "kaggle@server0" and body["how"] == "exit"
    assert calls == [("server0", "kaggle@server0")]
    assert any(
        a["label"] == "kaggle@server0" and a.get("kind") == "sdk" for a in state.snapshot()["agents"]
    )
    # agente da página não é convertível; máquina desconhecida é inválida; falha vira 500
    assert req(port, "POST", "/api/agent/convert", {"label": "proj@laptop"})[0] in (404, 400)
    assert req(port, "POST", "/api/agent/convert", {"label": "x@nope"})[0] == 400
    srv_ctx.convert = lambda host, label: {"ok": False, "msg": "não fechou"}
    state.add_provisional(
        {
            "label": "y@server0",
            "host": "server0",
            "account": "gmail",
            "dir": "/y",
            "state": "waiting",
            "tmux": "t",
        }
    )
    status, body, _ = req(port, "POST", "/api/agent/convert", {"label": "y@server0"})
    assert status == 500 and body["err"] == "não fechou"
