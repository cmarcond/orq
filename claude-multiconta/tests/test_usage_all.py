"""usage-all: janelas, ritmo, formatação e saída JSON, sem rede (a API é substituída)."""

import datetime
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import sys
from contextlib import redirect_stdout

HERE = pathlib.Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("usage_all", str(HERE / "bin" / "usage-all"))
spec = importlib.util.spec_from_loader("usage_all", loader)
U = importlib.util.module_from_spec(spec)
loader.exec_module(U)


def iso_in(seconds):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def acc(name="gmail", label="Gmail (g@x)"):
    return U.Account(name=name, config_dir=pathlib.Path("/tmp/x"), label=label.split(" (")[0], full_label=label, email="g@x")


# ---------------------------------------------------------------- modelo
def test_parse_windows_new_format_sorted_with_scope():
    data = {
        "limits": [
            {"kind": "weekly_scoped", "percent": 15, "resets_at": iso_in(3600), "scope": {"model": {"display_name": "Fable"}}},
            {"kind": "session", "percent": 21.0, "resets_at": iso_in(7200), "severity": "normal"},
            {"kind": "weekly_all", "percent": 34, "resets_at": iso_in(86400)},
        ]
    }
    ws = U.parse_windows(data)
    assert [w.kind for w in ws] == ["session", "weekly_all", "weekly_scoped"]
    assert ws[2].scope == "Fable" and ws[2].title == "Fable 7d" and ws[2].long_title.endswith("só Fable")
    assert ws[0].title == "sessão 5h" and ws[1].title == "semana 7d"


def test_parse_windows_old_format():
    data = {"session": {"utilization": 10, "resets_at": iso_in(100)}, "x": "ignorado"}
    (w,) = U.parse_windows(data)
    assert w.kind == "session" and w.percent == 10.0


def test_window_pace_and_slack():
    w = U.Window(kind="session", percent=20, resets_at=iso_in(4.5 * 3600))  # passou 10% da janela de 5 h
    assert 9 < w.pace < 11 and 9 < w.slack < 11
    assert U.Window(kind="session", percent=0, resets_at=None).pace == 0.0, "janela não iniciada"
    assert U.Window(kind="session", percent=0, resets_at=iso_in(-10)).pace is None, "dado vencido"
    assert U.Window(kind="desconhecida", percent=0, resets_at=iso_in(10)).pace is None
    assert U.Window(kind="session", percent=0, resets_at=iso_in(6 * 3600)).pace == 0.0, "reset além do tamanho = não começou"


# ---------------------------------------------------------------- formatação
def test_fmt_left_and_colors():
    assert U.fmt_left(45 * 60) == "00h45" and U.fmt_left(4 * 3600 + 45 * 60) == "04h45"
    assert U.fmt_left(6 * 86400 + 3 * 3600) == "6d03h"
    U.COLOR = False
    assert U.fmt_left(None, 5 * 3600) == "05h00" and U.fmt_left(None) == "  —  "
    assert U.color_for(10, 50) == "32" and U.color_for(55, 50) == "33" and U.color_for(70, 50) == "31"
    assert U.color_for(10, None) is None


def test_cell_has_fixed_width_and_marker():
    U.COLOR = False
    w = U.Window(kind="weekly_all", percent=32, resets_at=iso_in(0.3 * 7 * 86400))  # 70% passado
    c = U.cell(w)
    assert c.startswith(" 32% ") and c.count("█") == 3 and "┃" in c and len(c) == 21, repr(c)
    full = U.Window(kind="session", percent=100, resets_at=iso_in(3600))
    assert U.cell(full).startswith("100% ")


# ---------------------------------------------------------------- saídas
def fake_windows(percent_week):
    return [
        U.Window(kind="session", percent=0, resets_at=iso_in(3 * 3600)),
        U.Window(kind="weekly_all", percent=percent_week, resets_at=iso_in(3.5 * 86400)),
    ], None


def test_brief_orders_by_weekly_slack_and_errors_last(monkeypatch):
    U.COLOR = False
    table = {"a": fake_windows(80), "b": fake_windows(10), "c": ([], "sem credencial")}
    monkeypatch.setattr(U, "get_windows", lambda a: table[a.name])
    out = io.StringIO()
    with redirect_stdout(out):
        ok = U.show_brief([acc("a", "A (x)"), acc("b", "B (x)"), acc("c", "C (x)")])
    lines = out.getvalue().splitlines()
    assert not ok
    body = [ln for ln in lines if ln[:1] and ln[:1] in "ABC"]
    assert [ln[0] for ln in body] == ["B", "A", "C"], body
    assert "✗ sem credencial" in body[-1]
    assert any("sessão 5h" in ln and "semana 7d" in ln for ln in lines), "cabeçalho das janelas"


def test_usage_json_shape(monkeypatch):
    monkeypatch.setattr(U, "get_windows", lambda a: fake_windows(43))
    d = U.usage_json(acc())
    assert set(d) == {"5h", "5h_pace", "5h_left", "7d", "7d_pace", "7d_left"}
    assert d["7d"] == 43 and 49 < d["7d_pace"] < 51 and d["7d_left"] > 0
    monkeypatch.setattr(U, "get_windows", lambda a: ([], "token expirado"))
    assert U.usage_json(acc()) == {"err": "token expirado"}


# ---------------------------------------------------------------- total da semana
def test_total_json_consolidates_the_week():
    per = {
        "a": {"7d": 80, "7d_pace": 50, "7d_left": 3 * 86400},  # gastou 80 em metade da semana
        "b": {"7d": 20, "7d_pace": 50, "7d_left": 4 * 86400},
        "c": {"err": "sem credencial"},  # fica de fora
        "_total": {"7d": 999},  # nunca entra na conta
    }
    t = U.total_json(per)
    assert t["accounts"] == 2 and t["7d"] == 50 and t["7d_pace"] == 50
    assert t["7d_free"] == 50 and t["7d_free_accounts"] == 1.0
    assert t["7d_burn"] == 1.0, "100 pontos gastos em 2 meias-semanas = exatamente no ritmo"
    # sem resets, os 100 que sobram acabariam em 3,5 dias; mas 'a' recarrega em 3 dias e 'b' em 4: não zera
    assert t["7d_runs_out"] is None
    assert t["7d_next_reset"] == 3 * 86400 and t["7d_next_reset_account"] == "a"
    assert U.total_json({"a": {"err": "x"}}) is None
    assert U.total_json({"a": {"7d": 0, "7d_pace": 0, "7d_left": None}})["7d_runs_out"] is None, "nada gasto"


def test_runs_out_before_the_reset_saves_it():
    # 90 gastos em 3,5 dias; sobram 10, que no mesmo ritmo acabam em ~0,39 dia, antes do reset em 3,5 dias
    t = U.total_json({"a": {"7d": 90, "7d_pace": 50, "7d_left": 3.5 * 86400}})
    assert t["7d_burn"] == 1.8 and abs(t["7d_runs_out"] - 10 / 90 * 3.5 * 86400) < 60
    # o reset chega antes de zerar e devolve a conta inteira; a 1,8× do ritmo, a conta cheia dura 3,9 dias
    t = U.total_json({"a": {"7d": 90, "7d_pace": 50, "7d_left": 0.2 * 86400}})
    assert abs(t["7d_runs_out"] - (0.2 + 100 / 90 * 3.5) * 86400) < 60


def test_fmt_total_line():
    U.COLOR = False
    t = U.total_json({"a": {"7d": 80, "7d_pace": 50, "7d_left": 3 * 86400}, "b": {"7d": 20, "7d_pace": 50, "7d_left": 4 * 86400}})
    s = U.fmt_total(t)
    assert s.startswith("TOTAL  ") and " 50% █████┃░░░░   ∞  " in s and s.endswith("queima 1,0× · 50% livre"), s
    wide = U.fmt_total(t, width=len(s) + 20)
    assert wide == " " * 10 + s, "centralizada na largura pedida"
    assert U.fmt_total(None).startswith("TOTAL ") and "sem dados" in U.fmt_total(None)
    t = U.total_json({"a": {"7d": 90, "7d_pace": 50, "7d_left": 3.5 * 86400}})
    assert "09h20  queima 1,8×" in U.fmt_total(t), U.fmt_total(t)


def test_brief_and_json_carry_the_total(monkeypatch, capsys):
    U.COLOR = False
    monkeypatch.setattr(U, "get_windows", lambda a: fake_windows(40))
    assert U.show_brief([acc("a", "A (x)"), acc("b", "B (x)")])
    out = capsys.readouterr().out.splitlines()
    assert out[-1].strip().startswith("TOTAL ") and out[-1].endswith("queima 0,8× · 60% livre"), out[-1]
    assert out[-2] == "", "uma linha em branco antes do TOTAL"
    conf = {"accounts": [{"name": "a", "label": "A (x)", "config_dir": "~/.claude-a"}]}
    monkeypatch.setattr(U, "load_conf", lambda: conf)
    assert U.main(["--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["a"]["7d"] == 40 and d["_total"]["7d_free"] == 60 and d["_total"]["accounts"] == 1 and "7d_burn" in d["_total"]


def test_main_json_and_unknown_account(monkeypatch, tmp_path, capsys):
    conf = tmp_path / "accounts.json"
    conf.write_text(json.dumps({"accounts": [{"name": "gmail", "label": "Gmail (g@x)", "config_dir": "~/.claude-gmail"}]}))
    monkeypatch.setattr(U, "CONF", conf)
    monkeypatch.setattr(U, "get_windows", lambda a: fake_windows(5))
    assert U.main(["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert list(out) == ["gmail", "_total"] and out["gmail"]["7d"] == 5
    try:
        U.main(["nada"])
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("conta desconhecida deveria sair com 1")
    assert "conta desconhecida: nada" in capsys.readouterr().err
