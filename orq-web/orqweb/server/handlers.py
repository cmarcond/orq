"""Rotas HTTP da página: estáticos, /api/state, /api/projects, /api/agent/* (abrir, stream SSE, enviar,
permissão, interromper, parar).

Segurança: só responde aos Hosts permitidos (127.0.0.1/localhost e, se configurado, o IP da rede
privada); tudo em /api exige o token (cabeçalho X-Orq-Token, ou ?t= no SSE, que não aceita
cabeçalhos) ou um cookie de sessão obtido em /login com a senha; CSP restringe scripts a 'self' e
ao CDN.
"""

import json
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .assign import assign
from .auth import Auth, login_page
from .bridge import BridgeRegistry
from .state import StateCache, pick_host_and_label, provisional_agent

STATIC = Path(__file__).with_name("static")
CSP = (
    "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'"
)
TYPES = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
AGENT_RE = re.compile(r"^/api/agent/([^/]+)/(stream|send|permission|interrupt|stop|upload)$")
INBOX = "_inbox"
UPLOAD_MAX = 200 * 1024 * 1024


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
        self.put_file = None  # (host, path, data) -> erro ou None; None desliga /api/agent/<x>/upload
        self.token = token or secrets.token_urlsafe(16)
        self.auth: Optional[Auth] = None  # login por senha (rede); None ou desligado = só o token
        self.allowed_hosts = {"127.0.0.1", "localhost"}  # cabeçalho Host aceito
        self.local_host: Optional[str] = "laptop"  # nome desta máquina na página; None = sem agentes locais

    @property
    def login_required(self) -> bool:
        return bool(self.auth and self.auth.enabled)

    def valid_hosts(self) -> list:
        return ([self.local_host] if self.local_host else []) + list(self.conf.hosts)


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
            return self.headers.get("Host", "").split(":")[0] in ctx.allowed_hosts

        def sid(self) -> Optional[str]:
            return Auth.sid_from_cookie(self.headers.get("Cookie"))

        def authed(self, q: Optional[Dict[str, list]] = None) -> bool:
            if not self.ok_host():
                return False
            t = self.headers.get("X-Orq-Token", "") or ((q or {}).get("t") or [""])[0]
            if t and secrets.compare_digest(t, ctx.token):
                return True
            return bool(ctx.auth and ctx.auth.valid(self.sid()))

        def redirect(self, to: str, extra_headers: Tuple[Tuple[str, str], ...] = ()) -> None:
            self.send_response(303)
            self.send_header("Location", to)
            self.send_header("Content-Length", "0")
            for k, v in extra_headers:
                self.send_header(k, v)
            self.end_headers()

        def client_ip(self) -> str:
            return self.client_address[0] if self.client_address else "?"

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
            if u.path == "/login":
                return self.reply(200, login_page().encode(), "text/html")
            if u.path.startswith("/static/"):
                return self.static(u.path[len("/static/") :])
            if u.path == "/":
                if ctx.login_required and not self.authed(q):
                    return self.redirect("/login")
                return self.static("index.html")
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
            path = urlparse(self.path).path
            if path == "/login":
                return self.login()
            if path == "/logout":
                if ctx.auth:
                    ctx.auth.logout(self.sid())
                return self.redirect("/login", (("Set-Cookie", Auth.clear_cookie_header()),))
            if not self.authed():
                return self.reply(403, {"err": "token"})
            label, op = self.agent_path(path)
            if op == "upload":
                return self.upload(label)
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
            if op == "stop":  # a lista de agentes é consultada a cada 15 s; antecipa para a aba sumir logo
                threading.Timer(2.0, ctx.state.refresh_agents).start()
                threading.Timer(6.0, ctx.state.refresh_agents).start()
            return self.reply(200, {"msg": f"encerrando {label}"} if op == "stop" else {"ok": True})

        def login(self) -> None:
            if not self.ok_host():
                return self.reply(403, {"err": "host"})
            if not ctx.login_required:
                return self.reply(
                    404, login_page("login por senha desligado neste servidor").encode(), "text/html"
                )
            try:
                n = int(self.headers.get("Content-Length", 0))
                form = parse_qs(self.rfile.read(n).decode(errors="ignore"))
            except (ValueError, OSError):
                form = {}
            ip = self.client_ip()
            if ctx.auth.blocked(ip):
                return self.reply(
                    429, login_page("muitas tentativas; espere um minuto").encode(), "text/html"
                )
            sid = ctx.auth.login((form.get("password") or [""])[0], ip)
            print(f"login {'ok' if sid else 'falhou'} de {ip}", file=sys.stderr, flush=True)
            if not sid:
                return self.reply(403, login_page("senha incorreta").encode(), "text/html")
            return self.redirect("/", (("Set-Cookie", ctx.auth.cookie_header(sid)),))

        def convert_agent(self, body: dict) -> None:
            """Fecha o claude interativo daquele tmux e reabre a conversa como agente da página."""
            label = body.get("label") or ""
            host = label.rsplit("@", 1)[1] if "@" in label else ""
            if not ctx.convert or host not in ctx.valid_hosts():
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

        def upload(self, label: str) -> None:
            """Grava o corpo cru em <pasta do agente>/_inbox/<X-Filename>, na máquina do agente."""
            if not ctx.put_file:
                return self.reply(400, {"err": "envio de arquivos desligado"})
            name = safe_filename(unquote(self.headers.get("X-Filename", "")))
            if not name:
                return self.reply(400, {"err": "nome de arquivo inválido"})
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0 or n > UPLOAD_MAX:
                return self.reply(
                    413 if n > UPLOAD_MAX else 400,
                    {"err": f"tamanho inválido ({n} bytes; máximo {UPLOAD_MAX})"},
                )
            with ctx.state.lock:
                entry = next(
                    (a for a in ctx.state.agents if a.get("label") == label and a.get("state") != "closed"),
                    None,
                )
            if entry is None or not entry.get("dir"):
                return self.reply(404, {"err": "agente desconhecido"})
            data = self.rfile.read(n)
            path = f"{entry['dir'].rstrip('/')}/{INBOX}/{name}"
            err = ctx.put_file(entry.get("host") or ctx.local_host or "?", path, data)
            if err:
                return self.reply(500, {"err": f"não consegui gravar em {entry.get('host')}: {err}"})
            return self.reply(200, {"path": path, "bytes": n})

        def open_agent(self, body: dict) -> None:
            snap = ctx.state.snapshot()
            p, err = pick_host_and_label(body, ctx.conf.hosts, ctx.state.live_labels(), local=ctx.local_host)
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


def safe_filename(name: str) -> str:
    """Só o nome, sem caminho, sem controle; vazio se não sobrar nada."""
    base = name.replace("\\", "/").split("/")[-1].strip()
    base = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')
    return "" if base in ("", ".", "..") else base[:200]


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
