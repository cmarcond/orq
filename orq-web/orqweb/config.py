"""Shared configuration: the `claude-multiconta` accounts (accounts.json) and paths.

The file comes from $CLAUDE_MULTICONTA_CONF or from ~/.config/claude-multiconta/accounts.json.
This module has no side effects on import; whoever needs it calls `load()`.
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
    """Where the claude-multiconta utilities live (claude-last, usage-all, orq)."""
    return Path(os.environ.get("ORQ_BIN", str(HOME / ".local/bin")))


def state_root() -> Path:
    return Path(os.environ.get("ORQ_STATE_DIR", str(HOME / ".cache/orq")))


def safe_label(label: str) -> str:
    """Folder/session name derived from the agent's label: `acervo@node0` -> `acervo-node0`."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", label)


def state_dir(label: str) -> Path:
    return state_root() / safe_label(label)


def session_name(label: str) -> str:
    """Name that `claude -n` receives and by which SendMessage addresses the session.

    SendMessage rejects the `@` of the label ("to must be a bare teammate name"), so the name is
    the sanitized label: `kaggle@server0` -> `kaggle-server0`; `kaggle~professor@server0` ->
    `kaggle-professor-server0`.
    """
    return safe_label(label)


def host_of(label: str) -> str:
    """`folder@machine` -> `machine`; without `@` it is the laptop."""
    return label.rsplit("@", 1)[1] if "@" in label else "laptop"


# Default palette by position in accounts.json; "color" in the account overrides. Readable in light and dark.
PALETTE = ["#2b6cb0", "#c05621", "#2f855a", "#805ad5", "#b7791f", "#c53030", "#0987a0", "#97266d"]


@dataclass
class Account:
    name: str
    config_dir: Path
    label: str
    email: str = ""
    color: str = PALETTE[0]

    @property
    def is_default(self) -> bool:
        return self.config_dir.resolve() == DEFAULT_CLAUDE_DIR.resolve()

    def env(self, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Environment to run `claude` on this account: CLAUDE_CONFIG_DIR, or nothing for the default one."""
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
    timezone: str = "America/Sao_Paulo"
    auto_switch: bool = True  # switch accounts on its own when the agent's quota runs out

    @property
    def labels(self) -> Dict[str, str]:
        return {n: a.label for n, a in self.accounts.items()}

    @property
    def colors(self) -> Dict[str, str]:
        return {n: a.color for n, a in self.accounts.items()}

    def account(self, name: str) -> Account:
        try:
            return self.accounts[name]
        except KeyError:
            raise KeyError(
                "unknown account: {}. Available: {}".format(name, ", ".join(self.accounts))
            ) from None


def parse(raw: dict) -> Config:
    accounts = {}
    for i, a in enumerate(raw.get("accounts", [])):
        d = Path(os.path.expanduser(a.get("config_dir") or "~/.claude"))
        accounts[a["name"]] = Account(
            name=a["name"],
            config_dir=d,
            label=(a.get("label") or a["name"]).split(" (")[0],
            email=a.get("email", ""),
            color=a.get("color") or PALETTE[i % len(PALETTE)],
        )
    return Config(
        accounts=accounts,
        permission_mode=raw.get("permission_mode") or "manual",
        hosts=list(raw.get("orq_hosts", [])),
        timezone=raw.get("timezone") or "America/Sao_Paulo",
        auto_switch=bool(raw.get("auto_switch", True)),
    )


def load(path: Optional[Path] = None) -> Config:
    p = path or conf_path()
    try:
        return parse(json.loads(p.read_text()))
    except FileNotFoundError:
        raise SystemExit(
            f"Could not read {p}. Run the claude-multiconta install.sh or set CLAUDE_MULTICONTA_CONF."
        ) from None
