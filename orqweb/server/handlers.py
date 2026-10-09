"""Rotas HTTP da página: estáticos, /api/state, /api/projects, /api/agent/* (abrir, stream SSE, enviar,
permissão, interromper, parar).

Segurança: só responde a Host 127.0.0.1/localhost; tudo em /api exige o token (cabeçalho
X-Orq-Token, ou ?t= no SSE, que não aceita cabeçalhos); CSP restringe scripts a 'self' e ao CDN.
"""

import json
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .assign import assign
from .bridge import BridgeRegistry
from .state import StateCache, pick_host_and_label, provisional_agent

STATIC = Path(__file__).with_name("static")
CSP = (
    "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
)
TYPES = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
AGENT_RE = re.compile(r"^/api/agent/([^/]+)/(stream|send|permission|interrupt|stop)$")


class Context:
    """Tudo que as rotas precisam; montado por app.serve e pelos testes."""

    def __init__(
        self,
        conf,
        state: StateCache,
        bridges: BridgeRegistry,
        spawn: Callable,
        token: Optional[str] = None,
        convert: Optional[Callable] = None,
    ):
        self.conf, self.state, self.bridges, self.spawn = conf, state, bridges, spawn
        self.convert = convert  # (host, label) -> {ok, label, how, msg}; None desliga /api/agent/convert
        self.token = token or secrets.token_urlsafe(16)


def make_handler(ctx: Context):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "orq-web"

        def log_message(self, *a: Any) -> None:  # silêncio; erros reais sobem como exceção
            pass

        # ---- utilidades
        def reply(self, code: int, body: Any, ctype: str = "application/json") -> None:
            b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def ok_host(self) -> bool:
            return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

        def authed(self, q: Optional[Dict[str, list]] = None) -> bool:
            t = self.headers.get("X-Orq-Token", "") or ((q or {}).get("t") or [""])[0]
            return self.ok_host() and secrets.compare_digest(t, ctx.token)

        @staticmethod
        def agent_path(path: str) -> Tuple[Optional[str], Optional[str]]:
            m = AGENT_RE.match(path)
            return (unquote(m.group(1)), m.group(2)) if m else (None, None)

        def read_json(self) -> Optional[dict]:
            try:
                n = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, OSError):
                return None

        # ---- GET
        def do_GET(self) -> None:
            if not self.ok_host():
                return self.reply(403, {"err": "host"})
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                return self.static("index.html")
            if u.path.startswith("/static/"):
                return self.static(u.path[len("/static/") :])
            if not self.authed(q):
                return self.reply(403, {"err": "abra pelo link que o orq-web mostrou"})
            ctx.state.touch()
            if u.path == "/api/state":
                return self.reply(200, ctx.state.snapshot())
            if u.path == "/api/projects":
                items = ctx.state.get_projects()
                return self.reply(200, {"items": items})
            label, op = self.agent_path(u.path)
            if op == "stream":
                return self.stream(label, int((q.get("since") or ["0"])[0]))
            return self.reply(404, {"err": "?"})

        def static(self, name: str) -> None:
            p = (STATIC / name).resolve()
            if STATIC.resolve() not in p.parents or not p.is_file():
                return self.reply(404, {"err": "?"})
            self.reply(200, p.read_bytes(), TYPES.get(p.suffix, "application/octet-stream"))

        def stream(self, label: str, since: int) -> None:
            """SSE: cada linha do agente vira um `data:`. Uma ponte por aba; a anterior é fechada."""
            b, old = ctx.bridges.open(label, since)
            if old:
                old.close()
            self.send_response(200)
            for k, v in (
                ("Content-Type", "text/event-stream; charset=utf-8"),
                ("Cache-Control", "no-store"),
                ("Connection", "keep-alive"),
                ("X-Accel-Buffering", "no"),
            ):
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(b": ok\n\n")
                self.wfile.flush()
                for line in b.lines():
                    self.wfile.write(b"data: " + line.rstrip(b"\n") + b"\n\n")
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                ctx.bridges.release(label, b)

        # ---- POST
        def do_POST(self) -> None:
            if not self.authed():
                return self.reply(403, {"err": "token"})
            body = self.read_json()
            if body is None:
                return self.reply(400, {"err": "json"})
            path = urlparse(self.path).path
            if path == "/api/agent/open":
                return self.open_agent(body)
            if path == "/api/agent/convert":
                return self.convert_agent(body)
            label, op = self.agent_path(path)
            if op is None:
                return self.reply(404, {"err": "?"})
            msg = build_message(op, body)
            if msg is None:
                return self.reply(400, {"err": "mensagem vazia" if op == "send" else "pedido inválido"})
            err = ctx.bridges.deliver(label, msg)
            if err:
                return self.reply(500, {"err": err})
            return self.reply(200, {"msg": f"encerrando {label}"} if op == "stop" else {"ok": True})

        def convert_agent(self, body: dict) -> None:
            """Fecha o claude interativo daquele tmux e reabre a conversa como agente da página."""
            label = body.get("label") or ""
            host = label.rsplit("@", 1)[1] if "@" in label else ""
            if not ctx.convert or host not in ["laptop"] + list(ctx.conf.hosts):
                return self.reply(400, {"err": "agente inválido"})
            with ctx.state.lock:
                entry = next(
                    (a for a in ctx.state.agents if a.get("label") == label and a.get("kind") != "sdk"), None
                )
            if entry is None:
                return self.reply(404, {"err": "esse agente não é um claude de terminal"})
            if not entry.get("tmux"):
                return self.reply(
                    400, {"err": "painel da grade antiga do orq, sem tmux próprio; feche-o no terminal"}
                )
            res = ctx.convert(host, label)
            if not res.get("ok"):
                return self.reply(500, {"err": res.get("msg") or "falhou"})
            new_label = res.get("label") or label
            ctx.state.add_provisional(
                provisional_agent(
                    new_label, host, entry.get("account") or "?", entry.get("dir") or "", time.time()
                )
            )
            return self.reply(200, {"label": new_label, "how": res.get("how"), "msg": res.get("msg")})

        def open_agent(self, body: dict) -> None:
            snap = ctx.state.snapshot()
            p, err = pick_host_and_label(body, ctx.conf.hosts, ctx.state.live_labels())
            if err:
                return self.reply(400, {"err": err})
            assign([p], snap["usage"], list(ctx.conf.accounts), ctx.state.running_accounts())
            if p["mode"] == "resume":
                live = [
                    a
                    for a in snap["agents"]
                    if a.get("kind") != "sdk"
                    and a.get("tmux")
                    and a.get("state") != "closed"
                    and a.get("host") == p["host"]
                    and (a.get("dir") or "").rstrip("/") == p["cwd"].rstrip("/")
                ]
                if live:
                    who = live[0]["label"]
                    return self.reply(
                        409, {"err": f"há um claude aberto no terminal nessa pasta ({who}); use ⇄ converter"}
                    )
            err = ctx.spawn(p["host"], p["label"], p["cwd"], p["acc"], p["mode"])
            if err:
                return self.reply(500, {"err": err})
            ctx.state.add_provisional(
                provisional_agent(p["label"], p["host"], p["acc"], p["cwd"], time.time())
            )
            return self.reply(200, {"label": p["label"], "acc": p["acc"], "why": p["why"]})

    return Handler


def build_message(op: str, body: dict) -> Optional[dict]:
    """O que vai para o stdin do claude em cada operação da API."""
    if op == "send":
        text = (body.get("text") or "").strip()
        if not text:
            return None
        return {"type": "user", "message": {"role": "user", "content": text}}
    if op == "permission":
        if not body.get("request_id"):
            return None
        allow = bool(body.get("allow"))
        resp = (
            {"behavior": "allow", "updatedInput": body.get("input") or {}}
            if allow
            else {"behavior": "deny", "message": "negado pela página do orq"}
        )
        return {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": body["request_id"], "response": resp},
        }
    if op == "interrupt":
        return {
            "type": "control_request",
            "request_id": "int-" + secrets.token_hex(4),
            "request": {"subtype": "interrupt"},
        }
    if op == "stop":
        return {"orq": "quit"}
    return None
