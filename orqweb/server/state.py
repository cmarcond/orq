"""Cache do que a página mostra: cota, agentes de todas as máquinas e projetos recentes.

A cota é consultada a cada USAGE_EVERY s (a API reclama se for frequente); os agentes a cada
AGENTS_EVERY s, e só enquanto a página está aberta (`touch()` marca que alguém pediu).
"""

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from .external import External

USAGE_EVERY = 150
AGENTS_EVERY = 15
PROJECTS_TTL = 60
OPEN_WINDOW = 60  # s desde o último pedido para considerar a página aberta


class StateCache:
    def __init__(self, conf: Config, ext: External, clock=time.time, usage_host: Optional[str] = None):
        self.conf, self.ext, self.clock = conf, ext, clock
        self.usage_host = usage_host  # None = esta máquina; senão, por ssh (o container não tem credenciais)
        self.lock = threading.Lock()
        self.usage: Dict[str, Any] = {}
        self.usage_ts = 0.0
        self.agents: List[dict] = []
        self.agents_ts = 0.0
        self.host_errors: Dict[str, str] = {}
        self.projects: Optional[List[dict]] = None
        self.projects_ts = 0.0
        self.seen = clock()
        self._stop = threading.Event()

    # ---- consultas
    def touch(self) -> None:
        self.seen = self.clock()

    def page_open(self) -> bool:
        return self.clock() - self.seen < OPEN_WINDOW

    def refresh_usage(self) -> None:
        u = self.ext.usage(self.usage_host)
        with self.lock:
            self.usage, self.usage_ts = u, self.clock()

    def refresh_agents(self) -> None:
        ag, errs = self.ext.all_agents(self.conf.hosts)
        with self.lock:
            self.agents, self.host_errors, self.agents_ts = ag, errs, self.clock()

    def get_projects(self) -> List[dict]:
        with self.lock:
            fresh = self.projects is not None and self.clock() - self.projects_ts < PROJECTS_TTL
            if fresh:
                return list(self.projects or [])
        items, _ = self.ext.all_projects(self.conf.hosts)
        with self.lock:
            self.projects, self.projects_ts = items, self.clock()
            return list(items)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "usage": self.usage,
                "usage_ts": self.usage_ts,
                "agents": list(self.agents),
                "agents_ts": self.agents_ts,
                "host_errors": dict(self.host_errors),
                "accounts": list(self.conf.accounts),
                "labels": self.conf.labels,
                "colors": self.conf.colors,
            }

    # ---- agentes abertos pela página
    def live_labels(self) -> set:
        with self.lock:
            return {a["label"] for a in self.agents if a.get("state") != "closed"}

    def running_accounts(self) -> List[str]:
        with self.lock:
            return [a.get("account", "") for a in self.agents if a.get("state") != "closed"]

    def add_provisional(self, agent: dict) -> None:
        """Mostra o agente recém-aberto antes da próxima consulta às máquinas."""
        with self.lock:
            self.agents = [a for a in self.agents if a.get("label") != agent["label"]] + [agent]

    # ---- laços de fundo
    def _usage_loop(self) -> None:
        while not self._stop.is_set():
            self.refresh_usage()
            self._stop.wait(USAGE_EVERY)

    def _agents_loop(self) -> None:
        while not self._stop.is_set():
            if self.page_open():
                self.refresh_agents()
            self._stop.wait(AGENTS_EVERY)

    def start_background(self) -> None:
        threading.Thread(target=self._usage_loop, daemon=True).start()
        threading.Thread(target=self._agents_loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


def provisional_agent(label: str, host: str, account: str, cwd: str, now: float) -> dict:
    return {
        "label": label,
        "host": host,
        "account": account,
        "dir": cwd,
        "state": "starting",
        "msg": "",
        "ts": now,
        "kind": "sdk",
        "orq": True,
    }


def pick_host_and_label(body: dict, hosts: List[str], taken: set) -> Tuple[Optional[dict], Optional[str]]:
    """Valida o pedido de abrir projeto e monta p = {host, cwd, account, mode, label}."""
    from .assign import label_for
    from .external import LAPTOP

    host, cwd = body.get("host"), body.get("cwd")
    if host not in [LAPTOP] + list(hosts) or not cwd:
        return None, "projeto inválido"
    p = {"host": host, "cwd": cwd, "account": body.get("account"), "mode": body.get("mode") or "resume"}
    p["label"] = label_for(cwd, host, taken)
    return p, None
