"""Cache do que a página mostra: cota, agentes de todas as máquinas e projetos recentes.

A cota é consultada a cada USAGE_EVERY s (a API reclama se for frequente); os agentes a cada
AGENTS_EVERY s, e só enquanto a página está aberta (`touch()` marca que alguém pediu).
"""

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import Config
from .external import External

USAGE_EVERY = 150
AGENTS_EVERY = 15
PROJECTS_TTL = 60
OPEN_WINDOW = 60  # s desde o último pedido para considerar a página aberta
CLOSING_TTL = 30  # s em que um agente mandado encerrar fica "closed" mesmo que a máquina ainda o veja vivo
PROVISIONAL_TTL = 60  # s em que um agente recém-aberto fica na lista até a máquina confirmá-lo vivo


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
        self.closing: Dict[str, float] = {}  # rótulo -> até quando contar como fechado
        self.provisional: Dict[str, dict] = {}  # rótulo -> entrada recém-aberta, até a máquina confirmar
        self.on_agents: Optional[Callable[[List[dict]], Any]] = None  # chamado com a lista nova (migração)
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
            now = self.clock()
            self.closing = {k: v for k, v in self.closing.items() if v > now}
            for a in ag:
                if a.get("label") in self.closing:
                    a["state"] = "closed"
            # A consulta às máquinas leva segundos: se começou antes de um agente ser aberto, volta sem ele
            # (ou com o state.json "closed" da vida anterior) e a aba sumiria da página até a próxima
            # consulta. O recém-aberto fica na lista até a máquina o devolver vivo, por PROVISIONAL_TTL.
            self.provisional = {k: v for k, v in self.provisional.items() if now - v["ts"] < PROVISIONAL_TTL}
            seen = {a.get("label"): a for a in ag}
            for label, entry in list(self.provisional.items()):
                got = seen.get(label)
                if got is not None and got.get("state") != "closed":
                    del self.provisional[label]  # confirmado pela máquina
                    continue
                ag = [a for a in ag if a.get("label") != label] + [entry]
            self.agents, self.host_errors, self.agents_ts = ag, errs, now
        if self.on_agents:
            try:
                self.on_agents(list(ag))
            except Exception as e:  # o gancho nunca derruba a consulta
                print(f"aviso: gancho de agentes falhou: {e}")

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
            self.closing.pop(agent["label"], None)
            self.provisional[agent["label"]] = agent
            self.agents = [a for a in self.agents if a.get("label") != agent["label"]] + [agent]

    def mark_closing(self, label: str) -> None:
        """Agente mandado encerrar: fica `closed` já, e por CLOSING_TTL s mesmo que a máquina, consultada
        enquanto o claude ainda está saindo (até 15 s de graça), o devolva vivo."""
        with self.lock:
            self.closing[label] = self.clock() + CLOSING_TTL
            for a in self.agents:
                if a.get("label") == label:
                    a["state"] = "closed"

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


def provisional_agent(label: str, host: str, account: str, cwd: str, now: float, **extra: Any) -> dict:
    """Entrada de agente recém-aberto; `extra` leva o que o state.json vai ter (name, parent, level)."""
    from ..config import session_name

    a = {
        "label": label,
        "host": host,
        "account": account,
        "dir": cwd,
        "state": "starting",
        "msg": "",
        "ts": now,
        "kind": "sdk",
        "orq": True,
        "name": session_name(label),
    }
    a.update({k: v for k, v in extra.items() if v is not None})
    return a


def pick_host_and_label(
    body: dict, hosts: List[str], taken: set, local: Optional[str] = "laptop"
) -> Tuple[Optional[dict], Optional[str]]:
    """Valida o pedido de abrir projeto e monta p = {host, cwd, account, mode, label}.

    `local` é o nome desta máquina (None quando o servidor não roda agentes locais, como no container).
    """
    from .assign import label_for

    host, cwd = body.get("host"), body.get("cwd")
    valid = ([local] if local else []) + list(hosts)
    if host not in valid or not cwd:
        return None, "projeto inválido"
    p = {"host": host, "cwd": cwd, "account": body.get("account"), "mode": body.get("mode") or "resume"}
    p["label"] = label_for(cwd, host, taken)
    return p, None
