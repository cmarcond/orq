"""Conjuntos (orqweb/server/sets.py): formato, validação, catálogo em disco, conflitos, plano e fotografia."""

import json

import pytest

from orqweb.server import pipelines, sets

HOSTS = ["laptop", "server0", "node3"]
ACCS = ["gmail", "work3"]
RECIPES = list(pipelines.catalog())


def good():
    return {
        "nome": "Artigo Kaggle",
        "descricao": "modelo + texto",
        "permissao": "auto",
        "agentes": [
            {"maquina": "server0", "pasta": "~/Projects/kaggle/", "conta": "work3", "prompt": "modelo"},
            {"maquina": "node3", "pasta": "~/Projects/paper", "modo": "new", "tarefa": "comece"},
        ],
        "lider": {"receita": "professor-executores", "prompt": "chefe", "tarefa": "coordene"},
    }


def test_set_id_is_a_safe_filename():
    assert sets.set_id("Artigo Kaggle") == "artigo-kaggle"
    assert sets.set_id("  ../../x  ") == "x"
    assert sets.set_id("Ação ç!") == "a-o"
    assert sets.set_id("") == "" and sets.set_id(None) == ""
    assert len(sets.set_id("a" * 100)) == 60


def test_validate_normalizes_defaults_and_strips():
    out = sets.validate(good(), HOSTS, ACCS, RECIPES)
    assert out["nome"] == "Artigo Kaggle" and out["permissao"] == "auto"
    a1, a2 = out["agentes"]
    assert a1["pasta"] == "~/Projects/kaggle" and a1["modo"] == "resume" and a1["tarefa"] == ""
    assert a2["conta"] is None and a2["modo"] == "new" and a2["prompt"] == ""
    assert out["lider"] == {"receita": "professor-executores", "prompt": "chefe", "tarefa": "coordene"}
    # sem líder, sem permissão, receita padrão quando `lider` vem vazio
    d = good()
    del d["lider"], d["permissao"]
    out = sets.validate(d, HOSTS, ACCS, RECIPES)
    assert out["lider"] is None and out["permissao"] is None
    d["lider"] = {}
    assert sets.validate(d, HOSTS, ACCS, RECIPES)["lider"]["receita"] == sets.DEFAULT_RECIPE


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda d: d.update(nome=""), "`nome`"),
        (lambda d: d.update(extra=1), "campo desconhecido: extra"),
        (lambda d: d.update(permissao="tudo"), "`permissao`"),
        (lambda d: d.update(agentes=[]), "`agentes`"),
        (lambda d: d.update(agentes="x"), "`agentes`"),
        (lambda d: d["agentes"].append("x"), "agente 3: precisa ser um objeto"),
        (lambda d: d["agentes"][0].update(maquina="marte"), "`maquina` 'marte'"),
        (lambda d: d["agentes"][0].update(pasta=""), "`pasta`"),
        (lambda d: d["agentes"][0].update(modo="talvez"), "`modo`"),
        (lambda d: d["agentes"][0].update(conta="ninguem"), "`conta` 'ninguem'"),
        (lambda d: d["agentes"][0].update(cor="azul"), "agente 1: campo desconhecido: cor"),
        (
            lambda d: d["agentes"].append({"maquina": "server0", "pasta": "/home/x/Projects/kaggle"}),
            "repetido",
        ),
        (lambda d: d.update(lider="prof"), "`lider`"),
        (lambda d: d["lider"].update(receita="nao-existe"), "`receita` 'nao-existe'"),
        (lambda d: d["lider"].update(cor="x"), "lider: campo desconhecido"),
    ],
)
def test_validate_reports_each_problem(mutate, needle):
    d = good()
    mutate(d)
    with pytest.raises(ValueError) as e:
        sets.validate(d, HOSTS, ACCS, RECIPES)
    assert needle in str(e.value)


def test_validate_reports_all_problems_at_once_and_rejects_non_objects():
    d = good()
    d["nome"], d["permissao"] = "", "x"
    with pytest.raises(ValueError) as e:
        sets.validate(d, HOSTS, ACCS, RECIPES)
    assert "`nome`" in str(e.value) and "`permissao`" in str(e.value)
    with pytest.raises(ValueError):
        sets.validate([1], HOSTS, ACCS, RECIPES)


def test_same_dir_handles_tilde_and_home():
    assert sets.same_dir("~/Projects/x/", "/home/user/Projects/x")
    assert sets.same_dir("/Users/user/Projects/x", "~/Projects/x")
    assert sets.same_dir("/tmp/a", "/tmp/a/")
    assert not sets.same_dir("~/Projects/x", "~/Projects/y")
    assert not sets.same_dir("/tmp/a", "~/tmp/a")


def test_catalog_save_load_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("ORQ_SETS_DIR", str(tmp_path / "conj"))
    assert sets.catalog() == {}
    data = sets.validate(good(), HOSTS, ACCS, RECIPES)
    sid = sets.save(data)
    assert sid == "artigo-kaggle" and (tmp_path / "conj" / "artigo-kaggle.json").exists()
    on_disk = json.loads((tmp_path / "conj" / "artigo-kaggle.json").read_text())
    assert on_disk["agentes"][1] == {
        "maquina": "node3",
        "pasta": "~/Projects/paper",
        "modo": "new",
        "tarefa": "comece",
    }
    assert "conta" not in on_disk["agentes"][1] and on_disk["lider"]["receita"] == "professor-executores"
    (tmp_path / "conj" / "quebrado.json").write_text("{")
    (tmp_path / "conj" / "lista.json").write_text("[1]")
    (tmp_path / "conj" / ".oculto.json").write_text("{}")
    assert list(sets.catalog()) == ["artigo-kaggle"]
    assert sets.load("artigo-kaggle")["nome"] == "Artigo Kaggle"
    for bad in ("nao-existe", "../artigo-kaggle", "", "Artigo Kaggle"):
        with pytest.raises(KeyError):
            sets.load(bad)
    assert sets.delete("artigo-kaggle") and not sets.delete("artigo-kaggle") and not sets.delete("../x")
    assert sets.catalog() == {}


def agent(label, host, cwd, **extra):
    a = {"label": label, "host": host, "dir": cwd, "state": "waiting", "kind": "sdk", "account": "gmail"}
    a.update(extra)
    return a


def test_conflicts_find_live_agents_in_the_same_folders():
    live = [
        agent("kaggle@server0", "server0", "/home/user/Projects/kaggle"),
        agent("paper@node3", "node3", "/home/user/Projects/paper", state="closed"),
        agent("t@node3", "node3", "/home/user/Projects/paper", kind="term", tmux="s"),
        agent(
            "kaggle~professor@server0",
            "server0",
            "/home/user/Projects/kaggle",
            parent="kaggle@server0",
        ),
    ]
    d = sets.validate(good(), HOSTS, ACCS, RECIPES)
    assert sets.conflicts(d, live) == ["kaggle@server0", "t@node3"]
    assert sets.conflicts(d, []) == []


def test_plan_gives_unique_labels_and_assigns_accounts_for_the_whole_set():
    d = sets.validate(good(), HOSTS, ACCS, RECIPES)
    usage = {"gmail": {"7d": 10, "7d_pace": 50}, "work3": {"7d": 40, "7d_pace": 50}}
    items = sets.plan(d, {"kaggle@server0"}, usage, ACCS, [])
    assert [it["label"] for it in items] == ["kaggle-2@server0", "paper@node3"]
    assert items[0]["acc"] == "work3" and items[0]["why"] == "fixa no conjunto"
    assert items[1]["acc"] == "gmail" and items[1]["mode"] == "new" and items[1]["tarefa"] == "comece"
    assert (
        items[0]["prompt"] == "modelo"
        and items[0]["host"] == "server0"
        and items[0]["cwd"] == "~/Projects/kaggle"
    )


def test_summary_counts_open_agents():
    d = sets.validate(good(), HOSTS, ACCS, RECIPES)
    s = sets.summary("artigo-kaggle", d, [agent("kaggle@server0", "server0", "~/Projects/kaggle")])
    assert s["n"] == 2 and s["lider"] and s["maquinas"] == ["node3", "server0"]
    assert s["abertos"] == ["kaggle@server0"] and s["permissao"] == "auto"
    assert sets.summary("x", {"agentes": "lixo"})["n"] == 0


def test_snapshot_from_open_tabs_with_levels_and_links():
    cat = pipelines.catalog()
    live = [
        agent("a@laptop", "laptop", "/p/a", permission="auto", role="sou o a", set="velho"),
        agent("b@server0", "server0", "/p/b", permission="auto"),
        agent("a~professor@laptop", "laptop", "/p/a", parent="a@laptop", level="professor"),  # sem `pipeline`
        agent("morto@laptop", "laptop", "/p/m", state="closed"),
        {"label": "t@laptop", "host": "laptop", "dir": "/t", "state": "waiting", "kind": "term"},
    ]
    links = [{"local": "a@laptop", "remote": "b@server0", "up": True}]
    out, avisos = sets.snapshot(live, links, cat)
    assert avisos == []
    assert out["permissao"] == "auto" and out["lider"] == {"receita": "professor-executores"}
    assert out["agentes"] == [
        {"maquina": "laptop", "pasta": "/p/a", "modo": "resume", "conta": "gmail", "prompt": "sou o a"},
        {"maquina": "server0", "pasta": "/p/b", "modo": "resume", "conta": "gmail"},
    ]
    # sem vínculo, a receita de um executor só; o b vem primeiro porque é ele que tem níveis
    live2 = [
        agent("a@laptop", "laptop", "/p/a", permission="auto"),
        agent("b@server0", "server0", "/p/b", permission="manual"),
        agent("b~professor@server0", "server0", "/p/b", parent="b@server0", level="professor"),
    ]
    out, avisos = sets.snapshot(live2, [], cat)
    assert out["lider"] == {"receita": "professor-executor"} and "permissao" not in out
    assert (
        [a["maquina"] for a in out["agentes"]] == ["server0", "laptop"]
        and avisos
        and "permiss" in avisos[0]
    )
    # dois professores: só o primeiro vira líder, com aviso
    live3 = live2 + [agent("a~professor@laptop", "laptop", "/p/a", parent="a@laptop", level="professor")]
    out, avisos = sets.snapshot(live3, [], cat)
    assert out["agentes"][0]["maquina"] == "laptop" and any("b@server0" in w for w in avisos)
    # nível que gravou a receita: ela vale, sem inferir (mesmo sem vínculo de pé)
    live4 = [
        agent("a@laptop", "laptop", "/p/a"),
        agent(
            "a~professor@laptop",
            "laptop",
            "/p/a",
            parent="a@laptop",
            level="professor",
            pipeline="professor-executores",
        ),
    ]
    assert sets.snapshot(live4, [], cat)[0]["lider"] == {"receita": "professor-executores"}
    # nada aberto: conjunto vazio, sem líder
    out, avisos = sets.snapshot([], [], cat)
    assert out["agentes"] == [] and "lider" not in out


def test_infer_recipe_prefers_multi_when_linked():
    cat = pipelines.catalog()
    assert sets.infer_recipe(["professor"], True, cat) == "professor-executores"
    assert sets.infer_recipe(["professor"], False, cat) == "professor-executor"
    assert sets.infer_recipe(["professor", "arquiteto"], False, cat) == "professor-arquiteto-executor"
    assert sets.infer_recipe(["ninguem"], False, cat) == sets.DEFAULT_RECIPE


def test_clean_drops_empty_fields():
    c = sets.clean(
        {
            "nome": "x",
            "descricao": "",
            "permissao": None,
            "agentes": [{"maquina": "l", "pasta": "/p", "conta": None}],
            "lider": None,
        }
    )
    assert c == {"nome": "x", "agentes": [{"maquina": "l", "pasta": "/p", "modo": "resume"}]}
