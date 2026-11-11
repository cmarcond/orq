"""Level recipes: reading the TOML (tomllib and minimal reader), labels and plan for adding levels."""

import sys

import pytest

from orqweb.server import pipelines as P

SAMPLE = '''
titulo = "a → b"   # comment
descricao = 'simples'

# stray comment
[[niveis]]
nome = "chefe"
cor = "#123456"
papel = """
You are in charge of `{abaixo}`.
Line with "quotes" and a backslash \\\\ in the middle.
"""

[[niveis]]
nome = "meio"
papel = """Above `{acima}`, below `{abaixo}`."""
'''


def test_minimal_reader_covers_the_subset():
    d = P._parse_minimal(SAMPLE)
    assert d["titulo"] == "a → b" and d["descricao"] == "simples"
    assert [n["nome"] for n in d["niveis"]] == ["chefe", "meio"]
    assert d["niveis"][0]["papel"] == (
        'You are in charge of `{abaixo}`.\nLine with "quotes" and a backslash \\ in the middle.\n'
    )
    assert d["niveis"][1]["papel"] == "Above `{acima}`, below `{abaixo}`."
    with pytest.raises(ValueError):
        P._parse_minimal("x = 1")  # a number is not supported (and is not used)


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib only on 3.11+")
def test_minimal_reader_matches_tomllib_on_shipped_recipes():
    import tomllib

    for f in P.PIPELINE_DIR.glob("*.toml"):
        txt = f.read_text(encoding="utf-8")
        assert P._parse_minimal(txt) == tomllib.loads(txt), f.name
    assert P._parse_minimal(SAMPLE) == tomllib.loads(SAMPLE)


def test_catalog_has_shipped_recipes_and_default():
    cat = P.catalog()
    assert P.DEFAULT in cat and "professor-arquiteto-executor" in cat
    s = cat[P.DEFAULT].summary()
    assert s["niveis"] == ["professor"] and s["cores"]["professor"].startswith("#")
    assert cat["professor-arquiteto-executor"].summary()["niveis"] == ["professor", "arquiteto"]
    assert P.get(None).id == P.DEFAULT
    with pytest.raises(KeyError):
        P.get("nao-existe")


def test_catalog_skips_broken_files(tmp_path):
    (tmp_path / "boa.toml").write_text('titulo = "t"\n[[niveis]]\nnome = "x"\npapel = "p"\n')
    (tmp_path / "sem-nivel.toml").write_text('titulo = "t"\n')
    (tmp_path / "nome-ruim.toml").write_text('[[niveis]]\nnome = "a@b"\npapel = "p"\n')
    (tmp_path / "sem-papel.toml").write_text('[[niveis]]\nnome = "a"\n')
    (tmp_path / ".oculta.toml").write_text('[[niveis]]\nnome = "a"\npapel = "p"\n')
    assert sorted(P.catalog(tmp_path)) == ["boa"]


def test_level_labels_round_trip():
    assert P.level_label("kaggle@server0", "professor") == "kaggle~professor@server0"
    assert P.level_label("solto", "professor") == "solto~professor@laptop"
    assert P.base_of_label("kaggle~professor@server0") == "kaggle@server0"
    assert P.base_of_label("kaggle@server0") is None


def test_plan_levels_resolves_neighbors_with_session_names():
    p = P.get("professor-arquiteto-executor")
    plan = P.plan_levels(p, "kaggle@server0")
    assert [x["label"] for x in plan] == ["kaggle~arquiteto@server0", "kaggle~professor@server0"]
    arq, prof = plan
    assert arq["parent"] == "kaggle@server0" and prof["parent"] == "kaggle~arquiteto@server0"
    assert arq["level"] == "arquiteto" and prof["level"] == "professor"
    # the neighbours go in with the session name (without @), which is what SendMessage accepts
    assert "`kaggle-server0`" in arq["role"] and "`kaggle-professor-server0`" in arq["role"]
    assert "`kaggle-arquiteto-server0`" in prof["role"]
    assert "{abaixo}" not in prof["role"] and "{acima}" not in arq["role"]
    assert "{eu}" not in prof["role"] and "`kaggle-professor-server0`" in prof["role"]
    assert "`kaggle-arquiteto-server0`" in arq["role"]  # the architect's {eu}
    assert "@" not in arq["role"].split("`")[1]
    assert prof["color"] == "#2b6cb0"


def test_levels_above_follows_parent_chain_and_ignores_closed():
    agents = [
        {"label": "k@h", "state": "waiting"},
        {"label": "k~arquiteto@h", "parent": "k@h", "state": "waiting"},
        {"label": "k~professor@h", "parent": "k~arquiteto@h", "state": "working"},
        {"label": "velho~professor@h", "parent": "k@h", "state": "closed"},
    ]
    assert P.levels_above(agents, "k@h") == ["k~arquiteto@h", "k~professor@h"]
    assert P.levels_above(agents, "k~professor@h") == []
    assert P.levels_above([{"label": "a", "parent": "b"}, {"label": "b", "parent": "a"}], "a") == ["b"]


def test_multi_recipe_lists_executors_only_in_bottom_level():
    p = P.get("professor-executores")
    assert p.multi and p.summary()["multi"] is True and not P.get("professor-executor").multi
    base = {"label": "kaggle@server0", "name": "kaggle-server0", "host": "server0", "dir": "/h/k"}
    others = [
        {"label": "dados@laptop", "host": "laptop", "dir": "/Users/c/dados"}
    ]  # no name: sanitised label
    lst = P.executors_list(base, others)
    assert lst.splitlines() == [
        "- `kaggle-server0` (the one in the tab): machine server0, folder /h/k",
        "- `dados-laptop`: machine laptop, folder /Users/c/dados",
    ]
    (prof,) = P.plan_levels(p, "kaggle@server0", lst)
    assert "- `dados-laptop`: machine laptop" in prof["role"] and "{executores}" not in prof["role"]
    assert "`kaggle-server0` is the main one" in prof["role"] and "`kaggle-professor-server0`" in prof["role"]
    # three-level recipe: only the bottom one gets the list; the top one is left without the marker
    arq, top = P.plan_levels(P.get("professor-arquiteto-executor"), "k@laptop", "- `x`")
    assert "{executores}" not in top["role"] and "{executores}" not in arq["role"]
