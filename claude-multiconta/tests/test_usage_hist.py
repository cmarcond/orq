"""usage-hist: banco, coleta (API e ssh falsos), cálculos, alertas e relatórios; e o usage-all e o usage-sessoes
lendo o banco. Sem rede."""

import datetime as dt
import importlib.machinery
import importlib.util
import io
import json
import pathlib
from contextlib import redirect_stdout

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_") + "_t", str(HERE / "bin" / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


H = load("usage-hist")
U = load("usage-all")
S = load("usage-sessoes")

NOW = dt.datetime(2026, 10, 22, 15, 0, tzinfo=dt.timezone.utc)
R7 = NOW + dt.timedelta(days=3)  # reset da semana
R5 = NOW + dt.timedelta(hours=2)
LABELS = {"work3": "Work 3", "gmail": "Gmail"}


def at(minutes_ago):
    return NOW - dt.timedelta(minutes=minutes_ago)


def w(kind, pct, resets, scope=None):
    return {"kind": kind, "scope": scope, "pct": pct, "resets_at": H.iso(resets) if resets else None, "severity": "normal"}


def msg(key, minutes_ago, account="work3", host="laptop", entry="cli", out=100, cache_new=0, cwd="/p/a", session="s1", sub=0):
    return {"key": key, "ts": H.iso(at(minutes_ago)), "account": account, "session": session, "project": "-p-a", "cwd": cwd,
            "entrypoint": entry, "model": "fable-5-1", "input": 0, "output": out, "cache_new": cache_new, "cache_read": 0, "sub": sub}


@pytest.fixture
def conn():
    return H.connect(":memory:")


def quota_track(conn, account, points7, points5=None, every=5, reset7=R7, reset5=R5):
    """Amostras a cada `every` min, terminando em NOW: points7[i] é a % da semana na i-ésima."""
    n = len(points7)
    for i, p in enumerate(points7):
        ts = H.iso(NOW - dt.timedelta(minutes=every * (n - 1 - i)))
        ws = [w("weekly_all", p, reset7)]
        if points5:
            ws.append(w("session", points5[i], reset5))
        H.store_quota(conn, ts, account, ws, None)
    conn.commit()


# ---------------------------------------------------------------- cálculos
def T(minutes):
    return NOW + dt.timedelta(minutes=minutes)


def test_consumed_counts_rises_resets_and_window_start():
    r1, r2 = T(100), T(400)
    assert H.consumed([(T(0), 10, r1), (T(5), 12, r1), (T(10), 12, r1), (T(15), 15, r1)]) == 5
    # reset: o resets_at mudou e a % caiu; a % nova conta inteira
    assert H.consumed([(T(0), 90, r1), (T(5), 3, r2), (T(10), 4, r2)]) == 3 + 1
    # a janela de 5h começou (resets_at None -> definido)
    assert H.consumed([(T(0), 0, None), (T(5), 7, r1)]) == 7
    # oscilação pequena do resets_at não é reset
    assert H.consumed([(T(0), 10, r1), (T(5), 11, r1 + dt.timedelta(seconds=90))]) == 1
    assert H.consumed([(T(0), 10, r1)]) == 0


def test_series_uses_last_sample_before_window_as_baseline(conn):
    quota_track(conn, "gmail", [10, 11, 13, 14, 20], every=60)  # de NOW-4h a NOW
    assert H.consumed_between(conn, "gmail", "weekly_all", at(150), NOW) == 20 - 11  # base: a amostra de 3 h atrás
    assert H.consumed_between(conn, "gmail", "weekly_all", at(30), NOW) == 6


def test_rate_needs_half_the_interval_and_rate_best_falls_back(conn):
    quota_track(conn, "gmail", [10, 12, 14], every=10)  # 20 min de amostras
    assert H.rate(conn, "gmail", "weekly_all", NOW, 6) is None
    r, partial = H.rate_best(conn, "gmail", "weekly_all", NOW, 6)
    assert partial and r == pytest.approx(12.0)  # 4 pts em 20 min
    quota_track(conn, "work3", [10 + i for i in range(80)], every=5)  # ~6,6 h, 1 pt a cada 5 min
    r, partial = H.rate_best(conn, "work3", "weekly_all", NOW, 6)
    assert not partial and r == pytest.approx(12.0, rel=0.02)


def test_forecast():
    h, before = H.forecast(40, 10, R7, NOW)
    assert h == 6 and before
    h, before = H.forecast(40, 0.5, NOW + dt.timedelta(hours=10), NOW)
    assert h == 120 and not before
    assert H.forecast(40, None, R7, NOW) == (None, False)
    assert H.forecast(100, 1, R7, NOW) == (0.0, True)


def test_local_midnight_and_dur():
    m = H.local_midnight(NOW)
    assert m <= NOW and (NOW - m) < dt.timedelta(days=1) and m.astimezone().hour == 0
    assert H.dur(0.5) == "30 min" and H.dur(2.25) == "2 h 15" and H.dur(50) == "2 d 2 h" and H.dur(None) == "—"


# ---------------------------------------------------------------- banco
def test_store_host_is_idempotent_and_keeps_first_prompt(conn):
    data = {"messages": [msg("a|1", 10), msg("b|2", 5)], "session_info": {"work3|s1": {"account": "work3", "session": "s1", "project": "-p-a", "cwd": "/p/a", "entrypoint": "cli", "prompt": "primeiro"}},
            "processes": [{"pid": 7, "account": "work3", "kind": "interativo", "cwd": "/p/a", "seconds": 60, "last_activity": H.iso(at(1))}]}
    assert H.store_host(conn, H.iso(NOW), "laptop", data, None, 1.0) == 2
    data["session_info"]["work3|s1"]["prompt"] = "outro"
    assert H.store_host(conn, H.iso(NOW), "laptop", data, None, 1.0) == 0  # a sobreposição não duplica
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    assert conn.execute("SELECT prompt FROM sessions").fetchone()[0] == "primeiro"
    H.store_host(conn, H.iso(NOW), "node1", None, "No route to host", 1.0)
    assert H.last_ok(conn, "laptop") == NOW and H.last_ok(conn, "node1") is None


def test_latest_quota_prefers_valid_sample_over_newer_error(conn):
    H.store_quota(conn, H.iso(at(20)), "gmail", [w("weekly_all", 30, R7)], None)
    H.store_quota(conn, H.iso(at(5)), "gmail", [], "API respondeu 429")
    H.store_quota(conn, H.iso(at(5)), "work3", [], "API respondeu 429")
    q = H.latest_quota(conn, NOW)
    assert q["gmail"]["windows"][0]["pct"] == 30 and q["work3"] == {"ts": H.iso(at(5)), "err": "API respondeu 429"}


def test_calibrate_needs_points(conn):
    quota_track(conn, "gmail", [10, 11, 12], every=60)
    H.store_host(conn, H.iso(NOW), "h", {"messages": [msg("x|1", 30, account="gmail", out=1000)]}, None, 1)
    assert H.calibrate(conn, "gmail", NOW)["tokens_per_point"] is None  # 2 pontos: pouco
    quota_track(conn, "work3", [10, 12, 14], every=60)
    H.store_host(conn, H.iso(NOW), "h", {"messages": [msg("y|1", 30, out=800)]}, None, 1)
    c = H.calibrate(conn, "work3", NOW)
    assert c["points"] == 4 and c["tokens_per_point"] == pytest.approx(800 * 5 / 4)


# ---------------------------------------------------------------- coleta
@pytest.fixture
def accounts_file(tmp_path, monkeypatch):
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps({"accounts": [{"name": "work3", "config_dir": str(tmp_path / ".a3")}, {"name": "gmail", "config_dir": str(tmp_path / ".g")}], "orq_hosts": ["node4"]}))
    monkeypatch.setattr(H.sibling("usage-all"), "CONF", f)
    return f


def test_coletar_end_to_end_with_fake_api_and_ssh(conn, accounts_file):
    calls = []

    def quota_fn(acc):
        return [w("weekly_all", 19 if acc.name == "work3" else 30, R7), w("session", 5, R5)], None

    def gather_fn(hosts, hours, timeout=None, per_host_args=None):
        calls.append({h: per_host_args(h) for h in hosts})
        data = {"machine_id": "m1", "messages": [msg("k|1", 3)], "session_info": {}, "processes": []}
        return {"laptop": data}, {"node1": "ssh: connect to host node1 port 22: No route to host"}

    s = H.coletar(conn, now=NOW, quota_fn=quota_fn, gather_fn=gather_fn, hosts=["laptop", "node1"])
    assert s["quota"] == {"work3": "ok", "gmail": "ok"} and s["hosts"]["laptop"] == "+1 mensagens"
    assert calls[0]["laptop"] == ["--mensagens", "--desde", H.iso(NOW - dt.timedelta(hours=H.BACKFILL_H))]  # primeira: 7 dias
    later = NOW + dt.timedelta(minutes=5)
    H.coletar(conn, now=later, quota_fn=quota_fn, gather_fn=gather_fn, hosts=["laptop", "node1"])
    assert calls[1]["laptop"] == ["--mensagens", "--desde", H.iso(NOW - dt.timedelta(minutes=H.OVERLAP_MIN))]  # desde o último ok
    assert calls[1]["node1"][2] == H.iso(later - dt.timedelta(hours=H.BACKFILL_H))  # nunca respondeu: 7 dias
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert H.last_collect(conn) == later


def test_coletar_backs_off_after_429(conn, accounts_file):
    n = {"quota": 0}

    def limited(acc):
        n["quota"] += 1
        return [], "API respondeu 429: rate_limit_error"

    gather = lambda hosts, hours, timeout=None, per_host_args=None: ({}, {})  # noqa: E731
    H.coletar(conn, now=NOW, quota_fn=limited, gather_fn=gather, hosts=["laptop"])
    assert n["quota"] == 2
    s = H.coletar(conn, now=NOW + dt.timedelta(minutes=5), quota_fn=limited, gather_fn=gather, hosts=["laptop"])
    assert "skip" in s["quota"] and n["quota"] == 2  # dentro da espera: nem tenta
    H.coletar(conn, now=NOW + dt.timedelta(minutes=11), quota_fn=limited, gather_fn=gather, hosts=["laptop"])
    assert n["quota"] == 4 and H.meta_get(conn, "quota_backoff_min") == "20.0"  # tentou e dobrou a espera


# ---------------------------------------------------------------- alertas
def test_alert_script_surge_and_forgotten_process_and_stale_collector(conn):
    H.store_quota(conn, H.iso(at(40)), "work3", [w("session", 0, R5), w("weekly_all", 10, R7)], None)
    H.store_quota(conn, H.iso(at(25)), "work3", [w("session", 4, R5), w("weekly_all", 11, R7)], None)
    H.store_quota(conn, H.iso(NOW), "work3", [w("session", 12, R5), w("weekly_all", 12, R7)], None)
    msgs = [msg(f"s|{i}", 10 + i, entry="sdk-cli", out=1000, cwd="/u/claude-diario", session=f"x{i}") for i in range(5)] + [msg("i|1", 5, out=50)]
    procs = [{"pid": 9, "account": "work3", "kind": "interativo", "cwd": "/u/mycluster", "seconds": 99999, "last_activity": H.iso(NOW - dt.timedelta(days=2))}]
    H.store_host(conn, H.iso(NOW), "laptop", {"messages": msgs, "processes": procs}, None, 1)
    H.meta_set(conn, "last_collect", H.iso(at(30)))
    al = H.alerts(conn, NOW, LABELS)
    assert any(a.startswith("⚠ Work 3: +12 pts de 5h em 30 min") and "de scripts (laptop claude-diario)" in a for a in al)
    assert any("processo parado há 2 d 0 h em laptop mycluster (pid 9)" in a for a in al)
    assert any(a.startswith("⚠ coletor parado: última coleta há 30 min") for a in al)


def test_alert_rise_without_tokens_and_runs_out_before_reset(conn):
    # calibração: 10 pts com tokens nos últimos dias, depois 3 pts em 1 h sem nenhum token
    quota_track(conn, "gmail", [50 + i for i in range(10)], every=60 * 8)
    H.store_host(conn, H.iso(at(300)), "h", {"messages": [msg("c|1", 60 * 20, account="gmail", out=10000)]}, None, 1)
    H.store_quota(conn, H.iso(at(55)), "gmail", [w("weekly_all", 59, R7)], None)
    H.store_quota(conn, H.iso(NOW), "gmail", [w("weekly_all", 62, R7)], None)
    H.meta_set(conn, "last_collect", H.iso(NOW))
    al = H.alerts(conn, NOW, LABELS)
    assert any(a.startswith("⚠ Gmail: +") and "pts na semana em 1 h sem consumo nas máquinas" in a for a in al)
    # ritmo alto: acaba antes do reset
    quota_track(conn, "work3", [20 + 2 * i for i in range(80)], every=5)
    al = H.alerts(conn, NOW, LABELS)
    assert any(a.startswith("⚠ Work 3: a cota de semana acaba em") and "antes do reset" in a for a in al)


# ---------------------------------------------------------------- relatórios (interface)
def busy_db(conn):
    quota_track(conn, "work3", [10 + i * 0.25 for i in range(100)], [min(99, i) for i in range(100)], every=5)
    quota_track(conn, "gmail", [30] * 100, [0] * 100, every=5)
    msgs = [msg(f"m|{i}", 5 * i, out=2000, cwd="/u/claude-diario" if i % 2 else "/u/orq", entry="sdk-cli" if i % 2 else "cli", session=f"s{i % 2}") for i in range(60)]
    H.store_host(conn, H.iso(NOW), "laptop", {"messages": msgs, "processes": []}, None, 1)
    H.store_host(conn, H.iso(NOW), "node1", None, "ssh: connect to host node1 port 22: No route to host", 1)
    H.meta_set(conn, "last_collect", H.iso(at(2)))
    conn.commit()
    return conn


def table_lines(text):
    return [line for line in text.splitlines() if line and line[0] in "┌│├└"]


def test_painel(conn):
    text, data = H.painel(busy_db(conn), NOW, LABELS)
    lines = text.splitlines()
    assert lines[0].startswith("uso das contas")
    tl = table_lines(text)
    assert len({len(line) for line in tl}) == 1 and "últimas 24 h, por hora" in tl[1]
    row = next(line for line in tl if "Work 3" in line)
    assert "99%" in row and "35%" in row and "/h" in row
    assert any(ch in row for ch in "▁▂▃▄▅▆▇█")
    assert next(line for line in tl if "Gmail" in line).count("·") >= 20  # nada nas últimas horas
    assert any(line.startswith("frota: ") for line in lines)
    assert lines[-1] == "fonte: banco · última coleta há 2 min · sem resposta: node1"
    assert data["accounts"]["work3"]["rate_6h"] == pytest.approx(3.0, rel=0.05)
    assert data["collected"] == H.iso(at(2)) and data["t24"] == H.iso(NOW - dt.timedelta(hours=24))
    assert data["accounts"]["work3"]["rate_partial"] is False and len(data["accounts"]["work3"]["last_24h"]) == 24


def test_painel_marks_partial_data(conn):
    quota_track(conn, "gmail", [30, 31, 32], every=10)
    H.meta_set(conn, "last_collect", H.iso(NOW))
    text, _ = H.painel(conn, NOW, LABELS)
    row = next(line for line in table_lines(text) if "Gmail" in line)
    assert "+2,0*" in row and "/h*" in row
    assert "* desde o início da coleta" in text


def test_dia_semana_calibrar_estado(conn, monkeypatch):
    busy_db(conn)
    text, data = H.dia(conn, NOW, LABELS)
    assert text.startswith("dia ") and "o que mais gastou" in text and "claude-diario" in text and "script" in text
    assert len({len(line) for line in table_lines(text)}) == 2  # duas tabelas, cada uma com largura própria
    text, data = H.semana(conn, NOW, LABELS)
    assert "últimos 7 dias" in text and "quando você usa" in text and len(data["profile"]) == 24
    text, data = H.calibrar_txt(conn, NOW, LABELS)
    assert "tokens por ponto" in text and data["work3"]["tokens_per_point"] is not None
    monkeypatch.setattr(H.shutil, "which", lambda name: None)
    text, data = H.estado(conn, NOW)
    assert "fora da rede" in text and "mensagens no banco" in text and data["counts"]["messages"] == 60


def test_sessions_window_feeds_usage_sessoes_render(conn):
    busy_db(conn)
    H.store_host(conn, H.iso(at(1)), "laptop", {"messages": [], "processes": [{"pid": 3, "account": "work3", "kind": "interativo", "cwd": "/u/orq", "project": "-p-a", "seconds": 60, "last_activity": H.iso(at(1))}]}, None, 1)
    machines, errs = H.sessions_window(conn, NOW, 5)
    assert errs == {"node1": "ssh: connect to host node1 port 22: No route to host"}
    assert {s["session"] for s in machines["laptop"]["sessions"]} == {"s0", "s1"}
    by = S.build(machines)
    tpp = {"work3": 1000.0}
    out = S.render_brief(by, {}, LABELS, 5, errs, NOW, tpp=tpp, source="do banco · coleta há 2 min")
    assert "≈pts" in out and "do banco · coleta há 2 min" in out and "≈" in next(line for line in out.splitlines() if "script" in line)


# ---------------------------------------------------------------- usage-all e usage-sessoes lendo o banco
def test_usage_all_reads_snapshot_and_reports_source(monkeypatch, tmp_path):
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps({"accounts": [{"name": "gmail", "label": "Gmail (g@x)", "config_dir": str(tmp_path / ".g")}]}))
    monkeypatch.setattr(U, "CONF", f)
    snap = {"ts": H.iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3)),
            "accounts": {"gmail": {"windows": [w("session", 12, None), w("weekly_all", 40, dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2))]}}}
    monkeypatch.setattr(U, "load_snapshot", lambda: snap)
    monkeypatch.setattr(U, "fetch_usage", lambda cred: pytest.fail("não devia chamar a API"))
    buf = io.StringIO()
    with redirect_stdout(buf):
        U.main(["--json"])
    out = json.loads(buf.getvalue())
    assert out["gmail"]["7d"] == 40 and out["gmail"]["5h"] == 12 and out["_fonte"] == {"banco": snap["ts"]}
    buf = io.StringIO()
    with redirect_stdout(buf):
        U.main([])
    assert "do banco · coleta há 3 min" in buf.getvalue()
    # --ao-vivo: não lê o banco
    monkeypatch.setattr(U, "load_snapshot", lambda: pytest.fail("não devia ler o banco"))
    monkeypatch.setattr(U, "load_cred", lambda acc: None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        U.main(["--json", "--ao-vivo"])
    assert "sem credencial" in json.loads(buf.getvalue())["gmail"]["err"]


def test_usage_sessoes_uses_database_unless_live(monkeypatch):
    machines = {"laptop": {"sessions": [{"account": "gmail", "session": "s1", "project": "-p", "cwd": "/u/x", "entrypoint": "cli", "first": H.iso(at(30)), "last": H.iso(at(2)),
                                         "responses": 3, "subagent_responses": 0, "tokens": {"input": 0, "output": 100, "cache_new": 0, "cache_read": 0}, "models": [], "prompt": "", "weight": 500.0}],
                           "processes": []}}
    snap = {"ts": H.iso(dt.datetime.now(dt.timezone.utc)), "machines": machines, "errors": {}, "tpp": {"gmail": 100.0}}
    monkeypatch.setattr(S, "from_database", lambda hours: snap)
    monkeypatch.setattr(S, "quota", lambda: {})
    monkeypatch.setattr(S, "gather", lambda *a, **k: pytest.fail("não devia varrer as máquinas"))
    buf = io.StringIO()
    with redirect_stdout(buf):
        S.main([])
    assert "≈5,0" in buf.getvalue() and "do banco · coleta há 0 min" in buf.getvalue()
    monkeypatch.setattr(S, "from_database", lambda hours: pytest.fail("não devia ler o banco"))
    monkeypatch.setattr(S, "gather", lambda *a, **k: ({}, {}))
    buf = io.StringIO()
    with redirect_stdout(buf):
        S.main(["--ao-vivo"])
    assert "ao vivo" in buf.getvalue() and "≈pts" not in buf.getvalue()


def test_remote_host_only_without_local_db(monkeypatch, tmp_path):
    monkeypatch.setenv("USAGE_DB", str(tmp_path / "nao-existe.db"))
    monkeypatch.setenv("USAGE_DB_HOST", "node4")
    monkeypatch.setattr(H.sibling("usage-sessoes"), "local_name", lambda: "laptop")
    assert H.remote_host() == "node4"
    monkeypatch.setattr(H.sibling("usage-sessoes"), "local_name", lambda: "node4")
    assert H.remote_host() is None  # a máquina do banco é esta
    monkeypatch.delenv("USAGE_DB_HOST")
    db = tmp_path / "local.db"
    H.connect(str(db))
    monkeypatch.setenv("USAGE_DB", str(db))
    assert H.remote_host() is None  # há banco aqui


def test_unit_texts_point_to_this_script():
    service, timer = H.unit_texts("/usr/bin/python3", "/home/x/.local/bin/usage-hist")
    assert "ExecStart=/usr/bin/python3 /home/x/.local/bin/usage-hist coletar" in service
    assert "OnUnitActiveSec=5min" in timer and "Persistent=true" in timer


# ---------------------------------------------------------------- agente: uma linha por resposta
def test_agent_messages_since(tmp_path):
    cfg = tmp_path / ".claude-work3" / "projects" / "-p-a"
    cfg.mkdir(parents=True)
    rec = lambda mid, mins, out, model="claude-fable-5-1": {  # noqa: E731
        "type": "assistant", "sessionId": "s1", "requestId": "r" + mid, "timestamp": H.iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=mins)),
        "cwd": "/p/a", "entrypoint": "sdk-cli", "message": {"id": mid, "model": model, "usage": {"input_tokens": 1, "output_tokens": out}}}
    lines = [{"type": "user", "sessionId": "s1", "message": {"content": "Você vai\n auditar"}}, rec("m1", 30, 10), rec("m1", 30, 10), rec("m2", 5, 20), rec("m0", 600, 99), rec("m3", 4, 0, "<synthetic>")]
    (cfg / "s1.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    sub = cfg / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "a.jsonl").write_text(json.dumps(rec("m9", 3, 7)) + "\n")
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    msgs, info = S.messages_since(str(tmp_path), since, {str((tmp_path / ".claude-work3").resolve()): "work3"})
    assert sorted(m["key"] for m in msgs) == ["m1|rm1", "m2|rm2", "m9|rm9"]
    assert next(m for m in msgs if m["key"] == "m9|rm9")["sub"] == 1 and all(m["session"] == "s1" for m in msgs)
    assert info["work3|s1"]["prompt"] == "Você vai auditar" and info["work3|s1"]["entrypoint"] == "sdk-cli"


def test_conta_panel_data_and_text(conn):
    busy_db(conn)
    text, d = H.conta(conn, NOW, LABELS, "work3")
    assert d["account"] == "work3" and d["label"] == "Work 3" and d["7d"]["pct"] == pytest.approx(34.75)
    assert len(d["hourly_24h"]) == 24 and len(d["days_7"]) == 7 and d["days_7"][-1]["pts"] is not None
    assert d["week"]["end"] == H.iso(R7) and d["week"]["start"] == H.iso(R7 - dt.timedelta(days=7))
    assert d["week"]["series"] and all(len(s) == 2 for s in d["week"]["series"])
    assert len(d["week"]["series"]) <= 10  # uma amostra por hora (100 amostras a cada 5 min = ~8 h)
    assert {p["folder"] for p in d["projects_7d"]} == {"claude-diario", "orq"} and all(p["account"] == "work3" for p in d["projects_7d"])
    assert d["rate_6h"] == pytest.approx(3.0, rel=0.05)
    assert text.startswith("Work 3 · semana 35% · reset em 3 d 0 h") and "últimas 24 h" in text and "o que mais gastou em 7 dias" in text
    empty_text, empty = H.conta(H.connect(":memory:"), NOW, LABELS, "gmail")
    assert empty["7d"] is None and empty["week"]["series"] == [] and empty_text.startswith("Gmail")



def test_alert_items_say_account_kind_level_and_badge(conn):
    H.store_quota(conn, H.iso(at(40)), "work3", [w("session", 0, R5), w("weekly_all", 10, R7)], None)
    H.store_quota(conn, H.iso(at(25)), "work3", [w("session", 4, R5), w("weekly_all", 11, R7)], None)
    H.store_quota(conn, H.iso(NOW), "work3", [w("session", 12, R5), w("weekly_all", 12, R7)], None)
    msgs = [msg(f"s|{i}", 10 + i, entry="sdk-cli", out=1000, cwd="/u/claude-diario", session=f"x{i}") for i in range(5)]
    procs = [{"pid": 9, "account": "work3", "kind": "interativo", "cwd": "/u/x", "seconds": 9, "last_activity": H.iso(NOW - dt.timedelta(days=2))}]
    H.store_host(conn, H.iso(NOW), "laptop", {"messages": msgs, "processes": procs}, None, 1)
    H.meta_set(conn, "last_collect", H.iso(at(30)))
    items = H.alert_items(conn, NOW, LABELS)
    by = {(i["account"], i["kind"]): i for i in items}
    assert by[("work3", "script")]["level"] == "warn" and by[("work3", "script")]["short"] == "script"
    assert by[("work3", "processo")]["level"] == "info"
    assert by[(None, "coletor")]["short"] == "coletor"
    assert H.alerts(conn, NOW, LABELS) == [i["text"] for i in items]
    text, data = H.painel(conn, NOW, LABELS)
    assert data["alert_items"] == items
    _, d = H.conta(conn, NOW, LABELS, "work3")
    assert d["alerts"] == [i["text"] for i in items if i["account"] == "work3"]



def test_uso_groups_by_account_from_the_database(conn):
    busy_db(conn)
    H.store_host(conn, H.iso(NOW), "laptop", {"messages": [], "processes": [
        {"pid": 3, "account": "work3", "kind": "interativo", "cwd": "/u/orq", "project": "-p-a", "seconds": 60, "last_activity": H.iso(at(1))},
        {"pid": 4, "account": "gmail", "kind": "interativo", "cwd": "/u/velho", "project": "-u-velho", "seconds": 9e5, "last_activity": H.iso(NOW - dt.timedelta(days=3))}]}, None, 1)
    text, d = H.uso(conn, NOW, LABELS, 5)
    assert d["hours"] == 5 and d["errors"] == {"node1": "ssh: connect to host node1 port 22: No route to host"}
    a3 = next(a for a in d["accounts"] if a["account"] == "work3")
    assert a3["label"] == "Work 3" and a3["quota"]["7d"] == pytest.approx(34.75)
    kinds = {(g["folder"], g["kind"]) for g in a3["groups"]}
    assert kinds == {("claude-diario", "script"), ("orq", "interativo")}
    orq = next(g for g in a3["groups"] if g["folder"] == "orq")
    assert orq["live"] and orq["live"][0]["pid"] == 3 and orq["pts"] is not None and orq["now"] is True
    gm = next(a for a in d["accounts"] if a["account"] == "gmail")
    assert gm["groups"] == [] and gm["stale"][0]["folder"] == "velho" and gm["stale"][0]["idle_s"] > 2 * 86400
    assert "Work 3" in text and "≈pts" in text and "fonte: banco" in text
