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

    def usage(self, host=None):
        return {"gmail": {"5h": 1, "7d": 10, "7d_pace": 50, "7d_left": 100}}

    def all_agents(self, hosts):
        return [], {}

    def all_projects(self, hosts, limit=30):
        return [{"cwd": "/p", "host": "laptop", "mtime": 1, "account": "gmail", "title": "t"}], {}

    def spawn(self, host, label, cwd, account, mode, role=None, parent=None, level=None, restart=False):
        self.spawned.append((host, label, cwd, account, mode, role, parent, level))
        self.restarts = getattr(self, "restarts", []) + ([label] if restart else [])
        if cwd == "/falha":
            return "falhou de propósito"
        if self.real is not None:  # sobe um supervisor de verdade com o claude falso (níveis)
            name = config.session_name(label)
            cmd = [sys.executable, str(FAKE), "-n", name] + (["--append-system-prompt", role] if role else [])
            s = Supervisor(
                label=label,
                folder=Path(cwd),
                account=account,
                command=cmd,
                env=dict(os.environ, FAKE_PEER_DIR=self.real["peers"]),
                state_dir=config.state_dir(label),
                log=lambda *_: None,
                name=name,
                parent=parent,
                level=level,
            )
            s.start()
            self.real.setdefault("sups", []).append(s)
        return None

    real = None


@pytest.fixture
def short_tmp():
    d = Path(tempfile.mkdtemp(prefix="orqh", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def agent(short_tmp, monkeypatch):
    """Supervisor local com claude falso; ORQ_STATE_DIR aponta para ele, como o attach espera."""
    monkeypatch.setenv("ORQ_STATE_DIR", str(short_tmp))
    peers = short_tmp / "peers"
    peers.mkdir()
    s = Supervisor(
        label="proj@laptop",
        folder=short_tmp / "proj",
        account="gmail",
        command=[sys.executable, str(FAKE), "-n", "proj-laptop"],
        env=dict(os.environ, FAKE_PEER_DIR=str(peers)),
        state_dir=config.state_dir("proj@laptop"),
        log=lambda *_: None,
        name="proj-laptop",
    )
    s.start()
    s.peers = peers
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
    ctx = H.Context(conf, state, BridgeRegistry(factory=bridge_factory), ext.spawn, TOKEN, convert=None)
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
    assert ext.spawned == [("laptop", "x@laptop", "/novo/x", "gmail", "new", None, None, None)]
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


def test_context_accepts_convert_like_app_does():
    """app.serve monta Context(..., convert=ext.convert); o construtor precisa aceitar."""
    conf = config.parse({"accounts": []})
    ctx = H.Context(
        conf,
        StateCache(conf, FakeExt()),
        BridgeRegistry(),
        lambda *a: None,
        "t",
        convert=lambda host, label: {"ok": True},
    )
    assert ctx.convert("h", "l") == {"ok": True}
    assert H.Context(conf, StateCache(conf, FakeExt()), BridgeRegistry(), lambda *a: None).convert is None


def test_open_refuses_folder_with_live_terminal_claude_and_convert_needs_tmux(server):
    port, ext, state = server
    state.add_provisional(
        {
            "label": "p@laptop",
            "host": "laptop",
            "account": "gmail",
            "dir": "/p",
            "state": "waiting",
            "tmux": "claude-gmail-p",
        }
    )
    status, body, _ = req(port, "POST", "/api/agent/open", {"host": "laptop", "cwd": "/p", "mode": "resume"})
    assert status == 409 and "converter" in body["err"]
    assert req(port, "POST", "/api/agent/open", {"host": "laptop", "cwd": "/p", "mode": "new"})[0] == 200, (
        "conversa nova pode"
    )
    state.add_provisional(
        {"label": "grade@laptop", "host": "laptop", "account": "gmail", "dir": "/g", "state": "menu"}
    )
    srv_ctx = server_ctx(port)
    srv_ctx.convert = lambda host, label: {"ok": True, "label": label}
    status, body, _ = req(port, "POST", "/api/agent/convert", {"label": "grade@laptop"})
    assert status == 400 and "grade" in body["err"]


# ---------------------------------------------------------------- upload para a _inbox
def test_upload_writes_into_agent_inbox_and_validates(server, short_tmp):
    port, ext, state = server
    puts = []
    srv_ctx = server_ctx(port)
    srv_ctx.put_file = lambda host, path, data: puts.append((host, path, data)) or None
    state.add_provisional(
        {
            "label": "k@server0",
            "host": "server0",
            "account": "gmail",
            "dir": "/home/c/k",
            "state": "waiting",
            "kind": "sdk",
        }
    )
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(
        "POST",
        "/api/agent/k%40server0/upload",
        body=b"%PDF-1.4 conteudo",
        headers={
            "X-Orq-Token": TOKEN,
            "X-Filename": "../../relat%C3%B3rio.pdf",
            "Content-Type": "application/octet-stream",
        },
    )
    r = c.getresponse()
    body = json.loads(r.read())
    assert r.status == 200 and body["path"] == "/home/c/k/_inbox/relatório.pdf" and body["bytes"] == 17
    assert puts == [("server0", "/home/c/k/_inbox/relatório.pdf", b"%PDF-1.4 conteudo")]
    # nome vazio, agente desconhecido, corpo vazio
    for headers, expected in (
        ({"X-Filename": "..%2F"}, 400),
        ({"X-Filename": "a.txt"}, 404),
    ):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        path = "/api/agent/nada%40laptop/upload" if expected == 404 else "/api/agent/k%40server0/upload"
        c.request("POST", path, body=b"x", headers={"X-Orq-Token": TOKEN, **headers})
        assert c.getresponse().status == expected, headers
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(
        "POST",
        "/api/agent/k%40server0/upload",
        body=b"",
        headers={"X-Orq-Token": TOKEN, "X-Filename": "a.txt"},
    )
    assert c.getresponse().status == 400
    # erro do put_file vira 500 com a máquina no texto
    srv_ctx.put_file = lambda host, path, data: "disco cheio"
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(
        "POST",
        "/api/agent/k%40server0/upload",
        body=b"x",
        headers={"X-Orq-Token": TOKEN, "X-Filename": "a.txt"},
    )
    r = c.getresponse()
    assert r.status == 500 and "server0" in json.loads(r.read())["err"]


def test_safe_filename():
    assert H.safe_filename("relatório final.pdf") == "relatório final.pdf"
    assert H.safe_filename("/etc/passwd") == "passwd"
    assert H.safe_filename("..\\..\\x.txt") == "x.txt"
    assert H.safe_filename("..") == "" and H.safe_filename("") == ""
    assert H.safe_filename('a<b>:"|?*.txt') == "ab.txt"
    assert len(H.safe_filename("x" * 300)) == 200


def test_put_file_local(short_tmp):
    from orqweb.server.external import External

    ext = External(Path("/b"))
    assert ext.put_file("laptop", str(short_tmp / "proj" / "_inbox" / "a.bin"), b"\x00\x01") is None
    assert (short_tmp / "proj" / "_inbox" / "a.bin").read_bytes() == b"\x00\x01"


# ---------------------------------------------------------------- níveis
def base_entry(label="proj@laptop", cwd="/p", **extra):
    a = H.provisional_agent(label, "laptop", "gmail", cwd, time.time(), **extra)
    a["state"] = "waiting"
    return a


def test_pipelines_endpoint(server):
    port, _, _ = server
    status, body, _ = req(port, "GET", "/api/pipelines")
    assert status == 200 and body["default"] == "professor-executor"
    ids = [p["id"] for p in body["items"]]
    assert "professor-executor" in ids and "professor-arquiteto-executor" in ids
    prof = next(p for p in body["items"] if p["id"] == "professor-executor")
    assert prof["niveis"] == ["professor"] and prof["titulo"]


def test_upgrade_refusals(server):
    port, ext, state = server
    assert req(port, "POST", "/api/agent/x@laptop/upgrade", {})[0] == 404
    term = {
        "label": "t@laptop",
        "host": "laptop",
        "kind": "term",
        "tmux": "s",
        "state": "waiting",
        "dir": "/p",
    }
    state.add_provisional(term)
    status, body, _ = req(port, "POST", "/api/agent/t@laptop/upgrade", {})
    assert status == 400 and "terminal" in body["err"]
    old = base_entry("velho@laptop")
    del old["name"]  # agente aberto antes desta versão: subiu sem -n
    state.add_provisional(old)
    status, body, _ = req(port, "POST", "/api/agent/velho@laptop/upgrade", {})
    assert status == 409 and "reabra" in body["err"]
    state.add_provisional(base_entry())
    status, body, _ = req(port, "POST", "/api/agent/proj@laptop/upgrade", {"pipeline": "nao-existe"})
    assert status == 400 and "desconhecida" in body["err"]
    assert ext.spawned == []


def test_upgrade_spawns_levels_bottom_up_and_downgrade_needs_them(server):
    port, ext, state = server
    state.add_provisional(base_entry())
    status, body, _ = req(port, "POST", "/api/agent/proj@laptop/downgrade", {})
    assert status == 400
    status, body, _ = req(
        port, "POST", "/api/agent/proj@laptop/upgrade", {"pipeline": "professor-arquiteto-executor"}
    )
    assert status == 200, body
    assert (
        body["levels"] == ["proj~arquiteto@laptop", "proj~professor@laptop"]
        and body["top"] == "proj~professor@laptop"
    )
    assert [s[1] for s in ext.spawned] == body["levels"]
    host, label, cwd, acc, mode, role, parent, level = ext.spawned[0]
    assert (host, cwd, acc, mode, parent, level) == (
        "laptop",
        "/p",
        "gmail",
        "new",
        "proj@laptop",
        "arquiteto",
    )
    assert "`proj-laptop`" in role and "`proj-professor-laptop`" in role
    assert ext.spawned[1][6] == "proj~arquiteto@laptop" and ext.spawned[1][7] == "professor"
    snap = req(port, "GET", "/api/state")[1]
    levels = {a["label"]: a for a in snap["agents"] if a.get("parent")}
    assert levels["proj~professor@laptop"]["parent"] == "proj~arquiteto@laptop"
    assert (
        levels["proj~arquiteto@laptop"]["level"] == "arquiteto"
        and levels["proj~arquiteto@laptop"]["name"] == "proj-arquiteto-laptop"
    )
    # de novo: já tem níveis; e um nível não ganha níveis
    assert req(port, "POST", "/api/agent/proj@laptop/upgrade", {})[0] == 409
    status, body, _ = req(port, "POST", "/api/agent/proj~professor@laptop/upgrade", {})
    assert status == 400 and "nível de cima" in body["err"]


def test_upgrade_failure_in_second_level_rolls_back_first(server, monkeypatch):
    port, ext, state = server
    state.add_provisional(base_entry())
    calls = []
    ext.spawn = lambda host, label, cwd, acc, mode, **kw: (
        calls.append(label) or ("quebrou" if len(calls) > 1 else None)
    )
    server_ctx(port).spawn = ext.spawn
    quits = []
    server_ctx(port).bridges.deliver = lambda label, obj, linger=1.5: quits.append((label, obj)) or None
    status, body, _ = req(
        port, "POST", "/api/agent/proj@laptop/upgrade", {"pipeline": "professor-arquiteto-executor"}
    )
    assert status == 500 and "proj~professor@laptop" in body["err"]
    assert quits == [("proj~arquiteto@laptop", {"orq": "quit"})]


def test_levels_end_to_end_through_http(server, agent):
    """Upgrade sobe um professor falso de verdade; a página fala com ele, ele delega ao agente da aba,
    os fios aparecem nos dois streams; downgrade encerra o nível."""
    port, ext, state = server
    ext.real = {"peers": str(agent.peers)}
    state.add_provisional(base_entry(cwd=str(agent.folder)))
    try:
        status, body, _ = req(port, "POST", "/api/agent/proj@laptop/upgrade", {})
        assert status == 200, body
        top = body["top"]
        assert top == "proj~professor@laptop"
        sock = config.state_dir(top) / "agent.sock"
        deadline = time.time() + 10
        while not sock.exists() and time.time() < deadline:
            time.sleep(0.1)
        prof = SSE(port, top)
        init = prof.until(lambda e: e.get("subtype") == "init")[-1]
        assert init["fake_name"] == "proj-professor-laptop" and "proj-laptop" in init["fake_role"]
        base = SSE(port, "proj@laptop")
        base.until(lambda e: e.get("subtype") == "init")
        assert (
            req(port, "POST", f"/api/agent/{top}/send", {"text": "delegue: responda PONG para proj-laptop"})[
                0
            ]
            == 200
        )
        got = prof.until(
            lambda e: e.get("type") == "assistant" and e["message"]["content"][0].get("name") == "SendMessage"
        )
        assert got[-1]["message"]["content"][0]["input"]["to"] == "proj-laptop"
        got = base.until(lambda e: e.get("orq_origin") == "agent")
        assert (
            got[-1]["origin"]["name"] == "proj-professor-laptop"
            and got[-1]["origin"]["body"] == "responda PONG"
        )
        got = prof.until(lambda e: e.get("orq_origin") == "agent")
        assert got[-1]["origin"]["body"] == "PONG"
        prof.close()
        base.close()
        status, body, _ = req(port, "POST", "/api/agent/proj@laptop/downgrade", {})
        assert status == 200 and body["stopped"] == [top] and body["errors"] == {}
        assert ext.real["sups"][0].wait(10)
        assert not sock.exists()
        snap = req(port, "GET", "/api/state")[1]
        assert next(a for a in snap["agents"] if a["label"] == top)["state"] == "closed"
    finally:
        for s in ext.real.get("sups", []):
            s.quit()
            s.wait(10)


def test_reopen_restarts_in_same_conversation_or_other_account(server):
    port, ext, state = server
    assert req(port, "POST", "/api/agent/x@laptop/reopen", {})[0] == 404
    old = base_entry("velho@laptop")
    del old["name"]
    state.add_provisional(old)
    status, body, _ = req(port, "POST", "/api/agent/velho@laptop/reopen", {})
    assert status == 200 and body == {"label": "velho@laptop", "acc": "gmail"}, body
    assert ext.spawned[-1][:5] == ("laptop", "velho@laptop", "/p", "gmail", "resume") and ext.restarts == [
        "velho@laptop"
    ]
    assert (
        next(a for a in state.snapshot()["agents"] if a["label"] == "velho@laptop")["name"] == "velho-laptop"
    )
    assert req(port, "POST", "/api/agent/velho@laptop/reopen", {"account": "nao-existe"})[0] == 400
    # com níveis em cima, recusa (os níveis apontam para o pid antigo)
    state.add_provisional(base_entry("velho~professor@laptop", parent="velho@laptop", level="professor"))
    assert req(port, "POST", "/api/agent/velho@laptop/reopen", {})[0] == 409


# ---------------------------------------------------------------- arquivos da _outbox
def test_files_and_file_routes(server, short_tmp):
    from orqweb.server.external import External

    port, ext, state = server
    srv_ctx = server_ctx(port)
    out = short_tmp / "k" / "_outbox"
    out.mkdir(parents=True)
    (out / "rel.pdf").write_bytes(b"%PDF-1.4 0123456789")
    (out / "pag.html").write_bytes(b"<script>alert(1)</script>")
    real = External(short_tmp, local_host="laptop")
    srv_ctx.list_files = lambda host, folder: (
        real.list_files("laptop", folder) if host == "laptop" else ([], f"sem {host}")
    )
    srv_ctx.open_file = lambda host, path, start, end: real.open_file("laptop", path, start, end)
    state.add_provisional(
        {
            "label": "k@laptop",
            "host": "laptop",
            "account": "gmail",
            "dir": str(short_tmp / "k"),
            "state": "waiting",
            "kind": "sdk",
        }
    )
    status, body, _ = req(port, "GET", "/api/agent/k%40laptop/files")
    assert status == 200 and body["dir"] == str(out)
    assert {f["name"] for f in body["items"]} == {"rel.pdf", "pag.html"} and "err" not in body
    assert req(port, "GET", "/api/agent/nada%40laptop/files")[0] == 404
    # erro da máquina vai junto da lista vazia (a página mostra o texto)
    state.add_provisional(
        {
            "label": "r@server0",
            "host": "server0",
            "account": "gmail",
            "dir": "/h/r",
            "state": "waiting",
            "kind": "sdk",
        }
    )
    status, body, _ = req(port, "GET", "/api/agent/r%40server0/files")
    assert status == 200 and body["items"] == [] and body["err"] == "sem server0"

    def get(path, **headers):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path, headers={"X-Orq-Token": TOKEN, **headers})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    # inteiro, inline, com o tipo certo e CSP que não deixa nada rodar
    r, data = get("/api/agent/k%40laptop/file?name=rel.pdf")
    assert (
        r.status == 200
        and data == b"%PDF-1.4 0123456789"
        and r.getheader("Content-Type") == "application/pdf"
    )
    assert r.getheader("Content-Length") == "19" and r.getheader("Accept-Ranges") == "bytes"
    assert r.getheader("Content-Disposition") == "inline; filename*=UTF-8''rel.pdf"
    assert (
        "sandbox" in r.getheader("Content-Security-Policy")
        and r.getheader("X-Content-Type-Options") == "nosniff"
    )
    # ?dl=1 vira anexo; html nunca é inline (iria rodar na nossa origem)
    r, _ = get("/api/agent/k%40laptop/file?name=rel.pdf&dl=1")
    assert r.getheader("Content-Disposition").startswith("attachment;")
    r, data = get("/api/agent/k%40laptop/file?name=pag.html")
    assert (
        r.status == 200
        and r.getheader("Content-Disposition").startswith("attachment;")
        and data.startswith(b"<script>")
    )
    # faixa (o player de vídeo pede assim)
    r, data = get("/api/agent/k%40laptop/file?name=rel.pdf", Range="bytes=9-12")
    assert r.status == 206 and data == b"0123" and r.getheader("Content-Range") == "bytes 9-12/19"
    r, data = get("/api/agent/k%40laptop/file?name=rel.pdf", Range="bytes=15-")
    assert r.status == 206 and data == b"6789" and r.getheader("Content-Range") == "bytes 15-18/19"
    r, _ = get("/api/agent/k%40laptop/file?name=rel.pdf", Range="bytes=99-")
    assert r.status == 416
    # nome com caminho, arquivo que não existe, agente desconhecido, desligado
    assert get("/api/agent/k%40laptop/file?name=..%2Frel.pdf")[0].status == 400
    assert get("/api/agent/k%40laptop/file?name=")[0].status == 400
    r, data = get("/api/agent/k%40laptop/file?name=nada.pdf")
    assert r.status == 404 and "laptop" in json.loads(data)["err"]
    assert get("/api/agent/x%40laptop/file?name=rel.pdf")[0].status == 404
    srv_ctx.open_file = None
    assert get("/api/agent/k%40laptop/file?name=rel.pdf")[0].status == 400
    assert get("/api/agent/k%40laptop/files")[0].status == 200
    srv_ctx.list_files = None
    assert get("/api/agent/k%40laptop/files")[0].status == 400
