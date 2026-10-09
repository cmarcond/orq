"""Configuração compartilhada: as contas do `claude-multiconta` (accounts.json) e caminhos.

O arquivo vem de $CLAUDE_MULTICONTA_CONF ou de ~/.config/claude-multiconta/accounts.json.
Este módulo não tem efeitos colaterais na importação; quem precisa chama `load()`.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

HOME = Path.home()
DEFAULT_CONF = HOME / ".config/claude-multiconta/accounts.json"
DEFAULT_CLAUDE_DIR = HOME / ".claude"


def conf_path() -> Path:
    return Path(os.environ.get("CLAUDE_MULTICONTA_CONF", str(DEFAULT_CONF)))


def bin_dir() -> Path:
    """Onde estão os utilitários do claude-multiconta (claude-last, claude-usage-fast, orq)."""
    return Path(os.environ.get("ORQ_BIN", str(HOME / ".local/bin")))


def state_root() -> Path:
    return Path(os.environ.get("ORQ_STATE_DIR", str(HOME / ".cache/orq")))


def safe_label(label: str) -> str:
    """Nome de pasta/sessão a partir do rótulo do agente: `acervo@node0` -> `acervo-node0`."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", label)


def state_dir(label: str) -> Path:
    return state_root() / safe_label(label)


def host_of(label: str) -> str:
    """`pasta@maquina` -> `maquina`; sem `@` é o laptop."""
    return label.rsplit("@", 1)[1] if "@" in label else "laptop"


@dataclass
class Account:
    name: str
    config_dir: Path
    label: str
    email: str = ""

    @property
    def is_default(self) -> bool:
        return self.config_dir.resolve() == DEFAULT_CLAUDE_DIR.resolve()

    def env(self, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Ambiente para rodar o `claude` nesta conta: CLAUDE_CONFIG_DIR, ou nada para a conta padrão."""
        env = dict(os.environ if base is None else base)
        if self.is_default:
            env.pop("CLAUDE_CONFIG_DIR", None)
        else:
            env["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        return env


@dataclass
class Config:
    accounts: Dict[str, Account] = field(default_factory=dict)
    permission_mode: str = "manual"
    hosts: List[str] = field(default_factory=list)
    codex: bool = True
    timezone: str = "America/Sao_Paulo"

    @property
    def labels(self) -> Dict[str, str]:
        out = {n: a.label for n, a in self.accounts.items()}
        out["codex"] = "Codex"
        return out

    def account(self, name: str) -> Account:
        try:
            return self.accounts[name]
        except KeyError:
            raise KeyError(
                "conta desconhecida: {}. Disponíveis: {}".format(name, ", ".join(self.accounts))
            ) from None


def parse(raw: dict) -> Config:
    accounts = {}
    for a in raw.get("accounts", []):
        d = Path(os.path.expanduser(a.get("config_dir") or "~/.claude"))
        accounts[a["name"]] = Account(
            name=a["name"],
            config_dir=d,
            label=(a.get("label") or a["name"]).split(" (")[0],
            email=a.get("email", ""),
        )
    return Config(
        accounts=accounts,
        permission_mode=raw.get("permission_mode") or "manual",
        hosts=list(raw.get("orq_hosts", [])),
        codex=bool(raw.get("codex", True)),
        timezone=raw.get("timezone") or "America/Sao_Paulo",
    )


def load(path: Optional[Path] = None) -> Config:
    p = path or conf_path()
    try:
        return parse(json.loads(p.read_text()))
    except FileNotFoundError:
        raise SystemExit(
            f"Não consegui ler {p}. Rode o install.sh do claude-multiconta ou defina CLAUDE_MULTICONTA_CONF."
        ) from None
