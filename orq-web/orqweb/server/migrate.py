"""Troca automática de conta: um agente cuja conta ficou sem cota é reaberto, na mesma conversa, na
conta com mais folga, e a última mensagem da página é reenviada.

O supervisor marca `state: quota` (e guarda em `retry` a última mensagem) quando o claude avisa que a
cota acabou. A cada consulta às máquinas, `Migrator.check` procura esses agentes e, numa thread, roda
`orq-agent spawn --mode resume --restart` na conta escolhida (o `claude-last --as` copia a conversa).
Desligável com `"auto_switch": false` no accounts.json.
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
        self.busy: Dict[str, float] = {}  # rótulo -> quando começou a migrar (evita duas migrações)
        self.lock = threading.Lock()
        self.history: List[dict] = []  # o que foi migrado (a página mostra num aviso)

    def choose(self, agent: dict) -> Optional[str]:
        """Conta com mais folga que não seja a atual; None se nenhuma tem cota."""
        cur = agent.get("account")
        snap = self.state.snapshot()
        usage = {n: u for n, u in snap["usage"].items() if n != cur}
        p = {"account": None}
        assign([p], usage, [n for n in self.conf.accounts if n != cur], self.state.running_accounts())
        acc = p.get("acc")
        return acc if acc and acc in usage and "nenhuma" not in p.get("why", "") else None

    def check(self, agents: Optional[List[dict]] = None) -> List[str]:
        """Dispara a migração dos agentes sem cota; devolve os rótulos que começaram a migrar."""
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
        """Reabre `a` noutra conta e reenvia a mensagem pendente. Devolve a conta nova, ou None."""
        lab = a["label"]
        acc = self.choose(a)
        if not acc:
            self.log(f"{lab}: sem cota na conta {a.get('account')} e nenhuma outra com folga")
            return None
        host, cwd = a.get("host") or "laptop", a.get("dir") or ""
        self.log(f"{lab}: conta {a.get('account')} sem cota; reabrindo na conta {acc}")
        err = self.spawn(host, lab, cwd, acc, "resume", restart=True)
        if err:
            self.log(f"{lab}: não consegui reabrir na conta {acc}: {err}")
            return None
        self.state.add_provisional(
            provisional_agent(lab, host, acc, cwd, time.time(), migrated_from=a.get("account"))
        )
        retry = (a.get("retry") or "").strip()
        if retry:
            err = self.deliver(lab, {"type": "user", "message": {"role": "user", "content": retry}})
            if err:
                self.log(f"{lab}: reaberto na conta {acc}, mas não consegui reenviar a mensagem: {err}")
        self.history.append({"label": lab, "from": a.get("account"), "to": acc, "ts": time.time()})
        return acc
