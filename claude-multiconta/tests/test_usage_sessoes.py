"""usage-sessoes: leitura das conversas, classificação dos processos, junção por conta e saída, sem rede."""

import datetime as dt
import importlib.machinery
import importlib.util
import json
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("usage_sessoes", str(HERE / "bin" / "usage-sessoes"))
spec = importlib.util.spec_from_loader("usage_sessoes", loader)
S = importlib.util.module_from_spec(spec)
loader.exec_module(S)

NOW = dt.datetime.now(dt.timezone.utc)


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """Os testes daqui são da varredura e da formatação: nunca o banco do usage-hist da máquina."""
    monkeypatch.setattr(S, "from_database", lambda hours: None)


def iso(minutes_ago):
    return (NOW - dt.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def assistant(sid, mid, req, minutes_ago, out=100, cache_new=10, model="claude-fable-5-1", cwd="/p/a"):
    return {
        "type": "assistant",
        "sessionId": sid,
        "requestId": req,
        "timestamp": iso(minutes_ago),
        "cwd": cwd,
        "entrypoint": "sdk-cli",
        "message": {
            "id": mid,
            "model": model,
            "usage": {"input_tokens": 1, "output_tokens": out, "cache_creation_input_tokens": cache_new, "cache_read_input_tokens": 50},
        },
    }


def write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


# ---------------------------------------------------------------- funções puras
def test_project_key_matches_claude_code_folder_names():
    assert S.project_key("/home/user/Projects/orq-qa") == "-home-user-Projects-orq-qa"
    assert S.project_key("/Users/user/Projects/a_b.c") == "-Users-user-Projects-a-b-c"


def test_weight_uses_price_ratios():
    assert S.weight({"input": 10, "output": 2, "cache_new": 4, "cache_read": 100}) == 10 + 10 + 5 + 10


def test_parse_etime():
    assert S.parse_etime("05:07") == 307
    assert S.parse_etime("1:02:03") == 3723
    assert S.parse_etime("2-01:00:00") == 2 * 86400 + 3600


def test_is_claude_and_kind():
    assert S.is_claude("/home/c/.local/bin/claude --resume x")
    assert not S.is_claude("/Applications/Claude.app/Contents/MacOS/Claude")
    assert not S.is_claude("python3 claude_tool.py")
    assert S.kind_of("claude --chrome-native-host", "") == "chrome"
    assert S.kind_of("claude daemon run --json-path x", "") == "daemon"
    assert S.kind_of("claude", "claude daemon run") == "daemon"  # reserva do daemon
    assert S.kind_of("claude -p --permission-prompt-tool stdio", "") == "orq"
    assert S.kind_of("claude -p x", "python3 -m orqweb.agent.cli start") == "orq"
    assert S.kind_of("claude -p Você vai consolidar", "bash bin/consolidar.sh") == "script"
    assert S.kind_of("claude --resume 57952fe0-81", "tmux") == "interativo"
    assert S.session_arg("claude --resume 57952fe0-8178-4e33") == "57952fe0-8178-4e33"
    assert S.session_arg("claude") is None


# ---------------------------------------------------------------- conversas
def test_sessions_since_dedupes_windows_and_folds_subagents(tmp_path):
    cfg = tmp_path / ".claude-work3"
    proj = cfg / "projects" / "-p-a"
    write(
        proj / "s1.jsonl",
        [
            {"type": "user", "sessionId": "s1", "timestamp": iso(30), "message": {"content": "Você vai\nauditar  isto"}},
            assistant("s1", "m1", "r1", 30, out=100),
            assistant("s1", "m1", "r1", 30, out=100),  # o mesmo turno repetido (um registro por bloco)
            assistant("s1", "m2", "r2", 10, out=50, model="claude-opus-5-5", cwd="/p/a/sub"),
            assistant("s1", "m0", "r0", 60 * 8, out=999),  # fora da janela de 5 h
            {"type": "assistant", "sessionId": "s1", "timestamp": iso(5), "message": {"id": "m3", "model": "<synthetic>"}},
        ],
    )
    write(proj / "s1" / "subagents" / "agent-x.jsonl", [assistant("s1-sub", "m9", "r9", 20, out=7)])
    write(cfg / "projects" / "-p-b" / "s2.jsonl", [assistant("s2", "n1", "q1", 60 * 7)])  # só fora da janela
    dirs = {str(cfg.resolve()): "work3"}
    rows = S.sessions_since(str(tmp_path), NOW - dt.timedelta(hours=5), dirs)
    assert len(rows) == 1
    r = rows[0]
    assert (r["account"], r["session"], r["project"]) == ("work3", "s1", "-p-a")
    assert r["responses"] == 3 and r["subagent_responses"] == 1
    assert r["tokens"]["output"] == 157
    assert r["cwd"] == "/p/a" and r["entrypoint"] == "sdk-cli"  # a pasta onde abriu, não para onde foi
    assert r["models"] == ["fable-5-1", "opus-5-5"]
    assert r["prompt"] == "Você vai auditar isto"
    assert r["weight"] == S.weight(r["tokens"])


def test_account_of_falls_back_to_folder_name(tmp_path):
    assert S.account_of(str(tmp_path / ".claude-novo"), {}) == "novo"
    assert S.account_of(str(tmp_path / ".claude"), {}) == "padrão"


# ---------------------------------------------------------------- junção e saída
def session(sid, acc, project, weight, last_min, host="h", entry="cli"):
    return {
        "account": acc,
        "session": sid,
        "project": project,
        "cwd": "/p/" + project,
        "entrypoint": entry,
        "first": iso(last_min + 30),
        "last": iso(last_min),
        "responses": 3,
        "subagent_responses": 0,
        "tokens": {"input": 0, "output": 10, "cache_new": 0, "cache_read": 0},
        "models": ["fable-5-1"],
        "prompt": "faça x",
        "weight": weight,
    }


def proc(pid, acc, project, kind="interativo", session_arg=None):
    return {
        "pid": pid,
        "account": acc,
        "cwd": "/p/" + project,
        "project": project,
        "kind": kind,
        "seconds": 3600,
        "tmux": "claude-" + acc,
        "parent": "bash bin/x.sh",
        "session_arg": session_arg,
        "args": "claude",
        "last_activity": iso(90),
    }


def test_build_links_processes_and_computes_shares():
    machines = {
        "laptop": {
            "sessions": [session("a1", "work3", "k", 300, 2), session("a0", "work3", "k", 100, 120), session("g1", "gmail", "m", 50, 1)],
            "processes": [proc(1, "work3", "k"), proc(2, "gmail", "outra"), proc(3, "work3", "z", session_arg="a0")],
        }
    }
    by = S.build(machines)
    a3 = by["work3"]
    assert [s["session"] for s in a3["sessions"]] == ["a1", "a0"]
    assert [p["pid"] for p in a3["sessions"][0]["live"]] == [1]  # a mais recente da pasta
    assert [p["pid"] for p in a3["sessions"][1]["live"]] == [3]  # pelo --resume, mesmo noutra pasta
    assert a3["sessions"][0]["share"] == 0.75 and a3["weight"] == 400
    assert [p["pid"] for p in by["gmail"]["idle"]] == [2]
    assert list(S.build(machines, ["gmail"])) == ["gmail"]


def test_render_shows_quota_state_and_idle_processes():
    machines = {
        "laptop": {
            "sessions": [session("a1", "work3", "k", 300, 2, entry="sdk-cli"), session("a2", "work3", "j", 100, 200)],
            "processes": [proc(1, "work3", "k", kind="script"), proc(9, "work3", "q")],
        }
    }
    by = S.build(machines)
    out = S.render(by, {"work3": {"5h": 26, "7d": 19}}, {"work3": "Work 3"}, 5, ["laptop"], {"node1": "No route"}, NOW, False)
    assert "■ Work 3   cota: 5h 26% · 7d 19%" in out
    assert "● trabalhando · pid 1 script" in out and "lançado por bash bin/x.sh" in out
    assert "↳ faça x" in out and "encerrada" in out
    assert "processos vivos sem consumo na janela" in out and "pid 9 interativo" in out
    assert "sem resposta: node1 (No route)" in out
    limited = S.render(by, {"work3": {"err": "API respondeu 429: rate_limit_error"}}, {}, 5, ["laptop"], {}, NOW, False)
    assert "cota: indisponível agora (a API limitou" in limited and "5h 0%" not in limited


def test_gather_dedupes_the_same_machine_under_two_names(monkeypatch):
    def fake(host, hours, me, args=None, timeout=None):
        return host, {"machine_id": "same" if host in ("a", "b") else host, "sessions": [], "processes": []}, None

    monkeypatch.setattr(S, "fetch", fake)
    ok, errs = S.gather(["a", "b", "c"], 5)
    assert list(ok) == ["a", "c"] and errs == {}


def test_brief_groups_script_runs_hides_noise_and_flags_forgotten_processes():
    runs = [session(f"r{i}", "work3", "k", 100, 30 + i, entry="sdk-cli") for i in range(9)]
    machines = {
        "laptop": {
            "sessions": runs + [session("i1", "work3", "m", 100, 2)],
            "processes": [
                proc(1, "work3", "m"),  # ligado à sessão interativa recente
                proc(2, "work3", "velha"),  # aberto, parado há 90 min: aparece
                dict(proc(3, "work3", "x", kind="daemon")),  # reserva do daemon: some
            ],
        }
    }
    by = S.build(machines)
    by["gmail"] = {"sessions": [], "idle": [], "weight": 0.0}
    out = S.render_brief(by, {"work3": {"5h": 26, "7d": 19}, "gmail": {"err": "429"}}, {"work3": "Work 3", "gmail": "Gmail"}, 5, {"node1": "No route"}, NOW)
    lines = out.splitlines()
    assert lines[0] == "últimas 5 h · sem resposta: node1"
    assert lines[1].startswith("┌") and lines[-1].startswith("└"), "sem rodapé"
    assert len({len(l) for l in lines if l and l[0] in "┌│├└"}) == 1  # todas as linhas da tabela com a mesma largura
    row = next(l for l in lines if "Work 3" in l)
    assert " 26% ·  19%" in row
    script = next(l for l in lines if " · script " in l)
    assert "script 9×" in script and " 90%" in script and "●" not in script  # nove execuções numa linha
    assert "● m " in out and "interativo" not in out, "● marca a que respondeu agora; o tipo interativo é o padrão e não aparece"
    assert "tipo" not in lines[2] and "n │" not in lines[2]
    assert "parado 1 h 30" in out and "pid" not in out and "↳" not in out
    gmail = next(l for l in lines if "Gmail" in l)
    assert "  ? ·   ?" in gmail and "sem consumo" in gmail


def test_fetch_retries_a_hung_ssh_once_but_not_an_unreachable_host(monkeypatch):
    import subprocess as sp

    calls = []

    def hung_then_ok(cmd, input=None, capture_output=True, text=True, timeout=None):
        calls.append(cmd[-2])
        if len(calls) == 1:
            raise sp.TimeoutExpired(cmd, timeout)
        return sp.CompletedProcess(cmd, 0, json.dumps({"machine_id": "x", "sessions": [], "processes": []}), "")

    monkeypatch.setattr(S.subprocess, "run", hung_then_ok)
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    host, data, err = S.fetch("node0", 5, "outra")
    assert err is None and data["machine_id"] == "x" and calls == ["node0", "node0"]
    calls.clear()
    monkeypatch.setattr(S.subprocess, "run", lambda cmd, **k: calls.append(1) or sp.CompletedProcess(cmd, 255, "", "ssh: connect to host node1 port 22: No route to host"))
    host, data, err = S.fetch("node1", 5, "outra")
    assert data is None and "No route to host" in err and calls == [1]  # fora da rede: uma tentativa só
