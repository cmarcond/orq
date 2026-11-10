"""Segue o transcript JSONL de uma sessão e entrega registro por registro."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from . import config


class Tailer(threading.Thread):
    """`tail -F` de um JSONL, tolerante a arquivo ainda inexistente e linha parcial."""

    def __init__(
        self,
        session_id: str,
        cwd: Path,
        on_record: Callable[[dict], None],
        poll: float = 0.25,
        from_start: bool = True,
    ) -> None:
        super().__init__(name=f"tail-{session_id[:8]}", daemon=True)
        self.session_id = session_id
        self.cwd = cwd
        self.on_record = on_record
        self.poll = poll
        self.from_start = from_start
        self._stop = threading.Event()
        self.path: Path | None = None
        self.offset = 0
        self._pending = b""

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            if self.path is None:
                self.path = config.find_transcript(self.session_id, self.cwd)
                if self.path is None:
                    time.sleep(self.poll)
                    continue
                self.offset = 0 if self.from_start else self.path.stat().st_size
            try:
                size = self.path.stat().st_size
            except FileNotFoundError:
                self.path = None
                continue
            if size < self.offset:      # truncado/reescrito: recomeça
                self.offset, self._pending = 0, b""
            if size > self.offset:
                self._drain(size)
            else:
                time.sleep(self.poll)

    def _drain(self, size: int) -> None:
        with self.path.open("rb") as fh:
            fh.seek(self.offset)
            data = fh.read(size - self.offset)
        self.offset += len(data)
        buf = self._pending + data
        *lines, self._pending = buf.split(b"\n")
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue            # linha ainda sendo escrita; vem completa depois
            self.on_record(record)
