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
    body = [ln for ln in lines if ln[:1] in "ABC"]
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


def test_main_json_and_unknown_account(monkeypatch, tmp_path, capsys):
    conf = tmp_path / "accounts.json"
    conf.write_text(json.dumps({"accounts": [{"name": "gmail", "label": "Gmail (g@x)", "config_dir": "~/.claude-gmail"}]}))
    monkeypatch.setattr(U, "CONF", conf)
    monkeypatch.setattr(U, "get_windows", lambda a: fake_windows(5))
    assert U.main(["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert list(out) == ["gmail"] and out["gmail"]["7d"] == 5
    try:
        U.main(["nada"])
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("conta desconhecida deveria sair com 1")
    assert "conta desconhecida: nada" in capsys.readouterr().err
