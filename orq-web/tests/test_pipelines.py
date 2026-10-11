"""Receitas de níveis: leitura do TOML (tomllib e leitor mínimo), rótulos e plano de subida."""

import sys

import pytest

from orqweb.server import pipelines as P

SAMPLE = '''
titulo = "a → b"   # comentário
descricao = 'simples'

# comentário solto
[[niveis]]
nome = "chefe"
cor = "#123456"
papel = """
Você manda em `{abaixo}`.
Linha com "aspas" e barra \\\\ no meio.
"""

[[niveis]]
nome = "meio"
papel = """Acima `{acima}`, abaixo `{abaixo}`."""
'''


def test_minimal_reader_covers_the_subset():
    d = P._parse_minimal(SAMPLE)
    assert d["titulo"] == "a → b" and d["descricao"] == "simples"
    assert [n["nome"] for n in d["niveis"]] == ["chefe", "meio"]
    assert d["niveis"][0]["papel"] == 'Você manda em `{abaixo}`.\nLinha com "aspas" e barra \\ no meio.\n'
    assert d["niveis"][1]["papel"] == "Acima `{acima}`, abaixo `{abaixo}`."
    with pytest.raises(ValueError):
        P._parse_minimal("x = 1")  # número não é suportado (e não é usado)


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib só no 3.11+")
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
    # os vizinhos entram com o nome de sessão (sem @), que é o que o SendMessage aceita
    assert "`kaggle-server0`" in arq["role"] and "`kaggle-professor-server0`" in arq["role"]
    assert "`kaggle-arquiteto-server0`" in prof["role"]
    assert "{abaixo}" not in prof["role"] and "{acima}" not in arq["role"]
    assert "{eu}" not in prof["role"] and "`kaggle-professor-server0`" in prof["role"]
    assert "`kaggle-arquiteto-server0`" in arq["role"]  # {eu} do arquiteto
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
