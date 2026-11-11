"""Life cycle of a run: starts the levels, gathers the events, accepts commands, shuts down."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import gates, pipelines
from .bus import Bus
from .events import Event, normalize
from .session import LevelSpec, Session
from .tailer import Tailer

WATCH_INTERVAL = 1.5
DRENO = 1.0           # seconds for the tailers to read the end of the transcript before closing


@dataclass
class Run:
    id: str
    cwd: Path
    task: str
    levels: list[LevelSpec]
    run_dir: Path
    bus: Bus
    bootstrap: str = ""
    pipeline: str = ""

    sessions: list[Session] = field(default_factory=list)
    status: str = "novo"          # novo (new) · subindo (starting) · rodando (running) · encerrado (finished) · erro (error)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    _tailers: list[Tailer] = field(default_factory=list)
    _watch: threading.Thread | None = None
    _stopping: threading.Event = field(default_factory=threading.Event)

    # ------------------------------------------------------------------ life

    def start_async(self) -> None:
        threading.Thread(target=self._start, name=f"run-{self.id}", daemon=True).start()

    def _start(self) -> None:
        self.status = "subindo"
        try:
            for depth, spec in enumerate(self.levels, start=1):
                self.sessions.append(Session(
                    spec=spec, cwd=self.cwd, level=depth,
                    raw_log=self.run_dir / f"pty-{spec.name}.log",
                    on_gate=lambda s, g: self._level(s, "gate", g.describe),
                ))
            # Bottom-up: the executor has to exist by the time the professor
            # calls ListAgents.
            for session in reversed(self.sessions):
                self._level(session, "starting")
                session.start()
                if not session.wait_ready():
                    raise RuntimeError(f"{session.spec.name} did not become ready")
                self._level(session, "ready")
                self._tailers.append(self._tail(session))

            self.status = "rodando"
            self.gravar_manifesto()
            top = self.sessions[0]
            top.send_text(self.bootstrap)
            self._level(top, "task delivered")
            self._watch = threading.Thread(target=self._watch_states, daemon=True)
            self._watch.start()
        except Exception as exc:                       # noqa: BLE001
            self.status, self.error = "erro", str(exc)
            self.bus.publish(Event("level", {"name": self.id, "state": "erro", "detail": str(exc)},
                                   _now(), 0, "run"))
            self.stop()

    def _tail(self, session: Session) -> Tailer:
        def on_record(record: dict) -> None:
            for event in normalize(record):
                event.level = session.level
                event.session = session.spec.name
                self.bus.publish(event)

        tailer = Tailer(session.session_id, self.cwd, on_record)
        tailer.start()
        return tailer

    def _level(self, session: Session, state: str, detail: str = "") -> None:
        self.bus.publish(Event(
            "level",
            {"name": session.spec.name, "state": state, "detail": detail,
             "pid": session.proc.pid if session.proc else None},
            _now(), session.level, session.spec.name,
        ))

    def _watch_states(self) -> None:
        """Publishes state changes of the levels — including a block, which the
        transcript does not record."""
        last: dict[str, str] = {}
        while not self._stopping.is_set():
            for session in self.sessions:
                state = session.activity()
                if last.get(session.spec.name) != state:
                    last[session.spec.name] = state
                    block = session.blocked_on() if state == "bloqueado" else None
                    self.bus.publish(Event(
                        "level",
                        {"name": session.spec.name, "state": state,
                         "detail": block.hint if block else "",
                         "options": block.options if block else None},
                        _now(), session.level, session.spec.name,
                    ))
            self._stopping.wait(WATCH_INTERVAL)

    def gravar_manifesto(self) -> None:
        """`run.json` is what makes a finished run keep existing after the
        server dies — `events.ndjson` alone does not even say what the task was."""
        manifesto = {
            "id": self.id,
            "cwd": str(self.cwd),
            "task": self.task,
            "pipeline": self.pipeline,
            "status": self.status,
            "criado_em": datetime.fromtimestamp(self.created_at).isoformat(timespec="seconds"),
            "niveis": [
                {"nome": s.name, "nivel": i, "cor": s.color, "modo": s.permission_mode}
                for i, s in enumerate(self.levels, start=1)
            ],
        }
        (self.run_dir / "run.json").write_text(
            json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")

    def stop(self) -> None:
        self._stopping.set()
        # Takes the sessions down first and only then the tailers, with one last pass:
        # stopping reading before writing stops loses the end of the transcript — usually
        # precisely the last message exchanged between the levels.
        for session in self.sessions:
            session.stop()
        time.sleep(DRENO)
        for tailer in self._tailers:
            tailer.stop()
        if self.status != "erro":
            self.status = "encerrado"
        self.gravar_manifesto()
        self.bus.publish(Event("level", {"name": self.id, "state": self.status}, _now(), 0, "run"))
        self.bus.close()

    # --------------------------------------------------------------- commands

    def session_by_name(self, name: str | None) -> Session:
        if not name:
            return self.sessions[0]
        for session in self.sessions:
            if name in (session.spec.name, session.spec.name.rsplit("-", 1)[0]):
                return session
        raise KeyError(f"unknown level: {name}")

    def send(self, to: str | None, text: str) -> str:
        session = self.session_by_name(to)
        session.send_text(text)
        return session.spec.name

    def keys(self, to: str | None, data: bytes) -> str:
        session = self.session_by_name(to)
        session.send_keys(data)
        return session.spec.name

    def allow(self, to: str | None) -> str:
        """Unblocks the stuck level using the right key for the dialog that is on screen."""
        session = self.session_by_name(to)
        block = session.blocked_on()
        if block is None:
            raise RuntimeError(f"{session.spec.name} is not blocked")
        session.send_keys(block.keys_to_allow)
        return session.spec.name

    # ------------------------------------------------------------------ state

    def pty(self, nome: str, linhas: int = 200) -> list[str]:
        session = self.session_by_name(nome)
        caminho = self.run_dir / f"pty-{session.spec.name}.log"
        return ler_pty(caminho, linhas)

    def state(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cwd": str(self.cwd),
            "task": self.task,
            "pipeline": self.pipeline,
            "status": self.status,
            "error": self.error,
            "criado_em": datetime.fromtimestamp(self.created_at).isoformat(timespec="seconds"),
            "last_seq": self.bus.last_seq,
            "niveis": [
                {
                    "nome": s.spec.name,
                    "nivel": s.level,
                    "cor": s.spec.color,
                    "modo": s.spec.permission_mode,
                    "pid": s.proc.pid if s.proc else None,
                    "estado": s.activity(),
                    "bloqueio": ([list(o) for o in b.options] if (b := s.blocked_on()) else None),
                    "transcript": str(s.transcript) if s.transcript else None,
                }
                for s in self.sessions
            ],
        }


def ler_pty(caminho: Path, linhas: int = 200) -> list[str]:
    """The last lines of a level's raw terminal."""
    if not caminho.exists():
        return []
    with caminho.open("rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 200_000))
        return gates.plain_lines(fh.read())[-linhas:]


class Registry:
    """The runs of this server instance."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def create(self, cwd: str, task: str, pipeline: str | None = None,
               mode: str = "auto", model: str | None = None) -> Run:
        receita = pipelines.pegar(pipeline)
        tag = "-" + uuid.uuid4().hex[:4]
        run_id = f"r{datetime.now():%Y%m%d-%H%M%S}{tag}"
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        run = Run(
            id=run_id, cwd=Path(cwd).expanduser(), task=task,
            levels=receita.montar(model=model, mode=mode, tag=tag),
            run_dir=run_dir, bus=Bus(run_dir / "events.ndjson"),
            bootstrap=receita.texto_bootstrap(task, tag), pipeline=receita.id,
        )
        run.cwd.mkdir(parents=True, exist_ok=True)
        run.gravar_manifesto()
        with self._lock:
            self._runs[run_id] = run
        run.start_async()
        return run

    def get(self, run_id: str) -> Run:
        """The live run. Raises KeyError if it exists only on disk — commanding a
        finished run makes no sense, and the error has to say so."""
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            return self._runs[run_id]

    # -------------------------------------------------------- live and archived

    def _manifesto(self, run_id: str) -> dict[str, Any] | None:
        caminho = self.root / run_id / "run.json"
        dados = (json.loads(caminho.read_text(encoding="utf-8")) if caminho.is_file()
                 else self._manifesto_dos_eventos(run_id))
        if dados is None:
            return None
        dados["arquivado"] = True
        dados.setdefault("status", "encerrado")
        for nivel in dados.get("niveis", []):
            nivel.setdefault("estado", "morto")
            nivel.setdefault("bloqueio", None)
        return dados

    def _manifesto_dos_eventos(self, run_id: str) -> dict[str, Any] | None:
        """Old run, with no manifest: what can be known comes from the events themselves."""
        caminho = self.root / run_id / "events.ndjson"
        if not caminho.is_file():
            return None
        tarefa, niveis = "(task not recorded)", {}
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            evento = json.loads(linha)
            if evento.get("kind") == "prompt" and tarefa.startswith("("):
                tarefa = evento["payload"].get("text", "")[:400]
            if evento.get("session") and evento.get("level"):
                niveis.setdefault(evento["session"], evento["level"])
        return {
            "id": run_id, "cwd": "", "task": tarefa, "status": "encerrado",
            "criado_em": "", "niveis": [
                {"nome": nome, "nivel": nivel, "cor": "36" if nivel == 1 else "33", "modo": ""}
                for nome, nivel in sorted(niveis.items(), key=lambda kv: kv[1])
            ],
        }

    def state_of(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            vivo = self._runs.get(run_id)
        if vivo is not None:
            return vivo.state()
        manifesto = self._manifesto(run_id)
        if manifesto is None:
            raise KeyError(run_id)
        manifesto["last_seq"] = len(self.events_of(run_id, 0))
        return manifesto

    def events_of(self, run_id: str, since: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            vivo = self._runs.get(run_id)
        if vivo is not None:
            return vivo.bus.since(since)
        caminho = self.root / run_id / "events.ndjson"
        if not caminho.is_file():
            raise KeyError(run_id)
        eventos = []
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            evento = json.loads(linha)
            if evento.get("seq", 0) > since:
                eventos.append(evento)
        return eventos

    def pty_of(self, run_id: str, nivel: str, linhas: int = 200) -> list[str]:
        with self._lock:
            vivo = self._runs.get(run_id)
        if vivo is not None:
            return vivo.pty(nivel, linhas)
        return ler_pty(self.root / run_id / f"pty-{nivel}.log", linhas)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            vivos = {rid: r.state() for rid, r in self._runs.items()}
        pastas = {p.parent for p in self.root.glob("*/events.ndjson")}
        pastas |= {p.parent for p in self.root.glob("*/run.json")}
        for pasta in sorted(pastas, key=lambda p: p.name, reverse=True):
            rid = pasta.name
            if rid in vivos:
                continue
            manifesto = self._manifesto(rid)
            if manifesto:
                vivos[rid] = manifesto
        return sorted(vivos.values(), key=lambda r: r["id"], reverse=True)

    def stop_all(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            run.stop()


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")
