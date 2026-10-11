"""Integração do supervisor com um claude falso (tests/fake_claude.py): protocolo real, sem Claude."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from orqweb.agent import client
from orqweb.agent.state import StateFile
from orqweb.agent.supervisor import Supervisor, socket_alive

FAKE = Path(__file__).with_name("fake_claude.py")


class Conn:
    """Cliente de teste: lê eventos do socket com timeout; conta a sequência como a página faz."""

    def __init__(self, sock_path, since=0):
        self.c = client.connect(sock_path, since, timeout=10)
        self.f = self.c.makefile("rb")
        self.seq = since

    def send(self, obj):
        client.send(self.c, obj)

    def user(self, text):
        self.send({"type": "user", "message": {"role": "user", "content": text}})

    def allow(self, request_id, inp=None):
        self.send(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": {"behavior": "allow", "updatedInput": inp or {}},
                },
            }
        )

    def deny(self, request_id):
        self.send(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": {"behavior": "deny", "message": "não"},
                },
            }
        )

    def until(self, pred, timeout=10):
        """Lê até pred(evento) ser verdadeiro; devolve a lista de eventos lidos (o último satisfaz pred)."""
        got = []
        deadline = time.time() + timeout
        self.c.settimeout(timeout)
        while time.time() < deadline:
            line = self.f.readline()
            if not line:
                raise AssertionError(f"conexão fechou antes de: {got}")
            e = json.loads(line)
            if e.get("type") != "stream_event":
                self.seq += 1
            got.append(e)
            if pred(e):
                return got
        raise AssertionError(f"timeout esperando evento; vi {[g.get('type') for g in got]}")

    def close(self):
        self.c.close()


def is_result(e):
    return e.get("type") == "result"


def is_perm(e):
    return e.get("type") == "control_request" and e["request"]["subtype"] == "can_use_tool"


@pytest.fixture
def short_tmp():
    """Pasta curta: o caminho de um socket Unix tem limite de ~100 bytes; o tmp_path do pytest passa disso."""
    d = Path(tempfile.mkdtemp(prefix="orqt", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sup(short_tmp):
    tmp_path = short_tmp
    folder = tmp_path / "proj"
    s = Supervisor(
        label="proj@laptop",
        folder=folder,
        account="gmail",
        command=[sys.executable, str(FAKE)],
        env=dict(os.environ),
        state_dir=tmp_path / "state" / "proj-laptop",
        log=lambda *_: None,
    )
    s.start()
    assert socket_alive(s.sock_path)
    yield s
    s.quit()
    s.wait(10)


def state_of(s):
    return StateFile(s.dir / "state.json").read()


def test_replay_from_start_has_banner_and_init(sup):
    c = Conn(sup.sock_path)
    got = c.until(lambda e: e.get("type") == "system" and e.get("subtype") == "init")
    assert got[0]["type"] == "orq" and "proj@laptop" in got[0]["text"] and "conversa nova" in got[0]["text"]
    assert state_of(sup)["state"] == "waiting"
    assert state_of(sup)["kind"] == "sdk" and state_of(sup)["sid"] == "fake-sid-0001"
    c.close()


def test_message_echo_is_stamped_page_and_streams(sup):
    c = Conn(sup.sock_path)
    c.until(lambda e: e.get("subtype") == "init")
    c.user("olá mundo")
    got = c.until(is_result)
    types = [e["type"] for e in got]
    assert "stream_event" in types, "parciais devem passar ao vivo"
    echo = next(e for e in got if e["type"] == "user")
    assert echo["orq_origin"] == "page" and echo["message"]["content"] == "olá mundo"
    text = next(e for e in got if e["type"] == "assistant")["message"]["content"][0]["text"]
    assert text == "eco: olá mundo"
    assert state_of(sup)["state"] == "waiting"
    # parciais não entram na sequência persistida
    assert len(sup.events) == c.seq
    c.close()


def test_permission_allow_and_answer_is_recorded(sup):
    c = Conn(sup.sock_path)
    c.until(lambda e: e.get("subtype") == "init")
    c.user("use a ferramenta")
    req = c.until(is_perm)[-1]
    assert req["request"]["tool_name"] == "Bash"
    assert state_of(sup)["state"] == "permission" and state_of(sup)["msg"] == "Bash echo oi"
    c.allow(req["request_id"], req["request"]["input"])
    got = c.until(is_result)
    answered = next(e for e in got if e.get("subtype") == "permission_answered")
    assert answered["request_id"] == req["request_id"] and answered["behavior"] == "allow"
    assert any(
        b.get("type") == "tool_result"
        for e in got
        if e["type"] == "user"
        for b in e["message"]["content"]
        if isinstance(b, dict)
    )
    assert (
        next(e for e in got if e["type"] == "assistant" and e["message"]["content"][0].get("type") == "text")[
            "message"
        ]["content"][0]["text"]
        == "feito"
    )
    c.close()


def test_permission_deny(sup):
    c = Conn(sup.sock_path)
    c.until(lambda e: e.get("subtype") == "init")
    c.user("use a ferramenta")
    req = c.until(is_perm)[-1]
    c.deny(req["request_id"])
    got = c.until(is_result)
    assert (
        "negado"
        in next(
            e for e in got if e["type"] == "assistant" and e["message"]["content"][0].get("type") == "text"
        )["message"]["content"][0]["text"]
    )
    c.close()


def test_ask_user_question_answers_travel_in_updated_input(sup):
    c = Conn(sup.sock_path)
    c.until(lambda e: e.get("subtype") == "init")
    c.user("pergunte algo")
    req = c.until(is_perm)[-1]
    assert req["request"]["tool_name"] == "AskUserQuestion"
    inp = dict(req["request"]["input"], answers={"Qual fruta?": "uva"})
    c.allow(req["request_id"], inp)
    got = c.until(is_result)
    assert "você escolheu uva" in json.dumps(got, ensure_ascii=False)
    c.close()


def test_interrupt(sup):
    c = Conn(sup.sock_path)
    c.until(lambda e: e.get("subtype") == "init")
    c.user("demore bastante")
    c.until(lambda e: e.get("type") == "assistant")
    t0 = time.time()
    c.send({"type": "control_request", "request_id": "int-1", "request": {"subtype": "interrupt"}})
    got = c.until(is_result)
    assert got[-1]["is_error"] is True and time.time() - t0 < 4
    assert state_of(sup)["state"] == "error"
    c.close()


def test_reconnect_with_since_gets_missed_events(sup):
    c = Conn(sup.sock_path)
    c.until(lambda e: e.get("subtype") == "init")
    seq = c.seq
    c.user("mensagem perdida")
    c.close()  # cai antes de receber a resposta
    time.sleep(1.0)
    c2 = Conn(sup.sock_path, since=seq)
    got = c2.until(is_result)
    kinds = [e["type"] for e in got]
    assert kinds[0] == "user" and "assistant" in kinds and kinds[-1] == "result"
    assert "stream_event" not in kinds, "replay não traz parciais"
    assert c2.seq == len(sup.events)
    c2.close()


def test_injected_message_is_stamped_injected(sup):
    c = Conn(sup.sock_path)
    c.until(lambda e: e.get("subtype") == "init")
    c.user("injete um aviso")
    got = c.until(is_result)
    users = [e for e in got if e["type"] == "user"]
    assert users[0]["orq_origin"] == "page"
    assert users[1]["orq_origin"] == "injected" and users[1]["message"]["content"].startswith(
        "Aviso automático"
    )
    c.close()


def test_transcript_replay_on_resume(short_tmp):
    tmp_path = short_tmp
    transcript = tmp_path / "old.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "antiga"}, "origin": {"kind": "human"}},
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "resposta antiga"}]},
        },
    ]
    transcript.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    s = Supervisor(
        label="r@laptop",
        folder=tmp_path / "p",
        account="gmail",
        command=[sys.executable, str(FAKE)],
        env=dict(os.environ),
        state_dir=tmp_path / "st",
        sid="abcdef12-0000",
        transcript=transcript,
        log=lambda *_: None,
    )
    s.start()
    try:
        c = Conn(s.sock_path)
        got = c.until(lambda e: e.get("subtype") == "init")
        assert "retomando abcdef12" in got[0]["text"]
        replays = [e for e in got if e.get("replay")]
        assert [e["type"] for e in replays] == ["user", "assistant"]
        assert replays[0]["origin"] == {"kind": "human"}
        c.close()
    finally:
        s.quit()
        s.wait(10)


def test_quit_closes_everything(sup):
    c = Conn(sup.sock_path)
    c.until(lambda e: e.get("subtype") == "init")
    c.send({"orq": "quit"})
    got = c.until(lambda e: e.get("type") == "orq" and "encerrado" in e.get("text", ""))
    assert got[-1]["level"] == "info"
    assert sup.wait(10)
    assert state_of(sup)["state"] == "closed"
    assert not sup.sock_path.exists()
    assert sup.proc.returncode == 0


def test_second_supervisor_on_same_socket_is_refused(sup, short_tmp):
    tmp_path = short_tmp
    other = Supervisor(
        label="proj@laptop",
        folder=tmp_path / "proj",
        account="gmail",
        command=[sys.executable, str(FAKE)],
        env=dict(os.environ),
        state_dir=sup.dir,
        log=lambda *_: None,
    )
    with pytest.raises(RuntimeError, match="já existe"):
        other.start()


def test_attach_cli_bridges_stdin_and_stdout(sup, tmp_path):
    env = dict(os.environ, ORQ_STATE_DIR=str(sup.dir.parent))
    p = subprocess.Popen(
        [sys.executable, "-m", "orqweb.agent.cli", "attach", "proj@laptop", "--since", "0"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env,
    )
    try:
        p.stdin.write(
            (
                json.dumps({"type": "user", "message": {"role": "user", "content": "via attach"}}) + "\n"
            ).encode()
        )
        p.stdin.flush()
        seen = []
        deadline = time.time() + 10
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                break
            e = json.loads(line)
            seen.append(e.get("type"))
            if e.get("type") == "result":
                break
        assert "user" in seen and "assistant" in seen and seen[-1] == "result"
    finally:
        p.kill()


def test_attach_without_agent_reports_error(tmp_path):
    env = dict(os.environ, ORQ_STATE_DIR=str(tmp_path))
    r = subprocess.run(
        [sys.executable, "-m", "orqweb.agent.cli", "attach", "nada@laptop"],
        capture_output=True,
        env=env,
        timeout=20,
    )
    assert r.returncode == 1
    assert json.loads(r.stdout)["level"] == "error"


def test_stop_via_client(sup):
    assert client.stop(sup.sock_path) is True
    assert sup.wait(10)
    assert client.stop(sup.sock_path) is False


def test_socket_alive_false_for_stale_file(short_tmp):
    tmp_path = short_tmp
    p = tmp_path / "agent.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(p))
    s.close()  # arquivo fica, ninguém atende
    assert socket_alive(p) is False


def test_concurrent_clients_both_receive(sup):
    a, b = Conn(sup.sock_path), Conn(sup.sock_path)
    for c in (a, b):
        c.until(lambda e: e.get("subtype") == "init")
    a.user("para os dois")
    ra = a.until(is_result)
    rb = b.until(is_result)
    assert [e["type"] for e in ra if e["type"] != "stream_event"] == [
        e["type"] for e in rb if e["type"] != "stream_event"
    ]
    a.close()
    b.close()


def test_handle_client_message_is_threadsafe_under_burst(sup):
    """Várias mensagens em sequência: todas ecoadas na ordem, nenhuma perdida."""
    c = Conn(sup.sock_path)
    c.until(lambda e: e.get("subtype") == "init")
    n = 5
    threading.Thread(target=lambda: [c.user(f"m{i}") for i in range(n)]).start()
    results = 0
    echoes = []
    deadline = time.time() + 20
    while results < n and time.time() < deadline:
        got = c.until(is_result)
        results += 1
        echoes += [
            e["message"]["content"]
            for e in got
            if e["type"] == "user" and isinstance(e["message"]["content"], str)
        ]
    assert sorted(echoes) == [f"m{i}" for i in range(n)]
    c.close()


def test_named_levels_exchange_messages_through_peer_channel(short_tmp):
    """Professor e executor falsos, ambos com -n, pelo supervisor real: o SendMessage de um chega
    no outro como `user` carimbado `agent`, e a resposta volta pelo mesmo caminho."""
    peers = short_tmp / "peers"
    peers.mkdir()
    env = dict(os.environ, FAKE_PEER_DIR=str(peers))
    sups = {}
    for label, extra in (
        ("exe@laptop", {}),
        ("exe~professor@laptop", {"parent": "exe@laptop", "level": "professor"}),
    ):
        name = label.replace("@", "-").replace("~", "-")
        cmd = [sys.executable, str(FAKE), "-n", name]
        if extra:
            cmd += ["--append-system-prompt", "Você é o professor de exe-laptop"]
        s = Supervisor(
            label=label,
            folder=short_tmp / "proj",
            account="gmail",
            command=cmd,
            env=env,
            state_dir=short_tmp / "st" / name,
            log=lambda *_: None,
            name=name,
            **extra,
        )
        s.start()
        sups[label] = s
    try:
        exe, prof = Conn(sups["exe@laptop"].sock_path), Conn(sups["exe~professor@laptop"].sock_path)
        init = prof.until(lambda e: e.get("subtype") == "init")[-1]
        assert init["fake_name"] == "exe-professor-laptop" and init["fake_role"].startswith(
            "Você é o professor"
        )
        exe.until(lambda e: e.get("subtype") == "init")
        st = state_of(sups["exe~professor@laptop"])
        assert (
            st["name"] == "exe-professor-laptop"
            and st["parent"] == "exe@laptop"
            and st["level"] == "professor"
        )
        assert state_of(sups["exe@laptop"])["name"] == "exe-laptop" and "parent" not in state_of(
            sups["exe@laptop"]
        )

        prof.user("delegue: responda PONG para exe-laptop")
        got = prof.until(is_result)
        out = next(
            e
            for e in got
            if e["type"] == "assistant" and e["message"]["content"][0].get("type") == "tool_use"
        )
        assert out["message"]["content"][0]["name"] == "SendMessage"
        assert out["message"]["content"][0]["input"]["to"] == "exe-laptop"
        # no executor: chega como user carimbado agent, com quem mandou e o texto limpo
        got = exe.until(lambda e: e.get("orq_origin") == "agent")
        peer = got[-1]
        assert peer["origin"]["name"] == "exe-professor-laptop" and peer["origin"]["body"] == "responda PONG"
        assert "<cross-session-message" in peer["message"]["content"]
        exe.until(is_result)
        # a resposta volta ao professor pelo mesmo canal
        got = prof.until(lambda e: e.get("orq_origin") == "agent")
        assert got[-1]["origin"]["name"] == "exe-laptop" and got[-1]["origin"]["body"] == "PONG"
        got = prof.until(is_result)
        assert any("o peer disse: PONG" in json.dumps(e, ensure_ascii=False) for e in got)
        exe.close()
        prof.close()
    finally:
        for s in sups.values():
            s.quit()
        for s in sups.values():
            s.wait(10)


def test_tmux_shell_carries_pythonpath():
    from orqweb.agent.client import tmux_shell

    assert tmux_shell(["python3", "-m", "x"], {}) == "python3 -m x"
    assert tmux_shell(["python3", "-m", "x"], {"PYTHONPATH": "/a b"}) == "env 'PYTHONPATH=/a b' python3 -m x"


def test_stop_and_wait_until_socket_is_gone(sup):
    assert client.stop_and_wait(sup.sock_path, timeout=20) is True
    assert not sup.sock_path.exists() and sup.wait(10)
    assert client.stop_and_wait(sup.sock_path, timeout=1) is True  # já não roda: nada a esperar


def test_spawn_restart_flag_reaches_client(monkeypatch):
    from orqweb.agent import cli

    seen = {}
    monkeypatch.setattr(cli.client, "spawn", lambda *a, **k: seen.update(k) or "ok")
    monkeypatch.setattr(cli.config, "load", lambda: type("C", (), {"account": lambda self, n: n})())
    monkeypatch.setattr(cli, "_refuse_if_terminal_live", lambda *a: None)
    assert cli.main(["spawn", "--dir", "/p", "--account", "g", "--label", "p@l", "--restart"]) == 0
    assert seen["restart"] is True and seen["parent"] is None


def test_quota_exhausted_marks_state_and_keeps_last_message_for_retry(sup):
    c = Conn(sup.sock_path)
    c.until(lambda e: e.get("subtype") == "init")
    c.user("esgote a cota, por favor")
    got = c.until(lambda e: e.get("type") == "orq" and "sem cota" in e.get("text", ""))
    assert got[-1]["level"] == "error"
    c.until(is_result)
    st = state_of(sup)
    assert st["state"] == "quota" and st["retry"] == "esgote a cota, por favor" and "limit" in st["msg"]
    c.close()
