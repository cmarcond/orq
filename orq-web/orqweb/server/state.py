"""Cache of what the page shows: quota, agents of all the machines and recent projects.

The quota is queried every USAGE_EVERY s (the API complains if it is frequent); the usage history (the
usage-hist database: today, pace, forecast, 24 h, alerts) every HISTORY_EVERY s and the agents every
AGENTS_EVERY s, these two only while the page is open (`touch()` marks that someone asked).
"""

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import Config
from .external import External

USAGE_EVERY = 150
HISTORY_EVERY = 60  # the collector writes every 5 min; reading the database is cheap (~0.2 s)
ACCOUNT_TTL = 30  # s: the panel of one account, requested on demand, is valid for this long
AGENTS_EVERY = 15
PROJECTS_TTL = 60
OPEN_WINDOW = 60  # s since the last request to consider the page open
CLOSING_TTL = (
    30  # s during which an agent told to close stays "closed" even if the machine still sees it alive
)
PROVISIONAL_TTL = (
    60  # s during which a just-opened agent stays in the list until the machine confirms it alive
)


class StateCache:
    def __init__(self, conf: Config, ext: External, clock=time.time, usage_host: Optional[str] = None):
        self.conf, self.ext, self.clock = conf, ext, clock
        self.usage_host = (
            usage_host  # None = this machine; otherwise, over ssh (the container has no credentials)
        )
        self.lock = threading.Lock()
        self.usage: Dict[str, Any] = {}
        self.usage_ts = 0.0
        self.history: Dict[str, Any] = {}  # usage-hist painel --json; {} without a database
        self.history_ts = 0.0
        self.account_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}  # account -> (when, panel)
        self.usage_table_cache: Dict[float, Tuple[float, Dict[str, Any]]] = {}  # hours -> (when, usage tab)
        self.agents: List[dict] = []
        self.links: List[dict] = []  # links between agents (orq-agent links), of all the machines
        self.agents_ts = 0.0
        self.agents_started = 0.0  # when the query that produced `agents` started (discards the older ones)
        self.host_errors: Dict[str, str] = {}
        self.projects: Optional[List[dict]] = None
        self.projects_ts = 0.0
        self.closing: Dict[str, float] = {}  # label -> until when to count as closed
        self.provisional: Dict[str, dict] = {}  # label -> just-opened entry, until the machine confirms
        self.on_agents: Optional[Callable[[List[dict]], Any]] = None  # called with the new list (migration)
        self.seen = clock()
        self._stop = threading.Event()

    # ---- queries
    def touch(self) -> None:
        self.seen = self.clock()

    def page_open(self) -> bool:
        return self.clock() - self.seen < OPEN_WINDOW

    def refresh_history(self) -> None:
        fn = getattr(self.ext, "history", None)
        h = fn(self.usage_host) if fn else {}
        with self.lock:
            self.history, self.history_ts = h, self.clock()

    def account_detail(self, name: str) -> Dict[str, Any]:
        """The panel of one account (usage-hist conta), with a short cache: opening and closing does not redo
        the query."""
        with self.lock:
            hit = self.account_cache.get(name)
        if hit and self.clock() - hit[0] < ACCOUNT_TTL:
            return hit[1]
        fn = getattr(self.ext, "account", None)
        data = fn(self.usage_host, name) if fn else {}
        if data:
            with self.lock:
                self.account_cache[name] = (self.clock(), data)
        return data

    def usage_table(self, hours: float) -> Dict[str, Any]:
        """The usage tab (usage-hist uso) for the requested window, with the same short cache as the account
        panel."""
        with self.lock:
            hit = self.usage_table_cache.get(hours)
        if hit and self.clock() - hit[0] < ACCOUNT_TTL:
            return hit[1]
        fn = getattr(self.ext, "usage_table", None)
        data = fn(self.usage_host, hours) if fn else {}
        if data:
            with self.lock:
                self.usage_table_cache[hours] = (self.clock(), data)
        return data

    def refresh_usage(self) -> None:
        u = self.ext.usage(self.usage_host)
        with self.lock:
            self.usage, self.usage_ts = u, self.clock()

    def refresh_agents(self) -> None:
        started = self.clock()
        ag, errs = self.ext.all_agents(self.conf.hosts)
        with self.lock:
            # Queries run in parallel (the loop, plus the ones scheduled by stop/downgrade) and take 20 s or
            # more when a machine does not respond. One that started before an agent opened may finish after
            # another that already saw it alive: applying it would erase the agent from the list for a cycle
            # (the page removes the tab and jumps to another one). Only the newest query counts.
            if started < self.agents_started:
                return
            self.agents_started = started
            now = self.clock()
            self.closing = {k: v for k, v in self.closing.items() if v > now}
            for a in ag:
                if a.get("label") in self.closing:
                    a["state"] = "closed"
            # The query to the machines takes seconds: if it started before an agent was opened, it comes
            # back without it (or with the "closed" state.json of the previous life) and the tab would
            # disappear from the page until the next query. The just-opened one stays in the list until the
            # machine returns it alive, for PROVISIONAL_TTL.
            self.provisional = {k: v for k, v in self.provisional.items() if now - v["ts"] < PROVISIONAL_TTL}
            seen = {a.get("label"): a for a in ag}
            for label, entry in list(self.provisional.items()):
                got = seen.get(label)
                if got is not None and got.get("state") != "closed":
                    del self.provisional[label]  # confirmed by the machine
                    continue
                ag = [a for a in ag if a.get("label") != label] + [entry]
            self.agents, self.host_errors, self.agents_ts = ag, errs, now
            # a link created by the page after the query started cannot be in the response: it stays.
            # Created before, the machine's response is the truth (it lists it, or it no longer exists).
            host_links = list(getattr(self.ext, "links", []) or [])
            pairs = {link_pair(lk) for lk in host_links}
            self.links = host_links + [
                lk for lk in self.links if lk.get("page_ts", 0) > started and link_pair(lk) not in pairs
            ]
        if self.on_agents:
            try:
                self.on_agents(list(ag))
            except Exception as e:  # the hook never brings the query down
                print(f"warning: agents hook failed: {e}")

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
                "history": self.history,
                "agents": list(self.agents),
                "links": list(self.links),
                "agents_ts": self.agents_ts,
                "host_errors": dict(self.host_errors),
                "accounts": list(self.conf.accounts),
                "labels": self.conf.labels,
                "colors": self.conf.colors,
            }

    # ---- agents opened by the page
    def live_labels(self) -> set:
        with self.lock:
            return {a["label"] for a in self.agents if a.get("state") != "closed"}

    def running_accounts(self) -> List[str]:
        with self.lock:
            return [a.get("account", "") for a in self.agents if a.get("state") != "closed"]

    def add_provisional(self, agent: dict) -> None:
        """Shows the just-opened agent before the next query to the machines."""
        with self.lock:
            self.closing.pop(agent["label"], None)
            self.provisional[agent["label"]] = agent
            self.agents = [a for a in self.agents if a.get("label") != agent["label"]] + [agent]

    def add_link(self, record: dict) -> None:
        """Link just created by the page: shows at once; `page_ts` tells `refresh_agents` when to drop it."""
        pair = link_pair(record)
        with self.lock:
            self.links = [lk for lk in self.links if link_pair(lk) != pair] + [
                dict(record, page_ts=self.clock())
            ]

    def drop_link(self, local: str, remote: str) -> None:
        pair = frozenset((local, remote))
        with self.lock:
            self.links = [lk for lk in self.links if link_pair(lk) != pair]

    def drop_provisional(self, label: str) -> None:
        """Undoes `add_provisional` (the reopen failed): the next query to the machines says what is left."""
        with self.lock:
            self.provisional.pop(label, None)
            self.agents = [a for a in self.agents if a.get("label") != label]

    def mark_closing(self, label: str) -> None:
        """Agent told to close: it is `closed` at once, and for CLOSING_TTL s even if the machine, queried
        while claude is still exiting (up to 15 s of grace), returns it alive."""
        with self.lock:
            self.closing[label] = self.clock() + CLOSING_TTL
            for a in self.agents:
                if a.get("label") == label:
                    a["state"] = "closed"

    # ---- background loops
    def _usage_loop(self) -> None:
        while not self._stop.is_set():
            self.refresh_usage()
            self._stop.wait(USAGE_EVERY)

    def _history_loop(self) -> None:
        while not self._stop.is_set():
            if self.page_open():
                self.refresh_history()
            self._stop.wait(HISTORY_EVERY)

    def _agents_loop(self) -> None:
        while not self._stop.is_set():
            if self.page_open():
                self.refresh_agents()
            self._stop.wait(AGENTS_EVERY)

    def start_background(self) -> None:
        threading.Thread(target=self._usage_loop, daemon=True).start()
        threading.Thread(target=self._history_loop, daemon=True).start()
        threading.Thread(target=self._agents_loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


def link_pair(lk: dict) -> frozenset:
    return frozenset((lk.get("local"), lk.get("remote")))


def provisional_agent(label: str, host: str, account: str, cwd: str, now: float, **extra: Any) -> dict:
    """Entry of a just-opened agent; `extra` carries what the state.json will have (name, parent, level)."""
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
    """Validates the request to open a project and builds p = {host, cwd, account, mode, label}.

    `local` is this machine's name (None when the server runs no local agents, as in the container).
    """
    from .assign import label_for

    host, cwd = body.get("host"), body.get("cwd")
    valid = ([local] if local else []) + list(hosts)
    if host not in valid or not cwd:
        return None, "invalid project"
    p = {"host": host, "cwd": cwd, "account": body.get("account"), "mode": body.get("mode") or "resume"}
    p["label"] = label_for(cwd, host, taken)
    return p, None
