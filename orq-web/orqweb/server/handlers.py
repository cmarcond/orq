"""Rotas HTTP da página: estáticos, /api/state, /api/projects, /api/pipelines, /api/agent/* (abrir,
stream SSE, enviar, permissão, interromper, parar, níveis: upgrade/downgrade).

Segurança: só responde aos Hosts permitidos (127.0.0.1/localhost e, se configurado, o IP da rede
privada); tudo em /api exige o token (cabeçalho X-Orq-Token, ou ?t= no SSE, que não aceita
cabeçalhos) ou um cookie de sessão obtido em /login com a senha; CSP restringe scripts a 'self' e
ao CDN.
"""

import json
import mimetypes
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

from ..agent.files import OUTBOX
from ..config import session_name
from . import pipelines
from .assign import assign
from .auth import Auth, login_page
from .bridge import BridgeRegistry
from .state import StateCache, link_pair, pick_host_and_label, provisional_agent

STATIC = Path(__file__).with_name("static")
CSP = (
    "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'"
)
TYPES = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
AGENT_RE = re.compile(
    r"^/api/agent/([^/]+)/(stream|send|permission|interrupt|stop|upload|upgrade|downgrade|reopen|files|file"
    r"|link|unlink)$"
)
INBOX = "_inbox"
UPLOAD_MAX = 200 * 1024 * 1024
# arquivo da _outbox servido ao navegador: nada nele roda nem enxerga a página (um .html do agente não
# vira script na nossa origem), e o tipo declarado é o que vale
FILE_CSP = "default-src 'none'; sandbox; style-src 'unsafe-inline'; img-src 'self' data:; media-src 'self'"
INLINE_OK = ("image/", "video/", "audio/", "text/plain", "text/markdown", "application/pdf", "text/csv")


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
        self.list_files = None  # (host, pasta) -> ([{name,size,mtime}], erro); None desliga /files e /file
        self.link = None  # (host, local, remoto) -> erro ou None; None desliga /link e /unlink
        self.unlink = None
        self.open_file = None  # (host, path, start, end) -> FileStream
        self.token = token or secrets.token_urlsafe(16)
        self.auth: Optional[Auth] = None  # login por senha (rede); None ou desligado = só o token
        self.allowed_hosts = {"127.0.0.1", "localhost"}  # cabeçalho Host aceito
        self.local_host: Optional[str] = "laptop"  # nome desta máquina na página; None = sem agentes locais
        self.migrator = None  # troca automática de conta (app.serve liga); /api/state mostra o histórico

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
                snap = ctx.state.snapshot()
                if ctx.migrator is not None:
                    snap["migrations"] = ctx.migrator.history[-20:]
                    snap["auto_switch"] = ctx.migrator.enabled
                return self.reply(200, snap)
            if u.path == "/api/projects":
                items = ctx.state.get_projects()
                return self.reply(200, {"items": items})
            if u.path == "/api/pipelines":
                items = [p.summary() for p in pipelines.catalog().values()]
                return self.reply(200, {"items": items, "default": pipelines.DEFAULT})
            label, op = self.agent_path(u.path)
            if op == "stream":
                return self.stream(label, int((q.get("since") or ["0"])[0]))
            if op == "files":
                return self.files(label)
            if op == "file":
                return self.file(label, (q.get("name") or [""])[0], bool(q.get("dl")))
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

        def agent_dir(self, label: str) -> Optional[dict]:
            with ctx.state.lock:
                entry = next(
                    (a for a in ctx.state.agents if a.get("label") == label and a.get("state") != "closed"),
                    None,
                )
            return entry if entry and entry.get("dir") else None

        def files(self, label: str) -> None:
            """Arquivos que o agente pôs em <pasta do projeto>/_outbox/, na máquina dele."""
            if not ctx.list_files:
                return self.reply(400, {"err": "arquivos do agente desligados"})
            entry = self.agent_dir(label)
            if entry is None:
                return self.reply(404, {"err": "agente desconhecido"})
            folder = f"{entry['dir'].rstrip('/')}/{OUTBOX}"
            items, err = ctx.list_files(entry.get("host") or ctx.local_host or "?", folder)
            body = {"dir": folder, "items": items}
            if err:
                body["err"] = err
            return self.reply(200, body)

        def file(self, label: str, name: str, download: bool) -> None:
            """Um arquivo da _outbox: inline (prévia) ou anexo (?dl=1); aceita Range de uma faixa."""
            if not ctx.open_file:
                return self.reply(400, {"err": "arquivos do agente desligados"})
            if not name or name != safe_filename(name):
                return self.reply(400, {"err": "nome de arquivo inválido"})
            entry = self.agent_dir(label)
            if entry is None:
                return self.reply(404, {"err": "agente desconhecido"})
            start, end = 0, None
            m = re.match(r"^bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
            if m and (m.group(1) or m.group(2)):
                start, end = int(m.group(1) or 0), (int(m.group(2)) if m.group(2) else None)
            path = f"{entry['dir'].rstrip('/')}/{OUTBOX}/{name}"
            host = entry.get("host") or ctx.local_host or "?"
            try:
                fs = ctx.open_file(host, path, start, end)
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            except (OSError, subprocess.SubprocessError) as e:
                return self.reply(404, {"err": f"não achei {name} em {host}: {str(e)[:120]}"})
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            inline = not download and ctype.startswith(INLINE_OK)
            try:
                self.send_response(206 if m else 200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(fs.length))
                self.send_header("Accept-Ranges", "bytes")
                if m:
                    self.send_header("Content-Range", f"bytes {start}-{start + fs.length - 1}/{fs.size}")
                disp = "inline" if inline else "attachment"
                self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{quote(name)}")
                self.send_header("Content-Security-Policy", FILE_CSP)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "private, max-age=60")
                self.end_headers()
                for chunk in fs.chunks():
                    self.wfile.write(chunk)
                self.wfile.flush()
            except OSError:
                pass  # o navegador desistiu (fechou a prévia); o close abaixo derruba o ssh
            finally:
                fs.close()

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
            if op == "upgrade":
                return self.upgrade(label, body)
            if op == "downgrade":
                return self.downgrade(label)
            if op == "reopen":
                return self.reopen(label, body)
            if op == "link":
                return self.link_agents(label, body)
            if op == "unlink":
                return self.unlink_agents(label, body)
            msg = build_message(op, body)
            if msg is None:
                return self.reply(400, {"err": "mensagem vazia" if op == "send" else "pedido inválido"})
            err = ctx.bridges.deliver(label, msg)
            if err:
                return self.reply(500, {"err": err})
            if op == "stop":  # a lista de agentes é consultada a cada 15 s; antecipa para a aba sumir logo
                ctx.state.mark_closing(label)
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

        def live_sdk(self, label: str) -> Optional[dict]:
            with ctx.state.lock:
                return next(
                    (
                        a
                        for a in ctx.state.agents
                        if a.get("label") == label and a.get("state") != "closed" and a.get("kind") == "sdk"
                    ),
                    None,
                )

        def link_agents(self, label: str, body: dict) -> None:
            """Vínculo entre dois agentes da página: `orq-agent link` na máquina do primeiro (túnel ssh e
            registro espelhado se as máquinas diferem) e uma nota a cada um dizendo como pedir ao outro."""
            if not ctx.link:
                return self.reply(400, {"err": "vínculos desligados"})
            to = (body.get("to") or "").strip()
            a, b = self.live_sdk(label), self.live_sdk(to)
            if a is None or b is None:
                return self.reply(
                    404, {"err": "agente desconhecido (os dois precisam ser agentes da página, vivos)"}
                )
            if label == to:
                return self.reply(400, {"err": "um agente não se vincula a si mesmo"})
            if any(link_pair(lk) == frozenset((label, to)) and lk.get("up") for lk in ctx.state.links):
                return self.reply(400, {"err": "já estão vinculados"})
            record, err = self.make_link(a, b)
            if err:
                return self.reply(500, {"err": f"vínculo falhou: {err}"})
            errs = [
                e
                for e in (ctx.bridges.deliver(x["label"], link_note(x, y)) for x, y in ((a, b), (b, a)))
                if e
            ]
            return self.reply(200, {"ok": True, "link": record, "note_errors": errs})

        def make_link(self, a: dict, b: dict) -> Tuple[Optional[dict], Optional[str]]:
            """`orq-agent link` na máquina de `a` e o registro do vínculo no estado (até a máquina o
            listar). `host` do registro é sempre a máquina onde o vínculo roda: a de `a`."""
            host = link_host(a, ctx.local_host)
            err = ctx.link(host, a["label"], b["label"])
            if err:
                return None, err
            record = {
                "local": a["label"],
                "remote": b["label"],
                "host": host,
                "remote_host": link_host(b, ctx.local_host),
                "kind": "local" if link_host(b, ctx.local_host) == host else "ssh",
                "local_name": a.get("name") or session_name(a["label"]),
                "remote_name": b.get("name") or session_name(b["label"]),
                "up": True,
            }
            ctx.state.add_link(record)
            return record, None

        def break_link(self, rec: dict, closing: Optional[str] = None) -> Tuple[Optional[str], List[str]]:
            """`orq-agent unlink` na máquina do vínculo, tira-o do estado e avisa quem sobrou (com
            `closing`, esse lado está sendo encerrado e não recebe aviso). Devolve (erro, avisados)."""
            err = ctx.unlink(rec.get("host") or ctx.local_host or "?", rec["local"], rec["remote"])
            if err:
                return err, []
            ctx.state.drop_link(rec["local"], rec["remote"])
            told = []
            for me, other in ((rec["local"], rec["remote"]), (rec["remote"], rec["local"])):
                x = self.live_sdk(me) if me != closing else None
                if x is not None and not ctx.bridges.deliver(
                    x["label"], unlink_note(x, self.live_sdk(other), rec)
                ):
                    told.append(me)
            return None, told

        def unlink_agents(self, label: str, body: dict) -> None:
            if not ctx.unlink:
                return self.reply(400, {"err": "vínculos desligados"})
            to = (body.get("to") or "").strip()
            rec = next((lk for lk in ctx.state.links if link_pair(lk) == frozenset((label, to))), None)
            if rec is None:
                return self.reply(404, {"err": "não há vínculo entre esses agentes"})
            err, told = self.break_link(rec)
            if err:
                return self.reply(500, {"err": f"desfazer falhou: {err}"})
            return self.reply(200, {"ok": True, "told": told})

        def upgrade(self, label: str, body: dict) -> None:
            """Sobe os níveis de uma receita acima do agente da aba (mesma máquina, pasta e conta)."""
            with ctx.state.lock:
                agents = list(ctx.state.agents)
            base = next((a for a in agents if a.get("label") == label and a.get("state") != "closed"), None)
            if base is None:
                return self.reply(404, {"err": "agente desconhecido"})
            if base.get("kind") != "sdk":
                return self.reply(
                    400, {"err": "só um agente da página ganha níveis; converta o do terminal antes"}
                )
            if base.get("parent"):
                return self.reply(400, {"err": "esse agente já é um nível de cima"})
            if pipelines.levels_above(agents, label):
                return self.reply(409, {"err": "já tem níveis; desfaça-os antes de trocar de receita"})
            if not base.get("name"):
                msg = "reabra o agente (✕ e abrir de novo): ele precisa subir com nome para ter níveis"
                return self.reply(409, {"err": msg})
            try:
                p = pipelines.get(body.get("pipeline") or None)
            except KeyError as e:
                return self.reply(400, {"err": e.args[0]})
            host = base.get("host") or ctx.local_host or "?"
            acc, cwd = base.get("account") or "?", base.get("dir") or ""
            # receita multi: os agentes vinculados (🔗) ao da aba viram executores do nível de baixo
            others = []
            if p.multi:
                for lk in ctx.state.links:
                    if not lk.get("up") or label not in link_pair(lk):
                        continue
                    other = lk["remote"] if lk.get("local") == label else lk["local"]
                    a = next(
                        (x for x in agents if x.get("label") == other and x.get("state") != "closed"), None
                    )
                    if a is not None and a.get("kind") == "sdk":
                        others.append(a)
            plan = pipelines.plan_levels(p, label, pipelines.executors_list(base, others))
            started = []
            for lv in plan:
                err = ctx.spawn(
                    host,
                    lv["label"],
                    cwd,
                    acc,
                    "new",
                    role=lv["role"],
                    parent=lv["parent"],
                    level=lv["level"],
                )
                if err:
                    for done in reversed(started):  # não deixa nível órfão rodando
                        ctx.bridges.deliver(done, {"orq": "quit"})
                    return self.reply(500, {"err": f"não consegui subir {lv['label']}: {err}"})
                started.append(lv["label"])
                ctx.state.add_provisional(
                    provisional_agent(
                        lv["label"], host, acc, cwd, time.time(), parent=lv["parent"], level=lv["level"]
                    )
                )
            # o nível de baixo ganha vínculo com cada executor vinculado (local ou por ssh); os executores
            # recebem a nota de vínculo; falha aqui não derruba os níveis, só é reportada
            links, link_errors = [], {}
            bottom = started[0]
            me = {"label": bottom, "name": session_name(bottom), "host": host, "dir": cwd}
            for a in others:
                rec, err = self.make_link(me, a) if ctx.link else (None, "vínculos desligados")
                if err:
                    link_errors[a["label"]] = err
                    continue
                links.append(rec)
                ctx.bridges.deliver(a["label"], link_note(a, me))
            return self.reply(
                200,
                {
                    "pipeline": p.id,
                    "levels": started,
                    "top": started[-1],
                    "links": links,
                    "link_errors": link_errors,
                },
            )

        def reopen(self, label: str, body: dict) -> None:
            """Fecha e reabre o agente na mesma conversa (ganha nome; ou noutra conta, se `account` vier)."""
            with ctx.state.lock:
                agents = list(ctx.state.agents)
            a = next((x for x in agents if x.get("label") == label and x.get("state") != "closed"), None)
            if a is None or a.get("kind") != "sdk":
                return self.reply(404, {"err": "agente da página desconhecido"})
            if pipelines.levels_above(agents, label):
                return self.reply(409, {"err": "desfaça os níveis antes de reabrir"})
            acc = body.get("account") or a.get("account") or "?"
            if acc not in ctx.conf.accounts:
                return self.reply(400, {"err": f"conta desconhecida: {acc}"})
            host, cwd = a.get("host") or ctx.local_host or "?", a.get("dir") or ""
            err = ctx.spawn(host, label, cwd, acc, "resume", restart=True)
            if err:
                return self.reply(500, {"err": err})
            ctx.state.add_provisional(provisional_agent(label, host, acc, cwd, time.time()))
            return self.reply(200, {"label": label, "acc": acc})

        def downgrade(self, label: str) -> None:
            """Encerra os níveis acima do agente da aba; a aba volta a falar com ele."""
            with ctx.state.lock:
                agents = list(ctx.state.agents)
            above = pipelines.levels_above(agents, label)
            if not above:
                return self.reply(400, {"err": "esse agente não tem níveis acima"})
            errors = {}
            unlinked = []
            for lv in reversed(above):  # do topo para baixo
                for rec in [lk for lk in ctx.state.links if lv in link_pair(lk)]:
                    # vínculos do nível (receita multi): desfaz antes de fechar, para limpar os espelhos
                    err, _ = self.break_link(rec, closing=lv) if ctx.unlink else ("vínculos desligados", [])
                    if err:
                        errors[f"vínculo {rec['local']} ⇄ {rec['remote']}"] = err
                    else:
                        unlinked.append(rec["remote"] if rec["local"] == lv else rec["local"])
                err = ctx.bridges.deliver(lv, {"orq": "quit"})
                if err:
                    errors[lv] = err
                ctx.state.mark_closing(lv)
            threading.Timer(2.0, ctx.state.refresh_agents).start()
            threading.Timer(6.0, ctx.state.refresh_agents).start()
            return self.reply(200, {"stopped": above, "errors": errors, "unlinked": unlinked})

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


def link_host(agent: dict, local_host: Optional[str]) -> str:
    """Máquina de um agente, como o `orq-agent link` precisa (a local é `local_host`, não `laptop`)."""
    return agent.get("host") or local_host or "?"


def _user_msg(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def link_note(me: dict, other: dict) -> dict:
    """O que cada agente recebe ao ganhar um vínculo: quem é o outro e como falar com ele."""
    name = other.get("name") or session_name(other.get("label", ""))
    mine = me.get("name") or session_name(me.get("label", ""))
    return _user_msg(
        f"[orq] Vínculo criado com o agente `{name}` (pasta {other.get('dir') or '?'} em "
        f"{other.get('host') or '?'}). Vocês se enxergam no ListAgents. Para pedir algo a ele, use "
        f'SendMessage com to="{name}" e peça que responda pelo SendMessage a "{mine}"; ele pode pedir '
        "coisas a você do mesmo jeito. Nada a fazer agora: só tome nota."
    )


def unlink_note(me: dict, other: Optional[dict], rec: dict) -> dict:
    mine_is_local = rec.get("local") == me.get("label")
    name = (other or {}).get("name") or rec.get("remote_name" if mine_is_local else "local_name")
    return _user_msg(f"[orq] Vínculo com o agente `{name}` desfeito: ele não está mais ao seu alcance.")


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
