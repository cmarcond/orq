"""The agent's state.json: what `orq --status-json` and the page read to list agents."""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


class StateFile:
    """Atomic write (tmp + replace): the file is never half-written for whoever reads it."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def write(self, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            cur = self.read()
            cur.update(fields)
            cur["ts"] = time.time()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(cur, ensure_ascii=False))
            os.replace(str(tmp), str(self.path))
            return cur


def list_agents(root: Path) -> list:
    """Terminal-less agents of this machine, with the state corrected by the pid (dead = closed)."""
    out = []
    for f in sorted(root.glob("*/state.json")):
        st = StateFile(f).read()
        if st.get("kind") != "sdk":
            continue
        if not pid_alive(st.get("pid")):
            st["state"] = "closed"
        out.append(st)
    return out
