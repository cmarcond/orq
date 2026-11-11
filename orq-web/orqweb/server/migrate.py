"""Automatic account switch: an agent whose account ran out of quota is reopened, in the same
conversation, on the account with the most slack, and the last message from the page is resent.

The supervisor sets `state: quota` (and keeps the last message in `retry`) when claude warns that the
quota has run out. On each query to the machines, `Migrator.check` looks for those agents and, in a thread,
runs `orq-agent spawn --mode resume --restart` on the chosen account (`claude-last --as` copies the
conversation). Can be turned off with `"auto_switch": false` in accounts.json.
"""

import threading
import time
from typing import Callable, Dict, List, Optional

from .assign import assign
from .state import StateCache, provisional_agent


class Migrator:
    def __init__(
        self,
        conf,
        state: StateCache,
        spawn: Callable,
        deliver: Callable[[str, dict], Optional[str]],
        log: Callable[[str], None] = print,
        enabled: bool = True,
    ):
        self.conf, self.state, self.spawn, self.deliver, self.log = conf, state, spawn, deliver, log
        self.enabled = enabled
        self.busy: Dict[str, float] = {}  # label -> when it started migrating (avoids two migrations)
        self.lock = threading.Lock()
        self.history: List[dict] = []  # what was migrated (the page shows it in a notice)

    def choose(self, agent: dict) -> Optional[str]:
        """Account with the most slack other than the current one; None if none has quota."""
        cur = agent.get("account")
        snap = self.state.snapshot()
        usage = {n: u for n, u in snap["usage"].items() if n != cur}
        p = {"account": None}
        assign([p], usage, [n for n in self.conf.accounts if n != cur], self.state.running_accounts())
        acc = p.get("acc")
        return acc if acc and acc in usage and "no account" not in p.get("why", "") else None

    def check(self, agents: Optional[List[dict]] = None) -> List[str]:
        """Triggers the migration of the agents out of quota; returns the labels that started migrating."""
        if not self.enabled:
            return []
        if agents is None:
            with self.state.lock:
                agents = list(self.state.agents)
        started = []
        now = time.time()
        with self.lock:
            self.busy = {k: v for k, v in self.busy.items() if now - v < 180}
            for a in agents:
                lab = a.get("label")
                if a.get("state") != "quota" or a.get("kind") != "sdk" or not lab or lab in self.busy:
                    continue
                self.busy[lab] = now
                started.append(lab)
                threading.Thread(target=self.migrate, args=(dict(a),), daemon=True).start()
        return started

    def migrate(self, a: dict) -> Optional[str]:
        """Reopens `a` on another account and resends the pending message. Returns the new account or None."""
        lab = a["label"]
        acc = self.choose(a)
        if not acc:
            self.log(f"{lab}: out of quota on account {a.get('account')} and no other one with slack")
            return None
        host, cwd = a.get("host") or "laptop", a.get("dir") or ""
        self.log(f"{lab}: account {a.get('account')} out of quota; reopening on account {acc}")
        # The provisional entry goes in before the spawn: reopening takes seconds (ends the old one, copies
        # the conversation, starts the new one) and a query to the machines in the meantime would return the
        # agent as closed, the tab would disappear and the page would jump to another one.
        self.state.add_provisional(
            provisional_agent(lab, host, acc, cwd, time.time(), migrated_from=a.get("account"))
        )
        err = self.spawn(host, lab, cwd, acc, "resume", restart=True)
        if err:
            self.log(f"{lab}: could not reopen on account {acc}: {err}")
            self.state.drop_provisional(lab)
            return None
        retry = (a.get("retry") or "").strip()
        if retry:
            err = self.deliver(lab, {"type": "user", "message": {"role": "user", "content": retry}})
            if err:
                self.log(f"{lab}: reopened on account {acc}, but could not resend the message: {err}")
        self.history.append({"label": lab, "from": a.get("account"), "to": acc, "ts": time.time()})
        return acc
