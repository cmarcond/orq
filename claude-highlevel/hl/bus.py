"""The event stream of a run: numbering, history and fan-out.

Every event gets a monotonic `seq`. That is what allows reconnecting with no gap and
no repetition: whoever comes back says the last `seq` it saw and receives only what it missed.
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any

from .events import Event


class Bus:
    def __init__(self, ndjson: Path | None = None, keep: int = 5000) -> None:
        self.ndjson = ndjson
        self.keep = keep
        self._log: list[dict[str, Any]] = []
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._seq = 0
        self._sink = ndjson.open("a", encoding="utf-8") if ndjson else None

    # -------------------------------------------------------------- publish

    def publish(self, event: Event) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event.seq = self._seq
            data = event.as_dict()
            self._log.append(data)
            if len(self._log) > self.keep:
                del self._log[: len(self._log) - self.keep]
            subs = list(self._subs)
            if self._sink:
                self._sink.write(json.dumps(data, ensure_ascii=False) + "\n")
                self._sink.flush()
        for sub in subs:
            sub.put(data)
        return data

    # -------------------------------------------------------------- consume

    def since(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._log if e["seq"] > seq]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def subscribe(self, since: int = 0) -> tuple[queue.Queue, list[dict[str, Any]]]:
        """Subscribes and returns, atomically, the backlog to replay and the queue of what comes next.

        Doing both under the same lock is what guarantees that no event falls into
        the gap between "reading the history" and "starting to listen".
        """
        sub: queue.Queue = queue.Queue()
        with self._lock:
            backlog = [e for e in self._log if e["seq"] > since]
            self._subs.add(sub)
        return sub, backlog

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(sub)

    def close(self) -> None:
        with self._lock:
            if self._sink:
                self._sink.close()
                self._sink = None
