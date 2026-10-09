import json
import subprocess
from pathlib import Path

from orqweb import config
from orqweb.server import state as S
from orqweb.server.external import LAPTOP, External, ssh_cmd


class FakeRunner:
    """Devolve respostas por trecho do comando e guarda o que foi chamado."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, cmd, timeout):
        self.calls.append(cmd)
        for key, (code, out) in self.table.items():
            if key in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, code, out, "")
        raise OSError("sem resposta")


def test_usage_ok_and_failure():
    ext = External(Path("/b"), FakeRunner({"claude-usage-fast": (0, '{"gmail": {"7d": 1}}')}))
    assert ext.usage("claude-usage-fast") == {"gmail": {"7d": 1}}
    assert ext.usage("codex-usage-fast") == {"err": "falha ao consultar"}
    assert External(Path("/b"), FakeRunner({})).usage("claude-usage-fast") == {}


def test_projects_local_and_remote_commands():
    r = FakeRunner({"--projects": (0, '[{"cwd": "/p", "mtime": 5}]')})
    ext = External(Path("/b"), r)
    host, items, err = ext.projects(LAPTOP)
    assert err is None and items[0]["host"] == LAPTOP and r.calls[-1][0] == "/b/claude-last"
    host, items, err = ext.projects("server0")
    assert items[0]["host"] == "server0" and r.calls[-1][:1] == ["ssh"] and "server0" in r.calls[-1]
    assert ssh_cmd("h", "x")[-2:] == ["h", "x"] and "BatchMode=yes" in ssh_cmd("h", "x")


def test_agents_get_host_suffix_and_errors():
    r = FakeRunner(
        {
            "--status-json": (
                0,
                '[{"label": "acervo", "state": "waiting"}, {"label": "x@node0", "state": "closed"}]',
            )
        }
    )
    ext = External(Path("/b"), r)
    _, items, err = ext.agents("node0")
    assert [a["label"] for a in items] == ["acervo@node0", "x@node0"]
    ext2 = External(Path("/b"), FakeRunner({}))
    assert ext2.agents("down") == ("down", [], "sem resposta")


def test_all_projects_sorted_and_limited_with_host_errors():
    table = {
        "/b/claude-last --projects": (0, json.dumps([{"cwd": "/a", "mtime": 1}])),
        "ssh": (0, json.dumps([{"cwd": "/b", "mtime": 9}])),
    }
    ext = External(Path("/b"), FakeRunner(table))
    items, errs = ext.all_projects(["h1"])
    assert [p["cwd"] for p in items] == ["/b", "/a"] and errs == {}


def test_spawn_local_and_remote():
    r = FakeRunner({"orq-agent": (0, "pronto")})
    ext = External(Path("/b"), r)
    assert ext.spawn(LAPTOP, "p@laptop", "/p", "gmail", "resume") is None
    assert r.calls[-1][:2] == ["/b/orq-agent", "spawn"]
    assert ext.spawn("server0", "p@server0", "~/p", "gmail", "new") is None
    assert (
        r.calls[-1][0] == "ssh" and "orq-agent spawn" in r.calls[-1][-1] and "--mode new" in r.calls[-1][-1]
    )
    bad = External(Path("/b"), FakeRunner({"orq-agent": (1, "deu ruim")}))
    assert bad.spawn(LAPTOP, "p@laptop", "/p", "gmail", "resume") == "deu ruim"


# ---------------------------------------------------------------- StateCache
class FakeExt:
    def __init__(self):
        self.projects_calls = 0

    def usage(self, script):
        return {"5h": 1} if script.startswith("codex") else {"gmail": {"7d": 40, "7d_pace": 50}}

    def all_agents(self, hosts):
        return [{"label": "a@laptop", "state": "waiting", "account": "gmail"}], {"node1": "sem resposta"}

    def all_projects(self, hosts, limit=30):
        self.projects_calls += 1
        return [{"cwd": "/x", "host": "laptop", "mtime": 1}], {}


def make_cache(clock):
    conf = config.parse(
        {"accounts": [{"name": "gmail", "config_dir": "~/.claude-gmail"}], "orq_hosts": ["node1"]}
    )
    return StateCacheWithClock(conf, FakeExt(), clock)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def StateCacheWithClock(conf, ext, clock):
    return S.StateCache(conf, ext, clock=clock)


def test_snapshot_after_refresh():
    clk = Clock()
    c = make_cache(clk)
    c.refresh_usage()
    c.refresh_agents()
    snap = c.snapshot()
    assert snap["usage"]["gmail"]["7d"] == 40 and snap["usage"]["codex"] == {"5h": 1}
    assert snap["agents"][0]["label"] == "a@laptop" and snap["host_errors"] == {"node1": "sem resposta"}
    assert snap["accounts"] == ["gmail"] and snap["labels"] == {"gmail": "gmail", "codex": "Codex"}
    assert c.live_labels() == {"a@laptop"} and c.running_accounts() == ["gmail"]


def test_projects_cached_by_ttl():
    clk = Clock()
    c = make_cache(clk)
    c.get_projects()
    c.get_projects()
    assert c.ext.projects_calls == 1
    clk.t += S.PROJECTS_TTL + 1
    c.get_projects()
    assert c.ext.projects_calls == 2


def test_page_open_window():
    clk = Clock()
    c = make_cache(clk)
    assert c.page_open()
    clk.t += S.OPEN_WINDOW + 1
    assert not c.page_open()
    c.touch()
    assert c.page_open()


def test_provisional_replaces_same_label():
    c = make_cache(Clock())
    c.refresh_agents()
    c.add_provisional(S.provisional_agent("a@laptop", "laptop", "gmail", "/x", 5.0))
    (a,) = c.snapshot()["agents"]
    assert a["state"] == "starting" and a["kind"] == "sdk"


def test_pick_host_and_label():
    p, err = S.pick_host_and_label({"host": "node1", "cwd": "~/acervo"}, ["node1"], {"acervo@node1"})
    assert err is None and p["label"] == "acervo-2@node1" and p["mode"] == "resume"
    assert S.pick_host_and_label({"host": "nope", "cwd": "/x"}, ["node1"], set()) == (
        None,
        "projeto inválido",
    )
    assert S.pick_host_and_label({"host": "laptop"}, [], set())[1] == "projeto inválido"


def test_dedupe_labels_suffixes_terminal_duplicates_but_not_sdk():
    from orqweb.server.external import dedupe_labels

    items = [
        {"label": "p@h", "tmux": "t1"},
        {"label": "p@h", "tmux": "t2"},
        {"label": "p@h", "kind": "sdk"},
        {"label": "q@h", "tmux": "t3"},
    ]
    out = dedupe_labels(items)
    assert [a["label"] for a in out] == ["p-2@h", "p-3@h", "p@h", "q@h"]
