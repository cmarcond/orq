"""Vínculo entre máquinas (orq-agent link): registro de sessões, espelho, túnel e o ciclo de vida, sem ssh."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orqweb import config
from orqweb.agent import cli, client, link


def entry(sessions: Path, pid: int, name: str, sock: str, key: bool = True, **extra) -> Path:
    sessions.mkdir(parents=True, exist_ok=True)
    data = {"pid": pid, "name": name, "messagingSocketPath": sock, "status": "idle", **extra}
    (sessions / f"{pid}.json").write_text(json.dumps(data))
    if key:
        (sessions / f"{pid}.abc123.key").write_text(json.dumps({"peerToken": f"tok-{pid}"}))
    return sessions / f"{pid}.json"


# ---------------------------------------------------------------- registro
def test_find_entry_by_name_alive_only_and_never_a_mirror(tmp_path):
    s = tmp_path / "sessions"
    entry(s, 11, "a-laptop", "/tmp/cc-socks/11.sock")
    entry(s, 12, "b-laptop", "/tmp/cc-socks/12.sock", key=False)
    entry(s, 13, "morto-laptop", "/tmp/cc-socks/13.sock")
    entry(s, 99, "c-server0", "/tmp/cc-socks/99.sock", _orq_mirror="c@server0")
    (s / "lixo.json").write_text("{")
    alive = lambda pid: pid in (11, 12, 99)  # noqa: E731
    e = link.find_entry("a-laptop", [tmp_path / "nada", s], alive)
    assert e and e.pid == 11 and e.sock == "/tmp/cc-socks/11.sock" and e.sock_dir == "/tmp/cc-socks"
    assert e.key_path and e.key_path.name == "11.abc123.key"
    rec = e.record()
    assert rec["name"] == "a-laptop" and rec["key_name"] == "11.abc123.key" and "tok-11" in rec["key"]
    assert rec["sessions_dir"] == str(s) and rec["json"]["pid"] == 11
    assert link.find_entry("b-laptop", [s], alive).key_path is None
    assert link.find_entry("morto-laptop", [s], alive) is None, "pid morto não conta"
    assert link.find_entry("c-server0", [s], alive) is None, "espelho de outra máquina não é sessão local"
    assert link.find_entry("x", [s], alive) is None


def test_sessions_dirs_dedupes_accounts_sharing_a_dir():
    conf = config.parse(
        {
            "accounts": [
                {"name": "a", "config_dir": "~/.claude-a"},
                {"name": "b", "config_dir": "~/.claude-b"},
                {"name": "c", "config_dir": "~/.claude-a"},
            ]
        }
    )
    assert [d.name for d in link.sessions_dirs(conf)] == ["sessions", "sessions"]
    assert len({str(d) for d in link.sessions_dirs(conf)}) == 2


def test_write_and_remove_mirror(tmp_path):
    s = tmp_path / "sessions"
    rec = {
        "name": "x-server0",
        "pid": 500,
        "sock": "/run/user/1000/cc-socks/500.sock",
        "json": {
            "pid": 500,
            "name": "x-server0",
            "messagingSocketPath": "/run/user/1000/cc-socks/500.sock",
        },
        "key_name": "500.k.key",
        "key": "segredo",
    }
    paths = link.write_mirror(s, rec, "/tmp/cc-socks/500.sock", "x@server0")
    assert [p.name for p in paths] == ["500.json", "500.k.key"]
    d = json.loads((s / "500.json").read_text())
    assert d["messagingSocketPath"] == "/tmp/cc-socks/500.sock" and d["_orq_mirror"] == "x@server0"
    assert (s / "500.k.key").read_text() == "segredo" and oct(os.stat(s / "500.k.key").st_mode)[-3:] == "600"
    assert rec["json"]["messagingSocketPath"].startswith("/run"), "o registro de origem não muda"
    sock = tmp_path / "500.sock"
    sock.write_text("")
    link.remove_mirror(s, 500, str(sock))
    assert not list(s.iterdir()) and not sock.exists()
    link.remove_mirror(s, 500, str(sock))  # de novo: nada a fazer, sem erro
    assert link.write_mirror(s, {**rec, "key_name": None, "key": None}, "/x", "y") == [s / "500.json"]


# ---------------------------------------------------------------- túnel
def test_tunnel_command_forwards_both_sockets(tmp_path):
    e = link.Entry(tmp_path / "24.json", None, {"pid": 24, "messagingSocketPath": "/tmp/cc-socks/24.sock"})
    r = {"pid": 90, "sock": "/run/user/1000/cc-socks/90.sock"}
    cmd = link.tunnel_command("server0", e, r, ["ssh", "-F", "cfg"])
    assert cmd[:4] == ["ssh", "-F", "cfg", "-N"] and cmd[-1] == "server0"
    assert cmd[cmd.index("-L") + 1] == "/tmp/cc-socks/90.sock:/run/user/1000/cc-socks/90.sock"
    assert cmd[cmd.index("-R") + 1] == "/run/user/1000/cc-socks/24.sock:/tmp/cc-socks/24.sock"
    assert "ExitOnForwardFailure=yes" in cmd and "StreamLocalBindUnlink=yes" in cmd


# ---------------------------------------------------------------- ciclo de vida
class FakeTunnel:
    """O ssh -N: cria o socket encaminhado e fica vivo até terminate()."""

    instances = []

    def __init__(self, cmd, **kw):
        self.cmd, self.pid, self.rc = cmd, 4242, None
        self.stderr = None
        FakeTunnel.instances.append(self)
        local_sock = cmd[cmd.index("-L") + 1].split(":")[0]
        Path(local_sock).write_text("")

    def poll(self):
        return self.rc

    def terminate(self):
        self.rc = 0

    def kill(self):
        self.rc = -9

    def wait(self, t=None):
        return self.rc


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Conta `g` com sessions/ em tmp, o agente local `a@laptop` vivo (pid = este processo), estado em tmp."""
    monkeypatch.setenv("ORQ_STATE_DIR", str(tmp_path / "state"))
    conf = config.parse({"accounts": [{"name": "g", "config_dir": str(tmp_path / "cfg")}]})
    socks = tmp_path / "socks"
    socks.mkdir()
    sessions = tmp_path / "cfg" / "sessions"
    entry(sessions, os.getpid(), "a-laptop", str(socks / f"{os.getpid()}.sock"))
    remote = {
        "name": "b-server0",
        "pid": 777,
        "sock": "/run/user/1000/cc-socks/777.sock",
        "sessions_dir": "/home/c/.claude/sessions",
        "json": {
            "pid": 777,
            "name": "b-server0",
            "messagingSocketPath": "/run/user/1000/cc-socks/777.sock",
        },
        "key_name": "777.zz.key",
        "key": "segredo-remoto",
    }
    calls = []

    def run_remote(args, input=None):
        calls.append((args, input))
        if args[:2] == ["peer", "b@server0"] and len(args) == 2:
            return json.dumps(remote if not calls_fail.get("peer") else {"err": calls_fail["peer"]}) + "\n"
        return '{"ok": true}\n'

    calls_fail = {}
    FakeTunnel.instances.clear()
    return dict(
        conf=conf,
        sessions=sessions,
        socks=socks,
        remote=remote,
        calls=calls,
        run_remote=run_remote,
        fail=calls_fail,
    )


def test_link_up_mirrors_both_sides_and_down_cleans(world):
    lk = link.Link(
        "a@laptop",
        "b@server0",
        conf=world["conf"],
        run_remote=world["run_remote"],
        popen=FakeTunnel,
        log=lambda s: None,
    )
    lk.up()
    me = os.getpid()
    local_mirror_sock = str(world["socks"] / "777.sock")
    # espelho local do remoto, apontando para o socket encaminhado aqui
    d = json.loads((world["sessions"] / "777.json").read_text())
    assert d["messagingSocketPath"] == local_mirror_sock and d["_orq_mirror"] == "b@server0"
    assert (world["sessions"] / "777.zz.key").read_text() == "segredo-remoto"
    # a outra máquina: pediu o cartão, limpou sobras, gravou o espelho do local com o socket encaminhado lá
    args = [c[0] for c in world["calls"]]
    assert args[0] == ["peer", "b@server0"] and args[1] == ["peer", "b@server0", "--unmirror", str(me)]
    assert args[2][:5] == ["peer", "b@server0", "--mirror", "--sock", f"/run/user/1000/cc-socks/{me}.sock"]
    assert args[2][5:] == ["--from", "a@laptop"]
    sent = json.loads(world["calls"][2][1])
    assert sent["name"] == "a-laptop" and sent["pid"] == me and "tok-" in sent["key"]
    # túnel e estado
    t = FakeTunnel.instances[-1]
    assert t.cmd[t.cmd.index("-R") + 1].startswith(f"/run/user/1000/cc-socks/{me}.sock:")
    st = json.loads(link.link_file("a@laptop", "b@server0").read_text())
    assert st["up"] and st["pid"] == me and st["tunnel_pid"] == 4242 and st["remote_name"] == "b-server0"
    assert link.list_links()[0]["up"] is True
    # o túnel cai: wait volta com o motivo; down desfaz tudo
    t.rc = 255
    assert lk.wait(poll=0.01) == "túnel caiu"
    lk.down("túnel caiu")
    assert not (world["sessions"] / "777.json").exists() and not (world["sessions"] / "777.zz.key").exists()
    assert not Path(local_mirror_sock).exists()
    assert world["calls"][-1][0] == ["peer", "b@server0", "--unmirror", str(me)]
    st = json.loads(link.link_file("a@laptop", "b@server0").read_text())
    assert st["up"] is False and st["why"] == "túnel caiu" and link.list_links()[0]["up"] is False
    # stop() (SIGTERM do unlink) tira o wait
    lk2 = link.Link(
        "a@laptop",
        "b@server0",
        conf=world["conf"],
        run_remote=world["run_remote"],
        popen=FakeTunnel,
        log=lambda s: None,
    )
    lk2.up()
    lk2.stop()
    assert lk2.wait(poll=0.01) == "unlink"
    # o agente local fecha: o cartão some
    lk2._stop = False
    (world["sessions"] / f"{me}.json").unlink()
    assert lk2.wait(poll=0.01) == "a@laptop fechou"
    lk2.down("a@laptop fechou")


def test_link_failures_are_explained(world):
    logs = []
    kw = dict(conf=world["conf"], run_remote=world["run_remote"], popen=FakeTunnel, log=logs.append)
    world["fail"]["peer"] = "b@server0 não está rodando com nome b-server0"
    assert link.run_link("a@laptop", "b@server0", **kw) == 1
    st = json.loads(link.link_file("a@laptop", "b@server0").read_text())
    assert st["up"] is False and "não está rodando" in st["why"] and any("falhou" in m for m in logs)
    assert not FakeTunnel.instances, "sem cartão remoto não abre túnel"
    world["fail"].clear()
    (world["sessions"] / f"{os.getpid()}.json").unlink()
    with pytest.raises(RuntimeError, match="não está rodando com nome a-laptop"):
        link.Link("a@laptop", "b@server0", **kw).up()


def test_tunnel_that_dies_or_never_opens_fails_up(world, monkeypatch):
    class Dead(FakeTunnel):
        def __init__(self, cmd, **kw):
            import io

            self.cmd, self.pid, self.rc, self.stderr = (
                cmd,
                1,
                255,
                io.BytesIO(b"bind: Address already in use"),
            )
            FakeTunnel.instances.append(self)

    kw = dict(conf=world["conf"], run_remote=world["run_remote"], log=lambda s: None)
    with pytest.raises(RuntimeError, match="túnel ssh.*saiu.*Address already in use"):
        link.Link("a@laptop", "b@server0", popen=Dead, **kw).up()

    class Silent(FakeTunnel):
        def __init__(self, cmd, **kw):
            self.cmd, self.pid, self.rc, self.stderr = cmd, 1, None, None
            FakeTunnel.instances.append(self)

    monkeypatch.setattr(link, "TUNNEL_WAIT", 0.3)
    with pytest.raises(RuntimeError, match="não abriu"):
        link.Link("a@laptop", "b@server0", popen=Silent, **kw).up()
    assert FakeTunnel.instances[-1].rc == -9, "túnel mudo é morto"


# ---------------------------------------------------------------- CLI peer (o lado remoto)
def test_cli_peer_prints_record_mirrors_and_unmirrors(world, monkeypatch, capsys):
    from orqweb.agent.state import StateFile

    monkeypatch.setattr(cli.config, "load", lambda: world["conf"])
    StateFile(config.state_dir("a@laptop") / "state.json").write(label="a@laptop", account="g", kind="sdk")
    assert cli.main(["peer", "a@laptop"]) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["name"] == "a-laptop" and rec["pid"] == os.getpid() and rec["key_name"].endswith(".key")
    # espelho de um agente de outra máquina chega pelo stdin
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(world["remote"])))
    assert (
        cli.main(
            [
                "peer",
                "a@laptop",
                "--mirror",
                "--sock",
                str(world["socks"] / "777.sock"),
                "--from",
                "b@server0",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["ok"]
    d = json.loads((world["sessions"] / "777.json").read_text())
    assert d["messagingSocketPath"] == str(world["socks"] / "777.sock") and d["_orq_mirror"] == "b@server0"
    assert cli.main(["peer", "a@laptop", "--unmirror", "777"]) == 0
    capsys.readouterr()
    assert not (world["sessions"] / "777.json").exists() and not (world["sessions"] / "777.zz.key").exists()
    # erros: agente desconhecido, sem sessão com o nome
    assert (
        cli.main(["peer", "nada@laptop"]) == 1
        and "não é um agente" in json.loads(capsys.readouterr().out)["err"]
    )
    StateFile(config.state_dir("c@laptop") / "state.json").write(label="c@laptop", account="g", kind="sdk")
    assert cli.main(["peer", "c@laptop"]) == 1 and "c-laptop" in json.loads(capsys.readouterr().out)["err"]
    assert cli.main(["links"]) == 0 and json.loads(capsys.readouterr().out) == []


# ---------------------------------------------------------------- link / unlink (sem tmux)
def test_spawn_link_and_unlink_without_tmux(world, monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda n: None)
    started = []

    def fake_popen(cmd, **kw):
        started.append(cmd)
        # o link-run de mentira: sobe e grava o estado como o de verdade faria
        link.StateFile(link.link_file("a@laptop", "b@server0")).write(up=True, pid=os.getpid(), why=None)

        class P:
            pid = 1

        return P()

    import types

    monkeypatch.setattr(
        client,
        "subprocess",
        types.SimpleNamespace(
            Popen=fake_popen, run=subprocess.run, DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE
        ),
    )
    assert "segundo plano" in client.spawn_link("a@laptop", "b@server0", wait=5)
    assert started[0][-3:] == ["link-run", "a@laptop", "b@server0"]
    assert client.spawn_link("a@laptop", "b@server0") == "já de pé"
    # falha: o link-run grava up=False com o motivo
    link.StateFile(link.link_file("a@laptop", "b@server0")).write(
        up=False, pid=None, why="o túnel ssh saiu"
    )

    def fail_popen(cmd, **kw):
        link.StateFile(link.link_file("a@laptop", "b@server0")).write(
            up=False, pid=None, why="o túnel ssh saiu: x"
        )

        class P:
            pid = 1

        return P()

    monkeypatch.setattr(
        client,
        "subprocess",
        types.SimpleNamespace(
            Popen=fail_popen, run=subprocess.run, DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE
        ),
    )
    with pytest.raises(RuntimeError, match="túnel ssh saiu"):
        client.spawn_link("a@laptop", "b@server0", wait=5)
    assert client.unlink("a@laptop", "b@server0") == "não havia vínculo de pé"
    # unlink de verdade: um processo que, ao receber SIGTERM, grava up=False e sai (como o link-run)
    stf = link.link_file("a@laptop", "b@server0")
    code = (
        "import json,signal,sys,time\n"
        f"p={str(stf)!r}\n"
        "def bye(*a):\n    d=json.load(open(p)); d['up']=False; d['why']='unlink'\n"
        "    json.dump(d, open(p,'w')); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, bye)\n"
        "while True: time.sleep(0.1)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code])
    link.StateFile(stf).write(up=True, pid=p.pid, why=None)
    time.sleep(0.5)
    assert client.unlink("a@laptop", "b@server0", wait=10) == "vínculo desfeito"
    assert p.wait(5) == 0 and json.loads(stf.read_text())["why"] == "unlink"
    assert client.link_session("a@laptop", "b@server0") == "orql-a--b-server0"


def test_run_link_handles_sigterm(world, monkeypatch):
    """run_link instala o SIGTERM e sai limpo quando o unlink chega."""
    import threading

    kw = dict(conf=world["conf"], run_remote=world["run_remote"], popen=FakeTunnel, log=lambda s: None)
    monkeypatch.setattr(link, "POLL", 0.05)
    threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    assert link.run_link("a@laptop", "b@server0", **kw) == 0
    st = json.loads(link.link_file("a@laptop", "b@server0").read_text())
    assert st["up"] is False and st["why"] == "unlink"


# ---------------------------------------------------------------- mesma máquina: sem túnel
def test_local_link_mirrors_between_accounts(world, monkeypatch, tmp_path):
    """Contas diferentes têm sessions/ diferentes e o ListAgents só lê a própria: o vínculo local espelha."""
    from orqweb.agent.state import StateFile

    assert link.same_host("a@laptop", "b@laptop") and not link.same_host("a@laptop", "b@server0")
    conf = config.parse(
        {
            "accounts": [
                {"name": "g", "config_dir": str(tmp_path / "cfg")},
                {"name": "h", "config_dir": str(tmp_path / "cfg2")},
            ]
        }
    )
    monkeypatch.setattr(link.config, "load", lambda: conf)
    with pytest.raises(RuntimeError, match="@laptop não está rodando"):
        client.spawn_link("a@laptop", "b@laptop")
    for lab, acc in (("a@laptop", "g"), ("b@laptop", "h")):
        d = config.state_dir(lab)
        d.mkdir(parents=True, exist_ok=True)
        (d / "agent.sock").write_text("")
        StateFile(d / "state.json").write(label=lab, account=acc, kind="sdk")
    monkeypatch.setattr(client, "socket_alive", lambda p: p.exists())
    with pytest.raises(RuntimeError, match="b@laptop não está rodando com nome b-laptop"):
        client.spawn_link("a@laptop", "b@laptop")
    entry(
        tmp_path / "cfg2" / "sessions", os.getppid(), "b-laptop", str(world["socks"] / "x.sock")
    )  # pid vivo de verdade
    assert "4 arquivos espelhados" in client.spawn_link("a@laptop", "b@laptop")
    (lk,) = link.list_links()
    assert lk["kind"] == "local" and lk["up"] and lk["remote_name"] == "b-laptop" and lk["pid"] is None
    # cada conta ganhou o cartão e a chave do outro, com o MESMO socket (mesma máquina)
    me, other = os.getpid(), os.getppid()
    assert lk["sessions"] == {"a@laptop": me, "b@laptop": other}
    # um agente reaberto (sessão nova) derruba o vínculo, mesmo com os dois sockets de pé
    monkeypatch.setattr(link, "pid_alive", lambda pid: pid != other)
    assert link.list_links()[0]["up"] is False
    monkeypatch.setattr(link, "pid_alive", lambda pid: True)
    assert link.list_links()[0]["up"] is True
    d = json.loads((tmp_path / "cfg" / "sessions" / f"{other}.json").read_text())
    assert d["messagingSocketPath"] == str(world["socks"] / "x.sock") and d["_orq_mirror"] == "b@laptop"
    assert (tmp_path / "cfg2" / "sessions" / f"{me}.json").exists() and (
        tmp_path / "cfg2" / "sessions" / f"{me}.abc123.key"
    ).exists()
    assert len(lk["mirrors"]) == 4
    # vincular de novo troca os espelhos sem duplicar; fechar um dos dois derruba o `up`
    assert "4 arquivos espelhados" in client.spawn_link("a@laptop", "b@laptop")
    (config.state_dir("b@laptop") / "agent.sock").unlink()
    assert link.list_links()[0]["up"] is False, "um dos dois fechou"
    (config.state_dir("b@laptop") / "agent.sock").write_text("")
    assert client.unlink("a@laptop", "b@laptop") == "vínculo desfeito"
    assert not (tmp_path / "cfg" / "sessions" / f"{other}.json").exists()
    assert not (tmp_path / "cfg2" / "sessions" / f"{me}.json").exists()
    st = link.list_links()[0]
    assert st["up"] is False and st["why"] == "unlink" and st["mirrors"] == []
    # mesma conta: nada a espelhar
    StateFile(config.state_dir("b@laptop") / "state.json").write(account="g")
    entry(tmp_path / "cfg" / "sessions", other, "b-laptop", str(world["socks"] / "x.sock"))
    assert "já se enxergavam" in client.spawn_link("a@laptop", "b@laptop")
