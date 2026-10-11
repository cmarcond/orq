import json
from pathlib import Path

import pytest

from orqweb import config

RAW = {
    "accounts": [
        {"name": "work", "email": "a@x", "label": "Work (a@x)", "config_dir": "~/.claude"},
        {
            "name": "gmail",
            "email": "g@x",
            "label": "Gmail (g@x)",
            "config_dir": "~/.claude-gmail",
            "color": "#123456",
        },
    ],
    "permission_mode": "auto",
    "orq_hosts": ["server0"],
}


def test_parse_accounts_and_options():
    c = config.parse(RAW)
    assert list(c.accounts) == ["work", "gmail"]
    assert c.accounts["gmail"].label == "Gmail"
    assert c.accounts["gmail"].config_dir == Path.home() / ".claude-gmail"
    assert c.permission_mode == "auto" and c.hosts == ["server0"]
    assert c.labels == {"work": "Work", "gmail": "Gmail"}
    assert c.colors == {"work": config.PALETTE[0], "gmail": "#123456"}, (
        "paleta por posição; color sobrescreve"
    )


def test_defaults():
    c = config.parse({"accounts": []})
    assert c.permission_mode == "manual" and c.hosts == []


def test_account_env_default_vs_other():
    c = config.parse(RAW)
    base = {"PATH": "/bin", "CLAUDE_CONFIG_DIR": "/tmp/outra"}
    assert "CLAUDE_CONFIG_DIR" not in c.accounts["work"].env(base)
    assert c.accounts["gmail"].env(base)["CLAUDE_CONFIG_DIR"] == str(Path.home() / ".claude-gmail")
    assert c.accounts["gmail"].env(base)["PATH"] == "/bin"


def test_unknown_account_message():
    c = config.parse(RAW)
    with pytest.raises(KeyError, match="conta desconhecida: x. Disponíveis: work, gmail"):
        c.account("x")


def test_load_from_env_path(tmp_path, monkeypatch):
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps(RAW))
    monkeypatch.setenv("CLAUDE_MULTICONTA_CONF", str(p))
    assert config.load().permission_mode == "auto"


def test_load_missing_is_a_clean_exit(tmp_path):
    with pytest.raises(SystemExit, match="Não consegui ler"):
        config.load(tmp_path / "nada.json")


def test_labels_and_paths(monkeypatch, tmp_path):
    assert config.safe_label("acervo@node0") == "acervo-node0"
    assert config.host_of("acervo@node0") == "node0"
    assert config.host_of("solto") == "laptop"
    monkeypatch.setenv("ORQ_STATE_DIR", str(tmp_path))
    assert config.state_dir("a@b") == tmp_path / "a-b"
