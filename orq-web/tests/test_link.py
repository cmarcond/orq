"""Link between agents (orq-agent link): session registry, shared mirrors, tunnel and the life
cycle, without real ssh (the other machine and the `ssh -N` are fake; the sockets are real)."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import pytest

from orqweb import config
from orqweb.agent import cli, client, link
from orqweb.agent.state import StateFile


def entry(sessions: Path, pid: int, name: str, sock: str, key: bool = True, **extra) -> Path:
    sessions.mkdir(parents=True, exist_ok=True)
    data = {"pid": pid, "name": name, "messagingSocketPath": sock, "status": "idle", **extra}
    (sessions / f"{pid}.json").write_text(json.dumps(data))
    if key:
        (sessions / f"{pid}.abc123.key").write_text(json.dumps({"peerToken": f"tok-{pid}"}))
    return sessions / f"{pid}.json"


def listen(path: Path) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.listen(64)  # nobody accepts: the queue has to fit every `socket_alive`
    return s


# ---------------------------------------------------------------- registry
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
    assert link.find_entry("morto-laptop", [s], alive) is None, "a dead pid does not count"
    assert link.find_entry("c-server0", [s], alive) is None, "a mirror is not a local session"
    assert link.find_entry("x", [s], alive) is None


def test_write_mirror_validates_and_never_covers_a_real_session(tmp_path):
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
        "key_name": "500.1f2e.key",
        "key": "segredo",
    }
    paths = link.write_mirror(s, rec, "/tmp/cc-socks/orq-500.sock", "x@server0")
    assert [p.name for p in paths] == ["500.json", "500.1f2e.key"]
    d = json.loads((s / "500.json").read_text())
    assert d["messagingSocketPath"] == "/tmp/cc-socks/orq-500.sock" and d["_orq_mirror"] == "x@server0"
    assert (s / "500.1f2e.key").read_text() == "segredo"
    assert oct(os.stat(s / "500.1f2e.key").st_mode)[-3:] == "600"
    assert rec["json"]["messagingSocketPath"].startswith("/run"), "the source record does not change"
    link.write_mirror(s, rec, "/tmp/cc-socks/orq-500.sock", "x@server0")  # again: idempotent
    # a key name that is not <pid>.<hex>.key: refused (it comes from another machine; does not write
    # outside the folder)
    for bad in ("../x", "501.aa.key", "500.key", "500.zz/.key"):
        with pytest.raises(ValueError):
            link.write_mirror(s, {**rec, "key_name": bad}, "/x", "y")
    # a REAL session with the same pid (pids repeat across machines): does not write over it
    entry(s, 600, "local-laptop", "/tmp/cc-socks/600.sock")
    with pytest.raises(RuntimeError, match="local session with pid 600"):
        link.write_mirror(s, {**rec, "pid": 600, "key_name": "600.ab.key"}, "/x", "y")
    assert json.loads((s / "600.json").read_text())["name"] == "local-laptop"
    # removing: only mirrors; the real session stays
    sock = tmp_path / "orq-500.sock"
    sock.write_text("")
    assert link.remove_mirror(s, 500, str(sock)) is True
    assert not (s / "500.json").exists() and not (s / "500.1f2e.key").exists() and not sock.exists()
    assert link.remove_mirror(s, 500) is True, "already deleted: nothing to do, no error"
    assert link.remove_mirror(s, 600) is False and (s / "600.json").exists()
    assert (s / "600.abc123.key").exists()
    assert link.write_mirror(s, {**rec, "key_name": None, "key": None}, "/x", "y") == [s / "500.json"]
    assert link.mirror_sock("/tmp/cc-socks", 7) == "/tmp/cc-socks/orq-7.sock"


# ---------------------------------------------------------------- tunnel
def test_tunnel_command_only_the_forwards_asked():
    cmd = link.tunnel_command("server0", [("-L", "/a/orq-90.sock", "/b/90.sock")], ["ssh", "-F", "cfg"])
    assert cmd[:4] == ["ssh", "-F", "cfg", "-N"] and cmd[-1] == "server0"
    assert cmd[cmd.index("-L") + 1] == "/a/orq-90.sock:/b/90.sock" and "-R" not in cmd
    assert "ExitOnForwardFailure=yes" in cmd and "StreamLocalBindUnlink=yes" in cmd
    both = link.tunnel_command("h", [("-L", "l", "r"), ("-R", "rr", "ll")])
    assert both[both.index("-R") + 1] == "rr:ll"


# ---------------------------------------------------------------- life cycle
class FakeTunnel:
    """The ssh -N: really listens on the -L sockets (so `socket_alive` sees them), alive until terminate()."""

    instances = []

    def __init__(self, cmd, **kw):
        self.cmd, self.pid, self.rc = cmd, 4242, None
        self.listeners = []
        FakeTunnel.instances.append(self)
        for i, a in enumerate(cmd):
            if a == "-L":
                path = cmd[i + 1].split(":")[0]
                try:
                    os.unlink(path)
                except OSError:
                    pass
                self.listeners.append((listen(Path(path)), path))

    @property
    def flags(self):
        return [a for a in self.cmd if a in ("-L", "-R")]

    def poll(self):
        return self.rc

    def terminate(self):
        self.rc = 0
        for s, path in self.listeners:
            s.close()
            try:
                os.unlink(path)
            except OSError:
                pass
        self.listeners = []

    kill = terminate

    def wait(self, t=None):
        return self.rc


@pytest.fixture
def world(monkeypatch):
    """Account `g` with sessions/ in tmp, the local agent `a@laptop` alive (pid = this process), state in
    tmp and the make-believe other machine (`run_remote`): card of `b@server0`, probe, mirror."""
    tmp = Path(tempfile.mkdtemp(prefix="orql", dir="/tmp"))  # Unix socket: short path
    monkeypatch.setenv("ORQ_STATE_DIR", str(tmp / "state"))
    conf = config.parse(
        {
            "accounts": [
                {"name": "g", "config_dir": str(tmp / "cfg")},
                {"name": "h", "config_dir": str(tmp / "cfg2")},
            ]
        }
    )
    monkeypatch.setattr(link.config, "load", lambda: conf)
    socks = tmp / "socks"
    socks.mkdir()
    sessions = tmp / "cfg" / "sessions"
    me = os.getpid()
    entry(sessions, me, "a-laptop", str(socks / f"{me}.sock"))
    for lab, acc in (("a@laptop", "g"), ("c@laptop", "g"), ("b@laptop", "h")):
        d = config.state_dir(lab)
        d.mkdir(parents=True, exist_ok=True)
        StateFile(d / "state.json").write(label=lab, account=acc, kind="sdk")
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
        "key_name": "777.ab12.key",
        "key": "segredo-remoto",
    }
    w = dict(
        tmp=tmp,
        conf=conf,
        sessions=sessions,
        socks=socks,
        remote=remote,
        calls=[],
        fail={},
        remote_alive=False,
    )

    def run_remote(args, input=None):
        w["calls"].append((args, input))
        if args[:3] == ["peer", "b@server0", "--probe"]:
            if w["fail"].get("peer"):
                return json.dumps({"err": w["fail"]["peer"]}) + "\n"
            return json.dumps(dict(w["remote"], probe_alive=w["remote_alive"])) + "\n"
        return '{"ok": true}\n'

    w["run_remote"] = run_remote
    FakeTunnel.instances.clear()
    yield w
    shutil.rmtree(tmp, ignore_errors=True)


def mk(world, local="a@laptop", **kw):
    base = dict(conf=world["conf"], run_remote=world["run_remote"], popen=FakeTunnel, log=lambda s: None)
    return link.Link(local, "b@server0", **{**base, **kw})


def state_of(a, b):
    return json.loads(link.link_file(a, b).read_text())


def test_link_up_mirrors_both_sides_and_down_cleans(world):
    lk = mk(world)
    lk.up()
    me = os.getpid()
    local_sock = str(world["socks"] / "orq-777.sock")
    remote_sock = f"/run/user/1000/cc-socks/orq-{me}.sock"
    # local mirror of the remote, pointing to the forwarded socket (own name, never that of a session)
    d = json.loads((world["sessions"] / "777.json").read_text())
    assert d["messagingSocketPath"] == local_sock and d["_orq_mirror"] == "b@server0"
    assert (world["sessions"] / "777.ab12.key").read_text() == "segredo-remoto"
    # the other machine: card + probe in a single call, then my mirror with the forwarded socket there
    args = [c[0] for c in world["calls"]]
    assert args[0] == ["peer", "b@server0", "--probe", str(me)]
    assert args[1] == ["peer", "b@server0", "--mirror", "--sock", remote_sock, "--from", "a@laptop"]
    sent = json.loads(world["calls"][1][1])
    assert sent["name"] == "a-laptop" and sent["pid"] == me and "tok-" in sent["key"]
    # tunnel with both forwards (nothing was up) and state with host = WHERE the link runs
    t = FakeTunnel.instances[-1]
    assert t.flags == ["-L", "-R"] and link.socket_alive(Path(local_sock))
    assert t.cmd[t.cmd.index("-R") + 1] == f"{remote_sock}:{world['socks']}/{me}.sock"
    st = state_of("a@laptop", "b@server0")
    assert st["host"] == "laptop" and st["remote_host"] == "server0" and st["kind"] == "ssh"
    assert st["up"] and st["pid"] == me and st["tunnel_pid"] == 4242 and st["forwards"] == ["-L", "-R"]
    assert st["resources"] == [
        f"card:{world['sessions']}/777",
        f"sock:{local_sock}",
        f"remote:server0:/home/c/.claude/sessions/{me}",
    ]
    assert link.list_links()[0]["up"] is True
    # the tunnel drops: wait returns with the reason (and the last line of the ssh log); down undoes it all
    lk.tunnel_log.write_text("bind: Address already in use\n")
    t.rc = 255
    assert lk.wait(poll=0.01).startswith("tunnel dropped: bind: Address")
    lk.down("tunnel dropped")
    assert not (world["sessions"] / "777.json").exists() and not (world["sessions"] / "777.ab12.key").exists()
    assert not Path(local_sock).exists()
    assert world["calls"][-1][0] == ["peer", "b@server0", "--unmirror", str(me), "--sock", remote_sock]
    st = state_of("a@laptop", "b@server0")
    assert st["up"] is False and st["why"] == "tunnel dropped" and st["resources"] == []
    assert link.list_links()[0]["up"] is False


def test_two_links_share_mirror_and_socket_and_the_survivor_takes_over(world, monkeypatch):
    """The tab's agent and the teacher above it (same account) linked to the same remote executor: a single
    card and a single forwarded socket; the second does not redo the -L; when the first leaves, nothing
    vanishes, and the one that stayed starts keeping the forward up."""
    me, fake = os.getpid(), os.getpid() + 100000
    entry(world["sessions"], fake, "c-laptop", str(world["socks"] / "c.sock"))
    alive = link.pid_alive
    monkeypatch.setattr(link, "pid_alive", lambda pid: pid == fake or alive(pid))
    first = mk(world)
    first.up()
    second = mk(world, local="c@laptop")
    second.up()
    assert FakeTunnel.instances[-1].flags == ["-R"], "the remote's socket was already up: it only exports c"
    assert (world["sessions"] / "777.json").exists()
    # the first leaves: card, key and socket remain, because the second uses them; only a's mirror there goes
    n = len(world["calls"])
    first.down("unlink")
    assert (world["sessions"] / "777.json").exists() and (world["sessions"] / "777.ab12.key").exists()
    assert [c[0][:4] for c in world["calls"][n:]] == [["peer", "b@server0", "--unmirror", str(me)]]
    st1 = state_of("a@laptop", "b@server0")
    assert st1["up"] is False and st1["resources"] == [
        f"card:{world['sessions']}/777",
        f"sock:{world['socks']}/orq-777.sock",
    ]
    # but the first one's listener died with it: on the next cycle the second redoes the tunnel with -L
    assert not link.socket_alive(world["socks"] / "orq-777.sock")
    threading.Timer(0.3, second.stop).start()
    assert second.wait(poll=0.05, remote_poll=999) == "unlink"
    assert FakeTunnel.instances[-1].flags == ["-L", "-R"] and link.socket_alive(
        world["socks"] / "orq-777.sock"
    )
    assert state_of("c@laptop", "b@server0")["forwards"] == ["-L", "-R"]
    second.down("unlink")
    assert not (world["sessions"] / "777.json").exists() and not (world["socks"] / "orq-777.sock").exists()


def test_wait_notices_both_agents_and_remote_reopen(world):
    lk = mk(world)
    lk.up()
    # the remote closed: the probe returns an error
    world["fail"]["peer"] = "b@server0 is not running under the name b-server0"
    assert lk.wait(poll=0.01, remote_poll=0).startswith("b@server0 closed")
    world["fail"].clear()
    # the remote was reopened: another pid
    world["remote"] = dict(world["remote"], pid=778)
    assert lk.wait(poll=0.01, remote_poll=0) == "b@server0 was reopened"
    world["remote"] = dict(world["remote"], pid=777)
    # the local closed: the card vanishes
    (world["sessions"] / f"{os.getpid()}.json").unlink()
    assert lk.wait(poll=0.01, remote_poll=999) == "a@laptop closed"
    lk.down("fim")


def test_link_failures_are_explained_and_always_cleaned(world):
    logs = []
    kw = dict(conf=world["conf"], run_remote=world["run_remote"], popen=FakeTunnel, log=logs.append)
    world["fail"]["peer"] = "b@server0 is not running under the name b-server0"
    assert link.run_link("a@laptop", "b@server0", **kw) == 1
    st = state_of("a@laptop", "b@server0")
    assert st["up"] is False and "is not running" in st["why"] and any("failed" in m for m in logs)
    assert not FakeTunnel.instances, "without a remote card it opens no tunnel"
    world["fail"].clear()

    # unexpected error (ssh hung) after the tunnel came up: it still goes through down and closes the tunnel
    def flaky(args, input=None):
        if "--mirror" in args:
            raise subprocess.TimeoutExpired("ssh", 90)
        return world["run_remote"](args, input)

    assert link.run_link("a@laptop", "b@server0", **dict(kw, run_remote=flaky)) == 1
    assert FakeTunnel.instances[-1].rc is not None, "tunnel closed"
    st = state_of("a@laptop", "b@server0")
    assert st["up"] is False and "timed out" in st["why"] and st["resources"] == []
    assert not (world["sessions"] / "777.json").exists()
    (world["sessions"] / f"{os.getpid()}.json").unlink()
    with pytest.raises(RuntimeError, match="is not running under the name a-laptop"):
        link.Link("a@laptop", "b@server0", **kw).up()


def test_tunnel_that_dies_or_never_opens_fails_up(world, monkeypatch):
    class Dead(FakeTunnel):
        def __init__(self, cmd, **kw):
            self.cmd, self.pid, self.rc, self.listeners = cmd, 1, 255, []
            FakeTunnel.instances.append(self)

    kw = dict(conf=world["conf"], run_remote=world["run_remote"], log=lambda s: None)
    lk = link.Link("a@laptop", "b@server0", popen=Dead, **kw)
    lk.tunnel_log.parent.mkdir(parents=True, exist_ok=True)
    lk.tunnel_log.write_text("x\nbind: Address already in use\n")
    with pytest.raises(RuntimeError, match="ssh tunnel.*exited.*Address already in use"):
        lk.up()

    class Silent(FakeTunnel):
        def __init__(self, cmd, **kw):
            self.cmd, self.pid, self.rc, self.listeners = cmd, 1, None, []
            FakeTunnel.instances.append(self)

    monkeypatch.setattr(link, "TUNNEL_WAIT", 0.3)
    with pytest.raises(RuntimeError, match="did not open"):
        link.Link("a@laptop", "b@server0", popen=Silent, **kw).up()
    assert FakeTunnel.instances[-1].rc == 0, "a silent tunnel is closed"


def test_run_link_handles_sigterm(world, monkeypatch):
    kw = dict(conf=world["conf"], run_remote=world["run_remote"], popen=FakeTunnel, log=lambda s: None)
    monkeypatch.setattr(link, "POLL", 0.05)
    threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    assert link.run_link("a@laptop", "b@server0", **kw) == 0
    st = state_of("a@laptop", "b@server0")
    assert st["up"] is False and st["why"] == "unlink" and FakeTunnel.instances[-1].rc == 0


# ---------------------------------------------------------------- CLI peer (the remote side)
def test_cli_peer_prints_record_probes_mirrors_and_unmirrors(world, monkeypatch, capsys):
    monkeypatch.setattr(cli.config, "load", lambda: world["conf"])
    me = os.getpid()
    out = lambda: json.loads(capsys.readouterr().out)  # noqa: E731
    assert cli.main(["peer", "a@laptop"]) == 0
    rec = out()
    assert rec["name"] == "a-laptop" and rec["pid"] == me and rec["key_name"].endswith(".key")
    assert "probe_alive" not in rec
    # probe: is the forwarded socket of the given pid alive here?
    (world["socks"] / "orq-777.sock").write_text("")  # leftover of a tunnel that dropped
    assert cli.main(["peer", "a@laptop", "--probe", "777"]) == 0 and out()["probe_alive"] is False
    assert not (world["socks"] / "orq-777.sock").exists(), "the probe collects the dead socket"
    s = listen(world["socks"] / "orq-777.sock")
    assert cli.main(["peer", "a@laptop", "--probe", "777"]) == 0 and out()["probe_alive"] is True
    s.close()
    os.unlink(world["socks"] / "orq-777.sock")
    # the mirror of an agent from another machine arrives through stdin
    sock = str(world["socks"] / "orq-777.sock")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(world["remote"])))
    assert cli.main(["peer", "a@laptop", "--mirror", "--sock", sock, "--from", "b@server0"]) == 0
    assert out()["ok"]
    d = json.loads((world["sessions"] / "777.json").read_text())
    assert d["messagingSocketPath"] == sock and d["_orq_mirror"] == "b@server0"
    Path(sock).write_text("")
    assert cli.main(["peer", "a@laptop", "--unmirror", "777", "--sock", sock]) == 0 and out()["ok"]
    assert not (world["sessions"] / "777.json").exists() and not (world["sessions"] / "777.ab12.key").exists()
    assert not Path(sock).exists()
    # errors are a response, not a failure (code 0): unknown agent, no session with the name, invalid mirror
    assert cli.main(["peer", "nada@laptop"]) == 0 and "is not an agent" in out()["err"]
    monkeypatch.setattr(link, "ENTRY_WAIT", 0.1)
    assert cli.main(["peer", "c@laptop"]) == 0 and "c-laptop" in out()["err"]
    bad = json.dumps({**world["remote"], "key_name": "../x"})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(bad))
    assert cli.main(["peer", "a@laptop", "--mirror", "--sock", sock, "--from", "b@server0"]) == 0
    assert "invalid mirror" in out()["err"]
    assert cli.main(["links"]) == 0 and out() == []


# ---------------------------------------------------------------- link / unlink (without tmux)
def test_spawn_link_and_unlink_without_tmux(world, monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda n: None)
    started = []
    stf = link.link_file("a@laptop", "b@server0")

    class P:
        pid = 1

    def fake_popen(cmd, **kw):
        started.append(cmd)
        StateFile(stf).write(up=True, pid=os.getpid(), why=None)  # the make-believe link-run comes up
        return P()

    def ns(popen):
        return types.SimpleNamespace(
            Popen=popen, run=subprocess.run, DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE
        )

    monkeypatch.setattr(client, "subprocess", ns(fake_popen))
    assert "background" in client.spawn_link("a@laptop", "b@server0", wait=5)
    assert started[0][-3:] == ["link-run", "a@laptop", "b@server0"]
    assert client.spawn_link("a@laptop", "b@server0") == "already up"
    # still coming up (process alive, up not yet): do not tear it down midway
    StateFile(stf).write(up=False, pid=os.getpid(), why=None)
    assert client.spawn_link("a@laptop", "b@server0") == "still coming up"
    # failure: link-run writes up=False with the reason
    StateFile(stf).write(up=False, pid=None, why="x")

    def fail_popen(cmd, **kw):
        StateFile(stf).write(up=False, pid=None, why="the ssh tunnel exited: x")
        return P()

    monkeypatch.setattr(client, "subprocess", ns(fail_popen))
    with pytest.raises(RuntimeError, match="ssh tunnel exited"):
        client.spawn_link("a@laptop", "b@server0", wait=5)
    assert client.unlink("a@laptop", "b@server0") == "there was no link up"
    # a real unlink: a process that, on receiving SIGTERM, writes up=False and exits (like link-run)
    code = (
        "import json,signal,sys,time\n"
        f"p={str(stf)!r}\n"
        "def bye(*a):\n    d=json.load(open(p)); d['up']=False; d['why']='unlink'\n"
        "    json.dump(d, open(p,'w')); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, bye)\n"
        "while True: time.sleep(0.1)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code])
    StateFile(stf).write(up=True, pid=p.pid, why=None)
    time.sleep(0.5)
    assert client.unlink("a@laptop", "b@server0", wait=10) == "link undone"
    assert p.wait(5) == 0 and json.loads(stf.read_text())["why"] == "unlink"
    assert client.link_session("a@laptop", "b@server0") == "orql-a--b-server0"


# ---------------------------------------------------------------- same machine: no tunnel
def test_local_link_mirrors_between_accounts_and_is_reaped_when_an_agent_reopens(world, monkeypatch):
    """Different accounts have different sessions/ and ListAgents only reads its own: the local link
    mirrors. The mirror is shared: two links to the same agent only release it when the last one leaves."""
    assert link.same_host("a@laptop", "b@laptop") and not link.same_host("a@laptop", "b@server0")
    me, other, fake = os.getpid(), os.getppid(), os.getpid() + 100000
    monkeypatch.setattr(client, "socket_alive", lambda p: p.exists())
    for lab in ("a@laptop", "b@laptop", "c@laptop"):
        (config.state_dir(lab) / "agent.sock").write_text("")
    monkeypatch.setattr(link, "ENTRY_WAIT", 0.1)
    with pytest.raises(RuntimeError, match="b@laptop is not running under the name b-laptop"):
        client.spawn_link("a@laptop", "b@laptop")
    cfg, cfg2 = world["tmp"] / "cfg" / "sessions", world["tmp"] / "cfg2" / "sessions"
    entry(cfg2, other, "b-laptop", str(world["socks"] / "x.sock"))  # a really live pid
    assert "2 cards mirrored" in client.spawn_link("a@laptop", "b@laptop")
    (lk,) = link.list_links()
    assert lk["kind"] == "local" and lk["up"] and lk["host"] == "laptop" and lk["pid"] is None
    assert lk["sessions"] == {"a@laptop": me, "b@laptop": other}
    assert lk["resources"] == [f"card:{cfg}/{other}", f"card:{cfg2}/{me}"]
    # each account got the other's card and key, with the SAME socket (same machine)
    d = json.loads((cfg / f"{other}.json").read_text())
    assert d["messagingSocketPath"] == str(world["socks"] / "x.sock") and d["_orq_mirror"] == "b@laptop"
    assert (cfg2 / f"{me}.json").exists() and (cfg2 / f"{me}.abc123.key").exists()
    # c (same account as a) also links to b: b's card in cfg is the same; undoing a⇄b keeps it
    entry(cfg, fake, "c-laptop", str(world["socks"] / "c.sock"))
    alive = link.pid_alive
    monkeypatch.setattr(link, "pid_alive", lambda pid: pid == fake or alive(pid))
    assert "2 cards mirrored" in client.spawn_link("c@laptop", "b@laptop")
    assert client.unlink("a@laptop", "b@laptop") == "link undone"
    assert (cfg / f"{other}.json").exists(), "c still uses b's card"
    assert not (cfg2 / f"{me}.json").exists(), "a's card there belonged only to a's link"
    # c closes and reopens (new session): the c⇄b link dies and `links` collects what was left
    monkeypatch.setattr(link, "pid_alive", alive)
    sts = {(s["local"], s["remote"]): s for s in link.list_links()}
    assert sts[("c@laptop", "b@laptop")]["up"] is False and sts[("c@laptop", "b@laptop")]["resources"] == []
    assert not (cfg / f"{other}.json").exists() and not (cfg2 / f"{fake}.json").exists()
    # same account: nothing to mirror
    StateFile(config.state_dir("b@laptop") / "state.json").write(account="g")
    entry(cfg, other, "b-laptop", str(world["socks"] / "x.sock"))
    assert "already saw each other" in client.spawn_link("a@laptop", "b@laptop")
    assert state_of("a@laptop", "b@laptop")["resources"] == []
