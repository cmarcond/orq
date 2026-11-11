"""Local server: REST to command, SSE to watch.

SSE instead of WebSocket because the flow is one-way (server → browser) and because
it already comes with reconnection through `Last-Event-ID` — which is exactly our `seq`. That way
the whole project stays free of external dependencies.
"""
from __future__ import annotations

import json
import queue
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import pipelines
from .orchestrator import Registry

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
HEARTBEAT = 15.0
KEY_ALIASES = {
    "enter": b"\r", "esc": b"\x1b", "up": b"\x1b[A", "down": b"\x1b[B",
    "tab": b"\t", "ctrl-c": b"\x03",
}
_ROUTES: list[tuple[str, re.Pattern[str], str]] = []


def route(method: str, pattern: str):
    def decorate(func):
        _ROUTES.append((method, re.compile(f"^{pattern}$"), func.__name__))
        return func
    return decorate


class Handler(BaseHTTPRequestHandler):
    server_version = "HighLevel"
    registry: Registry

    # -------------------------------------------------------------- utilities

    def log_message(self, fmt: str, *args: Any) -> None:      # silences the default log
        pass

    def _json(self, payload: Any, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _query(self) -> dict[str, str]:
        _, _, qs = self.path.partition("?")
        return dict(pair.split("=", 1) for pair in qs.split("&") if "=" in pair)

    # ----------------------------------------------------------------- router

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        for verb, pattern, name in _ROUTES:
            if verb != method:
                continue
            match = pattern.match(path)
            if match:
                try:
                    getattr(self, name)(*match.groups())
                except KeyError as exc:
                    self._json({"erro": f"not found: {exc}"}, 404)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:                       # noqa: BLE001
                    self._json({"erro": str(exc)}, 400)
                return
        self._static(path)

    # ------------------------------------------------------------------- API

    @route("GET", "/api/pipelines")
    def get_pipelines(self) -> None:
        self._json([p.resumo() for p in pipelines.catalogo().values()])

    @route("GET", "/api/runs")
    def get_runs(self) -> None:
        self._json(self.registry.list())

    @route("POST", "/api/runs")
    def post_runs(self) -> None:
        body = self._body()
        if not body.get("cwd") or not body.get("task"):
            self._json({"erro": "cwd and task are required"}, 400)
            return
        run = self.registry.create(
            cwd=body["cwd"], task=body["task"],
            pipeline=body.get("pipeline") or None,
            mode=body.get("permission_mode", "auto"), model=body.get("model"),
        )
        self._json(run.state(), 201)

    @route("GET", r"/api/runs/([\w.-]+)")
    def get_run(self, run_id: str) -> None:
        self._json(self.registry.state_of(run_id))

    @route("GET", r"/api/runs/([\w.-]+)/events")
    def get_events(self, run_id: str) -> None:
        self._json(self.registry.events_of(run_id, int(self._query().get("since", 0))))

    @route("GET", r"/api/runs/([\w.-]+)/pty")
    def get_pty(self, run_id: str) -> None:
        consulta = self._query()
        nivel = urllib.parse.unquote(consulta.get("nivel", ""))
        linhas = int(consulta.get("linhas", 200))
        self._json({"nivel": nivel, "linhas": self.registry.pty_of(run_id, nivel, linhas)})

    @route("POST", r"/api/runs/([\w.-]+)/message")
    def post_message(self, run_id: str) -> None:
        body = self._body()
        if not body.get("text"):
            self._json({"erro": "text is required"}, 400)
            return
        alvo = self.registry.get(run_id).send(body.get("to"), body["text"])
        self._json({"entregue_a": alvo})

    @route("POST", r"/api/runs/([\w.-]+)/keys")
    def post_keys(self, run_id: str) -> None:
        body = self._body()
        raw = body.get("data", "")
        data = KEY_ALIASES.get(str(raw).lower(), str(raw).encode())
        alvo = self.registry.get(run_id).keys(body.get("to"), data)
        self._json({"entregue_a": alvo})

    @route("POST", r"/api/runs/([\w.-]+)/allow")
    def post_allow(self, run_id: str) -> None:
        alvo = self.registry.get(run_id).allow(self._body().get("to"))
        self._json({"liberado": alvo})

    @route("POST", r"/api/runs/([\w.-]+)/stop")
    def post_stop(self, run_id: str) -> None:
        run = self.registry.get(run_id)
        run.stop()
        self._json({"status": run.status})

    # ------------------------------------------------------------------- SSE

    @route("GET", r"/api/runs/([\w.-]+)/stream")
    def get_stream(self, run_id: str) -> None:
        since = int(self.headers.get("Last-Event-ID") or self._query().get("since", 0))
        try:
            run = self.registry.get(run_id)
        except KeyError:
            # Archived run: delivers the whole history and ends. There is nothing to
            # wait for, and holding the connection would only make the EventSource reconnect in a loop.
            self._abrir_sse()
            for evento in self.registry.events_of(run_id, since):
                self._emit(evento)
            self.wfile.write(b"event: fim\ndata: {}\n\n")
            self.wfile.flush()
            return
        # Subscribing and reading the backlog in the same step: this is what keeps an event
        # from falling into the gap between reconnecting and listening again.
        sub, backlog = run.bus.subscribe(since)
        self._abrir_sse()
        try:
            for event in backlog:
                self._emit(event)
            while True:
                try:
                    self._emit(sub.get(timeout=HEARTBEAT))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            run.bus.unsubscribe(sub)

    def _abrir_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _emit(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        self.wfile.write(f"id: {event['seq']}\ndata: {payload}\n\n".encode())
        self.wfile.flush()

    # ------------------------------------------------------------ static files

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self._json({"erro": "not found"}, 404)
            return
        kind = {".html": "text/html", ".js": "text/javascript",
                ".css": "text/css", ".json": "application/json"}.get(target.suffix, "text/plain")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "127.0.0.1", port: int = 7333, runs_root: Path = Path("runs")) -> None:
    Handler.registry = Registry(runs_root)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"HighLevel at http://{host}:{port}  (runs in {runs_root})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down runs…", flush=True)
    finally:
        Handler.registry.stop_all()
        httpd.server_close()
