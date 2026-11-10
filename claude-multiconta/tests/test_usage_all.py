"""usage-all: janelas, ritmo, formatação e saída JSON, sem rede (a API é substituída)."""

import datetime
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import sys

import pytest
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


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """Os testes daqui são da API e da formatação: nunca o banco do usage-hist da máquina."""
    monkeypatch.setattr(U, "load_snapshot", lambda: None)


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
    assert ws[2].scope == "Fable" and ws[2].title == "Fable 7d"
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
    body = [ln[2:] for ln in lines if ln[2:3] and ln[2:3] in "ABC"]
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
    s = U.fmt_total(t, {"a": "A"})
    assert s == "TOTAL  50% █████┃░░░░  queima 1,0×  zera ∞  reset A 3d00h", repr(s)
    assert U.fmt_total(None).startswith("TOTAL ") and "sem dados" in U.fmt_total(None)
    t = U.total_json({"a": {"7d": 90, "7d_pace": 50, "7d_left": 3.5 * 86400}})
    assert "queima 1,8×  zera 09h20" in U.fmt_total(t), U.fmt_total(t)


def test_brief_and_json_carry_the_total(monkeypatch, capsys):
    U.COLOR = False
    monkeypatch.setattr(U, "get_windows", lambda a: fake_windows(40))
    assert U.show_brief([acc("a", "A (x)"), acc("b", "B (x)")])
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith("TOTAL ") and "40% " in out[-1] and "% ████░┃░░░░  queima 0,8×  zera ∞" in out[-1], out[-1]
    assert len(out) == 5 and out[3] == "", "cabeçalho, duas contas, linha em branco e o TOTAL: sem legenda nem rodapé"
    assert sorted(ln[:2] for ln in out[1:3]) == ["  ", "→ "], "uma seta só (as duas contas empatam; qualquer uma serve)"
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
    assert list(out) == ["gmail", "_total", "_fonte"] and out["gmail"]["7d"] == 5 and out["_fonte"] == {"api": True}
    try:
        U.main(["nada"])
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("conta desconhecida deveria sair com 1")
    assert "conta desconhecida: nada" in capsys.readouterr().err


# ---------------------------------------------------------------- a melhor conta para abrir agora
def test_brief_notes_only_stale_data(monkeypatch, capsys):
    U.COLOR = False
    monkeypatch.setattr(U, "get_windows", lambda a: fake_windows(40))
    for mins, note in ((5, False), (40, True)):
        monkeypatch.setattr(U, "snap_age_min", lambda m=mins: m)
        U.show_brief([acc("a", "A (x)")])
        assert ("dados de 40 min atrás" in capsys.readouterr().out) is note


def test_verbose_is_compact(monkeypatch, capsys):
    U.COLOR = False
    table = {"a": fake_windows(80), "b": fake_windows(10), "c": ([], "sem credencial")}
    monkeypatch.setattr(U, "get_windows", lambda a: table[a.name])
    monkeypatch.setattr(U, "logged_email", lambda a: None)
    ok = U.show_verbose([acc("a", "A (x)"), acc("b", "B (x)"), acc("c", "C (x)")], datetime.timezone.utc)
    out = capsys.readouterr().out.splitlines()
    assert not ok
    assert out[0] == "→ B  g@x" and out[1].startswith("  sessão 5h ") and out[2].startswith("  semana 7d  10% ██"), out[:3]
    assert out[4] == "A  g@x" and "C  g@x" in out and "  ✗ sem credencial" in out
    assert out[-2].startswith("TOTAL ") and out[-1].startswith("→ B: 90 pts vencem em ") and out[-1].endswith("dados da API"), out[-2:]
    assert not any("ritmo" in ln or "reseta" in ln or "📊" in ln for ln in out), "sem textos longos"


def test_brief_highlights_the_best_account(monkeypatch, capsys):
    U.COLOR = True
    try:
        table = {"a": fake_windows(80), "b": fake_windows(10)}
        monkeypatch.setattr(U, "get_windows", lambda a: table[a.name])
        U.show_brief([acc("a", "A (x)"), acc("b", "B (x)")])
        out = capsys.readouterr().out
    finally:
        U.COLOR = False
    assert "\033[1m→ B         \033[0m" in out, "seta e nome da melhor em negrito"
    assert "→ A" not in out and "melhor agora" not in out


def u(d7, h5=0.0, left_h=48.0):
    return {"5h": h5, "7d": d7, "7d_left": None if left_h is None else int(left_h * 3600)}


def test_best_uses_first_what_expires_first():
    per = {
        "gmail": u(61, 29, left_h=8.4),  # sobra 39 que vencem em 8 h: 4,6 pts/h
        "work": u(41, 2, left_h=30),  # 59 em 30 h: 2 pts/h
        "work2": u(4, 13, left_h=142),  # 96 em 6 dias: 0,7 pts/h
        "_total": {},
    }
    assert [r["name"] for r in U.rank(per)] == ["gmail", "work", "work2"]
    name, why = U.best_account(per)
    assert name == "gmail" and "vencem em" in why and "4,6" in why


def test_best_guards_5h_window_and_small_leftover():
    per = {"gmail": u(97, 10, left_h=2), "work": u(30, 90, left_h=5), "work3": u(20, 0, left_h=100)}
    ranked = {r["name"]: r for r in U.rank(per)}
    assert not ranked["gmail"]["ok"] and "sobra só 3" in ranked["gmail"]["why"]
    assert not ranked["work"]["ok"] and "5 h em 90%" in ranked["work"]["why"]
    assert U.best_account(per)[0] == "work3"


def test_best_window_not_started_and_errors():
    per = {"gmail": u(0, 0, left_h=None), "work": {"err": "429"}}
    ranked = U.rank(per)
    assert ranked[0]["name"] == "gmail" and ranked[0]["ok"] and ranked[-1]["why"] == "sem dados de cota"


def test_best_stays_on_the_conversation_account_when_it_is_fine():
    per = {"gmail": u(61, 29, left_h=8), "work3": u(20, 10, left_h=100)}
    assert U.best_account(per, stay="work3")[0] == "work3"  # boa: fica, mesmo não sendo a primeira
    per["work3"] = u(20, 75, left_h=100)  # 5 h apertada
    assert U.best_account(per, stay="work3")[0] == "gmail"
    per["work3"] = u(90, 10, left_h=100)  # só 10 pts sobrando
    assert U.best_account(per, stay="work3")[0] == "gmail"


def test_best_when_nothing_is_fine():
    name, why = U.best_account({"gmail": u(97, 10), "work": u(99, 95)})
    assert name == "gmail" and why.startswith("nenhuma conta com folga")
    assert U.best_account({"gmail": {"err": "x"}}) == (None, "nenhuma conta com cota")


def test_main_melhor_prints_only_the_name(monkeypatch, tmp_path, capsys):
    conf = tmp_path / "accounts.json"
    conf.write_text(json.dumps({"accounts": [{"name": "gmail", "config_dir": "~/.g"}, {"name": "work", "config_dir": "~/.a"}]}))
    monkeypatch.setattr(U, "CONF", conf)
    table = {"gmail": u(61, 29, left_h=8), "work": u(10, 0, left_h=150)}
    monkeypatch.setattr(U, "usage_json", lambda acc: table[acc.name])
    assert U.main(["--melhor"]) == 0
    out = capsys.readouterr()
    assert out.out == "gmail\n" and out.err == ""  # silencioso
    assert U.main(["--melhor", "--fica", "work"]) == 0 and capsys.readouterr().out == "work\n"
    assert U.main(["--melhor", "-v"]) == 0
    err = capsys.readouterr().err
    assert "→ gmail" in err.replace("Gmail", "gmail") or "escolha:" in err
