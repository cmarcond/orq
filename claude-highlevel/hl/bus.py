"""O fluxo de eventos de um run: numeração, histórico e fan-out.

Todo evento recebe um `seq` monotônico. É ele que permite reconectar sem furo nem
repetição: quem volta diz o último `seq` que viu e recebe só o que perdeu.
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

    # ------------------------------------------------------------- publicar

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

    # ------------------------------------------------------------- consumir

    def since(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._log if e["seq"] > seq]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def subscribe(self, since: int = 0) -> tuple[queue.Queue, list[dict[str, Any]]]:
        """Inscreve e devolve, atomicamente, o atraso a repor e a fila do que vier.

        Fazer as duas coisas sob o mesmo lock é o que garante que nenhum evento caia
        no vão entre "ler o histórico" e "começar a escutar".
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
