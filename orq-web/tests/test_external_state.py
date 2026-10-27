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
    r = FakeRunner({"usage-all": (0, '{"gmail": {"7d": 1}}')})
    assert External(Path("/b"), r).usage() == {"gmail": {"7d": 1}}
    assert r.calls[-1] == ["/b/usage-all", "--json"]
    assert External(Path("/b"), FakeRunner({})).usage() == {}


def test_usage_remote_goes_through_tmux_run_shell_with_fallback():
    r = FakeRunner({"usage-all": (0, '{"gmail": {"7d": 2}}')})
    assert External(Path("/b"), r).usage("laptop-zt") == {"gmail": {"7d": 2}}
    cmd = r.calls[-1]
    assert cmd[0] == "ssh" and "laptop-zt" in cmd
    assert (
        cmd[-1].startswith('tmux run-shell "$HOME/.local/bin/usage-all --json"')
        and "|| ~/.local/bin/usage-all --json" in cmd[-1]
    )
    # host local não usa ssh
    External(Path("/b"), r).usage("laptop")
    assert r.calls[-1] == ["/b/usage-all", "--json"]


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


class InputRunner(FakeRunner):
    def __call__(self, cmd, timeout, input=None):
        self.inputs = getattr(self, "inputs", []) + [input]
        return super().__call__(cmd, timeout)


def test_spawn_level_sends_role_on_stdin_with_parent_and_level():
    r = InputRunner({"orq-agent": (0, "pronto")})
    ext = External(Path("/b"), r)
    assert (
        ext.spawn(
            LAPTOP,
            "p~professor@laptop",
            "/p",
            "gmail",
            "new",
            role="Você é…",
            parent="p@laptop",
            level="professor",
        )
        is None
    )
    cmd = r.calls[-1]
    assert cmd[cmd.index("--role-file") + 1] == "-" and cmd[cmd.index("--parent") + 1] == "p@laptop"
    assert cmd[cmd.index("--level") + 1] == "professor" and r.inputs[-1] == "Você é…"
    assert (
        ext.spawn(
            "server0", "p~professor@server0", "~/p", "gmail", "new", role="papel", parent="p@server0"
        )
        is None
    )
    assert r.calls[-1][0] == "ssh" and "--role-file -" in r.calls[-1][-1] and r.inputs[-1] == "papel"
    # sem papel, o runner antigo (sem `input`) continua servindo
    plain = FakeRunner({"orq-agent": (0, "pronto")})
    assert External(Path("/b"), plain).spawn(LAPTOP, "p@laptop", "/p", "gmail", "resume") is None
    assert "--role-file" not in plain.calls[-1]


def test_spawn_restart_flag():
    r = FakeRunner({"orq-agent": (0, "pronto")})
    assert External(Path("/b"), r).spawn(LAPTOP, "p@laptop", "/p", "gmail", "resume", restart=True) is None
    assert r.calls[-1][-1] == "--restart"
    External(Path("/b"), r).spawn("h", "p@h", "/p", "gmail", "resume", restart=True)
    assert r.calls[-1][-1].endswith("--restart")


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

    def usage(self, host=None):
        self.usage_host = host
        return {"gmail": {"7d": 40, "7d_pace": 50}}

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
    assert snap["usage"]["gmail"]["7d"] == 40
    assert snap["agents"][0]["label"] == "a@laptop" and snap["host_errors"] == {"node1": "sem resposta"}
    assert snap["accounts"] == ["gmail"] and snap["labels"] == {"gmail": "gmail"}
    assert snap["colors"] == {"gmail": config.PALETTE[0]}
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


def test_stale_listing_finishing_late_does_not_overwrite_a_newer_one():
    """Duas consultas em paralelo: a velha (sem o agente, lenta) termina depois da nova e é ignorada."""
    import threading

    class Ext:
        def __init__(self):
            self.entered, self.gate, self.calls = threading.Event(), threading.Event(), 0

        def all_agents(self, hosts):
            self.calls += 1
            if self.calls == 1:  # a primeira começou antes de o agente abrir e demora (máquina sem resposta)
                self.entered.set()
                self.gate.wait(5)
                return [], {}
            return [{"label": "p@h", "state": "waiting", "pid": 1}], {}

    now = [1000.0]
    ext = Ext()
    cache = S.StateCache(type("C", (), {"hosts": []})(), ext, clock=lambda: now[0])
    old = threading.Thread(target=cache.refresh_agents)
    old.start()
    assert ext.entered.wait(5)
    now[0] = 1010.0
    cache.refresh_agents()  # a mais nova vê o agente
    assert [a["label"] for a in cache.agents] == ["p@h"]
    ext.gate.set()
    old.join(5)
    assert [a["label"] for a in cache.agents] == ["p@h"], "a consulta velha não apaga o agente"
    now[0] = 1020.0
    cache.refresh_agents()  # e uma nova de verdade continua valendo
    assert cache.agents_started == 1020.0


def test_provisional_replaces_same_label():
    c = make_cache(Clock())
    c.refresh_agents()
    c.add_provisional(S.provisional_agent("a@laptop", "laptop", "gmail", "/x", 5.0))
    (a,) = c.snapshot()["agents"]
    assert a["state"] == "starting" and a["kind"] == "sdk"


def test_provisional_survives_stale_listing_until_host_confirms():
    """A consulta que começou antes do spawn volta sem o novo (ou com o "closed" da vida anterior)."""
    clk = Clock()
    c = make_cache(clk)
    c.refresh_agents()
    c.add_provisional(S.provisional_agent("novo@laptop", "laptop", "gmail", "/n", clk.t))

    def state_of(label):
        return next(a["state"] for a in c.snapshot()["agents"] if a["label"] == label)

    c.refresh_agents()  # a máquina ainda não devolve novo@laptop
    assert state_of("novo@laptop") == "starting" and "a@laptop" in c.live_labels()
    c.ext.all_agents = lambda hosts: ([{"label": "novo@laptop", "state": "closed", "account": "gmail"}], {})
    c.refresh_agents()  # state.json de uma rodada anterior
    assert state_of("novo@laptop") == "starting"
    c.ext.all_agents = lambda hosts: ([{"label": "novo@laptop", "state": "waiting", "account": "gmail"}], {})
    c.refresh_agents()  # confirmado
    assert state_of("novo@laptop") == "waiting" and "novo@laptop" not in c.provisional
    # sem confirmação, o provisório expira
    c.add_provisional(S.provisional_agent("x@laptop", "laptop", "gmail", "/x", clk.t))
    c.ext.all_agents = lambda hosts: ([], {})
    clk.t += S.PROVISIONAL_TTL + 1
    c.refresh_agents()
    assert "x@laptop" not in c.live_labels()


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


def test_usage_host_is_passed_to_external():
    conf = config.parse({"accounts": [{"name": "gmail", "config_dir": "~/.claude-gmail"}]})
    ext = FakeExt()
    S.StateCache(conf, ext, usage_host="server0").refresh_usage()
    assert ext.usage_host == "server0"
    S.StateCache(conf, ext).refresh_usage()
    assert ext.usage_host is None


def test_mark_closing_keeps_agent_closed_while_host_still_sees_it():
    class Ext:
        def __init__(self):
            self.state = "waiting"

        def all_agents(self, hosts):
            return [{"label": "p@h", "state": self.state}], {}

    now = [1000.0]
    ext = Ext()
    cache = S.StateCache(type("C", (), {"hosts": []})(), ext, clock=lambda: now[0])
    cache.refresh_agents()
    assert cache.agents[0]["state"] == "waiting"
    cache.mark_closing("p@h")
    assert cache.agents[0]["state"] == "closed"
    cache.refresh_agents()  # a máquina ainda vê o processo (graça de 15 s)
    assert cache.agents[0]["state"] == "closed"
    now[0] += S.CLOSING_TTL + 1
    cache.refresh_agents()
    assert cache.agents[0]["state"] == "waiting" and cache.closing == {}
    # reabrir o mesmo rótulo cancela o "fechando"
    cache.mark_closing("p@h")
    cache.add_provisional(S.provisional_agent("p@h", "h", "g", "/p", now[0]))
    cache.refresh_agents()
    assert cache.agents[0]["state"] == "waiting"


# ---------------------------------------------------------------- arquivos da _outbox
def test_list_files_local_and_remote(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    ext = External(Path("/b"), FakeRunner({}))
    items, err = ext.list_files(LAPTOP, str(tmp_path))
    assert err is None and [i["name"] for i in items] == ["a.pdf"]
    r = FakeRunner({"orq-agent ls": (0, '[{"name": "r.md", "size": 3, "mtime": 1}]')})
    items, err = External(Path("/b"), r).list_files("server0", "~/p/_outbox")
    assert err is None and items[0]["name"] == "r.md"
    assert r.calls[-1][0] == "ssh" and r.calls[-1][-1] == "~/.local/bin/orq-agent ls '~/p/_outbox'"
    # orq-agent antigo na máquina: erro explica o que fazer
    r = FakeRunner({"orq-agent ls": (2, "")})
    r.table = {"orq-agent ls": (2, "")}
    ext = External(Path("/b"), r)
    ext.run = lambda cmd, timeout: subprocess.CompletedProcess(
        cmd, 2, "", "argument cmd: invalid choice: 'ls'"
    )
    items, err = ext.list_files("server0", "~/p/_outbox")
    assert items == [] and "antigo" in err and "deploy.sh server0" in err
    assert External(Path("/b"), FakeRunner({})).list_files("server0", "~/p")[1] == "sem resposta"


class FakePopen:
    def __init__(self, cmd, stdout=None, stderr=None):
        self.cmd, self.killed = cmd, False
        import io

        spec = cmd[-1].split("--range ")[-1]
        FakePopen.last = self
        if "nada" in cmd[-1]:
            self.stdout = io.BytesIO(b'{"err": "[Errno 2] No such file"}\n')
        elif spec == "99-":
            self.stdout = io.BytesIO(b'{"err": "faixa 99- fora do arquivo de 10 bytes"}\n')
        else:
            self.stdout = io.BytesIO(b'{"size": 10, "start": 2, "length": 4}\n2345')

    def kill(self):
        self.killed = True

    def wait(self):
        return 0


def test_open_file_local_and_remote(tmp_path):
    import pytest

    (tmp_path / "a.bin").write_bytes(b"0123456789")
    ext = External(Path("/b"), FakeRunner({}), popen=FakePopen)
    fs = ext.open_file(LAPTOP, str(tmp_path / "a.bin"), 2, 5)
    assert (fs.size, fs.length, b"".join(fs.chunks())) == (10, 4, b"2345")
    fs.close()
    fs = ext.open_file("server0", "~/p/_outbox/a.bin", 2, 5)
    assert (fs.size, fs.length, b"".join(fs.chunks())) == (10, 4, b"2345")
    assert FakePopen.last.cmd[0] == "ssh" and FakePopen.last.cmd[-1].endswith(
        "orq-agent cat '~/p/_outbox/a.bin' --range 2-5"
    )
    fs.close()
    assert FakePopen.last.killed
    with pytest.raises(FileNotFoundError):
        ext.open_file("server0", "~/p/_outbox/nada", 0, None)
    with pytest.raises(ValueError):
        ext.open_file("server0", "~/p/_outbox/a.bin", 99, None)
    with pytest.raises(FileNotFoundError):
        ext.open_file(LAPTOP, str(tmp_path / "nada"))


# ---------------------------------------------------------------- vínculos
def test_agents_also_bring_links_in_one_connection():
    agents_json = '[{"label": "k", "kind": "sdk", "state": "waiting"}]'
    links_json = '[{"local": "k@server0", "remote": "p@laptop", "up": true}]'
    r = FakeRunner(
        {"--status-json": (0, agents_json + "\n" + External.LINKS_MARK + "\n" + links_json + "\n")}
    )
    ext = External(Path("/b"), r)
    host, items, err = ext.agents("server0")
    assert err is None and items[0]["label"] == "k@server0"
    assert (
        r.calls[-1][0] == "ssh"
        and "orq --status-json; echo; echo ---orq-links---; ~/.local/bin/orq-agent links" in r.calls[-1][-1]
    )
    assert ext.links == [{"local": "k@server0", "remote": "p@laptop", "up": True}]
    # máquina antiga, sem `orq-agent links`: a lista de agentes continua valendo, vínculos vazios
    r = FakeRunner({"--status-json": (0, agents_json + "\n" + External.LINKS_MARK + "\n")})
    ext = External(Path("/b"), r)
    assert ext.agents("server0")[2] is None and ext.links == []
    # local: duas chamadas (sem ssh não há o problema da segunda conexão)
    r = FakeRunner({"--status-json": (0, agents_json), "orq-agent links": (0, links_json)})
    ext = External(Path("/b"), r)
    assert ext.agents(LAPTOP)[2] is None and ext.links[0]["local"] == "k@server0"
    assert r.calls[-1] == ["/b/orq-agent", "links"]
    # all_agents consolida o que cada máquina devolveu (aqui só o laptop tem; o ssh falso não traz a marca)
    ext.all_agents(["server0"])
    assert set(ext.links_by_host) == {"laptop", "server0"} and len(ext.links) == 1


def test_link_and_unlink_commands_local_and_remote():
    r = FakeRunner({"orq-agent": (0, "vínculo na sessão tmux orql-a--b")})
    ext = External(Path("/b"), r)
    assert ext.link(LAPTOP, "a@laptop", "b@server0") is None
    assert r.calls[-1] == ["/b/orq-agent", "link", "a@laptop", "b@server0"]
    assert ext.unlink("server0", "b@server0", "a@laptop") is None
    assert r.calls[-1][0] == "ssh" and r.calls[-1][-1] == "~/.local/bin/orq-agent unlink b@server0 a@laptop"
    ext.run = lambda cmd, timeout: subprocess.CompletedProcess(cmd, 1, "", "o túnel ssh saiu: x")
    assert ext.link(LAPTOP, "a@laptop", "b@server0") == "o túnel ssh saiu: x"
    assert External(Path("/b"), FakeRunner({})).link(LAPTOP, "a", "b") == "sem resposta"


def test_new_link_outlives_a_listing_that_started_before_it():
    conf = config.parse({"accounts": [{"name": "g", "config_dir": "~/.c"}], "orq_hosts": []})
    t = [1000.0]
    rec = {"local": "a@laptop", "remote": "b@laptop", "host": "laptop", "kind": "local", "up": True}

    class Ext:
        links = []
        add_during = None  # o vínculo nasce no meio da consulta

        def all_agents(self, hosts):
            t[0] += 2
            if self.add_during:
                self.add_during()
            t[0] += 3
            return [], {}

    ext = Ext()
    st = S.StateCache(conf, ext, clock=lambda: t[0])
    ext.add_during = lambda: st.add_link(rec)
    st.refresh_agents()  # começou antes do vínculo e voltou sem ele: o vínculo fica
    assert [lk["local"] for lk in st.snapshot()["links"]] == ["a@laptop"]
    ext.add_during = None
    st.refresh_agents()  # começou depois e a máquina não o lista: a máquina é a verdade
    assert st.snapshot()["links"] == []
    st.add_link(rec)
    ext.links = [dict(rec, sessions={"a@laptop": 1})]
    st.refresh_agents()  # a máquina o lista: vale o dela, sem duplicar
    (lk,) = st.snapshot()["links"]
    assert lk["sessions"] == {"a@laptop": 1} and "page_ts" not in lk
    st.drop_link("b@laptop", "a@laptop")
    assert st.snapshot()["links"] == []


def test_links_of_an_unreachable_host_are_dropped():
    links_json = '[{"local": "k@server0", "remote": "p@laptop", "up": true}]'
    r = FakeRunner({"--status-json": (0, "[]\n" + External.LINKS_MARK + "\n" + links_json + "\n")})
    ext = External(Path("/b"), r)
    ext.agents("server0")
    assert len(ext.links) == 1
    ext.run = lambda cmd, timeout: (_ for _ in ()).throw(OSError("sem rede"))
    assert ext.agents("server0")[2] == "sem resposta" and ext.links == []


def test_spawn_passes_permission_and_set():
    r = FakeRunner({"orq-agent": (0, "pronto")})
    ext = External(Path("/b"), r)
    assert ext.spawn(LAPTOP, "p@laptop", "/p", "gmail", "new", permission="auto", set_id="artigo") is None
    cmd = r.calls[-1]
    assert cmd[cmd.index("--permission-mode") + 1] == "auto" and cmd[cmd.index("--set") + 1] == "artigo"
    ext.spawn(
        "server0", "p@server0", "~/p", "gmail", "new", permission="auto", set_id="artigo", pipeline="x-y"
    )
    assert "--permission-mode auto" in r.calls[-1][-1] and "--set artigo" in r.calls[-1][-1]
    assert "--pipeline x-y" in r.calls[-1][-1]


def test_history_local_remote_and_failure():
    body = json.dumps({"accounts": {"gmail": {"today_7d": 2.0}}, "alerts": []})
    r = FakeRunner({"usage-hist": (0, body)})
    ext = External(Path("/b"), r)
    assert ext.history()["accounts"]["gmail"]["today_7d"] == 2.0
    assert r.calls[-1] == ["/b/usage-hist", "painel", "--json"]
    assert ext.history("node4")["accounts"] and r.calls[-1][0] == "ssh"
    assert r.calls[-1][-1] == "~/.local/bin/usage-hist painel --json"
    assert External(Path("/b"), FakeRunner({})).history() == {}  # sem usage-hist
    assert External(Path("/b"), FakeRunner({"usage-hist": (0, "null")})).history() == {}  # sem coleta recente
    assert External(Path("/b"), FakeRunner({"usage-hist": (0, "[1]")})).history() == {}


def test_history_refreshes_only_with_the_page_open_and_goes_in_the_snapshot():
    now = [1000.0]

    class Ext:
        calls = []

        def history(self, host=None):
            self.calls.append(host)
            return {"accounts": {"gmail": {}}, "alerts": ["⚠ x"]}

    conf = type("C", (), {"hosts": [], "accounts": {}, "labels": {}, "colors": {}})()
    ext = Ext()
    cache = S.StateCache(conf, ext, clock=lambda: now[0], usage_host="node4")
    cache.refresh_history()
    assert ext.calls == ["node4"] and cache.snapshot()["history"]["alerts"] == ["⚠ x"]
    # sem history no External (servidor antigo, testes): fica vazio, sem quebrar
    bare = S.StateCache(conf, object(), clock=lambda: now[0])
    bare.refresh_history()
    assert bare.snapshot()["history"] == {}


def test_account_panel_command_and_cache():
    body = json.dumps({"account": "gmail", "hourly_24h": [0] * 24})
    r = FakeRunner({"usage-hist": (0, body)})
    ext = External(Path("/b"), r)
    assert ext.account(None, "gmail")["account"] == "gmail" and r.calls[-1] == [
        "/b/usage-hist",
        "conta",
        "gmail",
        "--json",
    ]
    assert ext.account("node4", "gmail") and r.calls[-1][-1] == "~/.local/bin/usage-hist conta gmail --json"
    assert ext.account(None, "outra") == {}  # resposta de outra conta não vale
    assert External(Path("/b"), FakeRunner({})).account(None, "gmail") == {}
    now = [100.0]

    class Ext:
        n = 0

        def account(self, host, name):
            self.n += 1
            return {"account": name}

    conf = type("C", (), {"hosts": [], "accounts": {}, "labels": {}, "colors": {}})()
    e = Ext()
    cache = S.StateCache(conf, e, clock=lambda: now[0])
    cache.account_detail("gmail")
    cache.account_detail("gmail")
    assert e.n == 1  # dentro do cache
    now[0] += S.ACCOUNT_TTL + 1
    cache.account_detail("gmail")
    assert e.n == 2


def test_usage_table_command_and_cache():
    body = json.dumps({"hours": 24, "accounts": [{"account": "gmail", "groups": []}]})
    r = FakeRunner({"usage-hist": (0, body)})
    ext = External(Path("/b"), r)
    assert ext.usage_table(None, 24)["hours"] == 24 and r.calls[-1] == [
        "/b/usage-hist",
        "uso",
        "--horas",
        "24",
        "--json",
    ]
    assert ext.usage_table("node4", 5) and r.calls[-1][-1] == "~/.local/bin/usage-hist uso --horas 5 --json"
    assert (
        External(Path("/b"), FakeRunner({"usage-hist": (0, json.dumps({"accounts": {}}))})).usage_table(
            None, 5
        )
        == {}
    )
    now = [0.0]

    class Ext:
        n = 0

        def usage_table(self, host, hours):
            self.n += 1
            return {"hours": hours, "accounts": []}

    conf = type("C", (), {"hosts": [], "accounts": {}, "labels": {}, "colors": {}})()
    e = Ext()
    cache = S.StateCache(conf, e, clock=lambda: now[0])
    cache.usage_table(5)
    cache.usage_table(5)
    cache.usage_table(24)
    assert e.n == 2  # 5 h do cache; 24 h é outra entrada
