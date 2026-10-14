#!/usr/bin/env python3
"""QA do orq-web com Playwright: dirige a aplicação de verdade (claude real) pelo navegador.

É biblioteca (o conftest usa a classe `Orq`) e linha de comando, para injetar textos e comandos à mão
e ver o resultado em captura de tela, console e estado:

  python qa/orqqa.py up | down            sobe/derruba um orq-web só do QA (porta ORQ_QA_PORT, padrão 8790)
  python qa/orqqa.py state                agentes, estados e cota (/api/state)
  python qa/orqqa.py shot [nome]          captura de tela -> qa/out/<nome>.png
  python qa/orqqa.py open laptop:~/Projects/orq-qa    abre um projeto (pasta nova = conversa nova)
  python qa/orqqa.py show rótulo          troca para a aba desse agente
  python qa/orqqa.py say "texto"          envia ao agente da aba atual e espera a resposta
  python qa/orqqa.py esc                  interrompe o turno (Esc)
  python qa/orqqa.py allow | deny         responde à permissão pendente
  python qa/orqqa.py stop [rótulo]        encerra o agente (✕ duas vezes)
  python qa/orqqa.py levels [receita]     ⇅ subir nível na aba atual (padrão professor-executor)
  python qa/orqqa.py link rótulo          🔗 vincula a aba atual ao agente rótulo (qualquer máquina)
  python qa/orqqa.py unlink rótulo        desfaz o vínculo
  python qa/orqqa.py wires                fios entre os níveis e os níveis do painel
  python qa/orqqa.py undo                 ↓ desfazer: encerra os níveis de cima
  python qa/orqqa.py sets                 ⊞ conjuntos: lista os salvos (nome, agentes, já abertos)
  python qa/orqqa.py set ID               abre o conjunto ID pelo seletor e espera o servidor terminar
  python qa/orqqa.py quota ["texto"]       força "sem cota" na aba atual e espera a troca de conta
  python qa/orqqa.py click "seletor" | text "seletor" | eval "js"
  python qa/orqqa.py watch [segundos]     fica ouvindo console, erros e requisições que falham

Todo comando grava captura e console em qa/out/ (ORQ_QA_VIDEO=1 grava também um vídeo). Alvo: o orq-web
do `up`, ou ORQ_QA_URL (ex.: http://10.0.0.10:8765/ com ORQ_QA_PASSWORD) para a página da rede;
ORQ_QA_VIA=server0 chega a ela por um túnel ssh (e lê a senha lá), porque a privacidade de rede local do
macOS só deixa binários da Apple alcançarem o endereço ZeroTier — o Chromium recebe "No route to host".
ORQ_QA_HEADED=1 mostra o navegador; ORQ_QA_LABEL=rótulo escolhe a aba antes de `say`, `esc`, `allow`,
`deny` e `stop`.
"""

import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse

from playwright.sync_api import Page, Playwright, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # orq-web
OUT = HERE / "out"
PORT = int(os.environ.get("ORQ_QA_PORT", "8790"))
STATE_ROOT = Path(os.environ.get("ORQ_STATE_DIR", str(Path.home() / ".cache/orq")))


def ts() -> str:
    return time.strftime("%H%M%S")


# ---------------------------------------------------------------- orq-web só do QA
def pid_file(port: int = PORT) -> Path:
    return OUT / f"orq-web-{port}.pid"


def local_url(port: int = PORT) -> Optional[str]:
    """Link (com token) do orq-web nesta porta, se ele responde."""
    try:
        url = (STATE_ROOT / f"web-{port}.url").read_text().strip()
    except OSError:
        return None
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3).read(64)
    except OSError:
        return None
    return url


def server_up(port: int = PORT, wait: float = 30) -> Tuple[str, bool]:
    """Garante um orq-web real nesta porta. Devolve (link com token, se foi este chamado que o subiu)."""
    url = local_url(port)
    if url:
        return url, False
    OUT.mkdir(exist_ok=True)
    log = open(OUT / f"orq-web-{port}.log", "ab")
    p = subprocess.Popen(
        [sys.executable, "-m", "orqweb.server.cli", "--port", str(port), "--no-open"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},  # senão o log só sai no fim (e some no SIGTERM)
    )
    pid_file(port).write_text(str(p.pid))
    deadline = time.time() + wait
    while time.time() < deadline:
        url = local_url(port)
        if url:
            return url, True
        if p.poll() is not None:
            raise RuntimeError(f"orq-web saiu com {p.returncode}; veja {OUT / f'orq-web-{port}.log'}")
        time.sleep(0.3)
    raise RuntimeError(f"orq-web não respondeu em {wait:.0f} s na porta {port}")


def server_down(port: int = PORT, wait: float = 10) -> bool:
    """Encerra o orq-web do QA (os agentes continuam; encerre-os com `stop`)."""
    try:
        pid = int(pid_file(port).read_text())
    except (OSError, ValueError):
        pid = None
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        pid_file(port).unlink(missing_ok=True)
    else:
        subprocess.run(["pkill", "-f", f"orqweb.server.cli --port {port}"], capture_output=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        if not local_url(port):
            return True
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------- túnel até a página da rede
TUNNEL_PORT = int(os.environ.get("ORQ_QA_TUNNEL_PORT", "18765"))
_tunnel: Optional[Tuple[str, Optional[str], subprocess.Popen]] = None  # (url local, senha, ssh)


def tunnel_up(url: str, via: str, port: int = TUNNEL_PORT, wait: float = 30) -> Tuple[str, Optional[str]]:
    """Túnel ssh por `via` até a página em `url`. Devolve (url local, senha lida em `via`).

    Daqui, só binários da Apple (curl, ssh) alcançam o endereço ZeroTier: a privacidade de rede local do
    macOS nega o Chromium do Playwright e o python ("No route to host"). Uma única conexão ssh traz a
    senha de ~/.config/orq-web/password (duas seguidas à mesma máquina caem) e segura o túnel até sair."""
    global _tunnel
    if _tunnel:
        return _tunnel[0], _tunnel[1]
    u = urlparse(url)
    remote = f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}"
    marker = "__orq_qa_tunel__"
    p = subprocess.Popen(
        [
            "ssh",
            *("-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "-o", "ExitOnForwardFailure=yes"),
            *("-L", f"127.0.0.1:{port}:{remote}"),
            via,
            f"cat ~/.config/orq-web/password 2>/dev/null; echo; echo {marker}; exec sleep 86400",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    lines: List[str] = []
    for line in p.stdout:  # type: ignore[union-attr]
        if line.strip() == marker:
            break
        lines.append(line.strip())
    else:
        raise RuntimeError(f"túnel ssh por {via} não abriu: {' / '.join(x for x in lines if x)}")
    password = next((x for x in lines if x), None)
    local = f"http://127.0.0.1:{port}"
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            urllib.request.urlopen(local + "/", timeout=3).read(64)
            break
        except OSError:
            time.sleep(0.3)
    else:
        tunnel_down(p)
        raise RuntimeError(f"a página {url} não respondeu pelo túnel {local} (via {via})")
    _tunnel = (local, password, p)
    atexit.register(tunnel_down)
    return local, password


def tunnel_down(p: Optional[subprocess.Popen] = None) -> None:
    global _tunnel
    p = p or (_tunnel[2] if _tunnel else None)
    _tunnel = None
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def target() -> Tuple[str, Optional[str], Optional[str]]:
    """(url base, token, senha): ORQ_QA_URL (página da rede, por túnel com ORQ_QA_VIA) ou o orq-web do QA."""
    url = os.environ.get("ORQ_QA_URL")
    if url:
        password = os.environ.get("ORQ_QA_PASSWORD")
        if os.environ.get("ORQ_QA_VIA"):
            url, read = tunnel_up(url, os.environ["ORQ_QA_VIA"])
            password = password or read
        return url.rstrip("/"), None, password
    url = local_url()
    if not url:
        raise SystemExit(
            f"nenhum orq-web na porta {PORT}: rode `python qa/orqqa.py up` (ou defina ORQ_QA_URL)"
        )
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}", parse_qs(u.query).get("t", [None])[0], None


# ---------------------------------------------------------------- a página
class Orq:
    """Uma aba do orq-web aberta no Chromium, com console, erros e requisições falhas guardados."""

    def __init__(self, page: Page, base: str, token: Optional[str] = None, password: Optional[str] = None):
        self.page, self.base, self.token, self.password = page, base, token, password
        self.console: List[str] = []
        self.browser = None
        page.on("console", lambda m: self.console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: self.console.append(f"[pageerror] {e}"))
        page.on(
            "requestfailed", lambda r: self.console.append(f"[requestfailed] {r.method} {r.url} {r.failure}")
        )
        page.on("response", lambda r: r.status >= 400 and self.console.append(f"[http {r.status}] {r.url}"))

    @classmethod
    def launch(cls, pw: Playwright, headed: bool = False, video: bool = True) -> "Orq":
        """Abre a página. Com `video`, grava a sessão; `close(nome)` salva em qa/out/<nome>.mp4 (ou .webm)."""
        base, token, password = target()
        browser = pw.chromium.launch(headless=not headed)
        size = {"width": 1280, "height": 860}
        extra = {"record_video_dir": str(OUT / "video"), "record_video_size": size} if video else {}
        ctx = browser.new_context(viewport=size, locale="pt-BR", **extra)
        o = cls(ctx.new_page(), base, token, password)
        o.browser = browser
        o.goto()
        return o

    def close(self, video_name: Optional[str] = None) -> Optional[Path]:
        """Fecha o navegador. Com `video_name`, guarda o vídeo da sessão em qa/out/<nome>.mp4 (ou .webm)."""
        if not self.browser:
            return None
        v = self.page.video
        self.page.context.close()  # termina de gravar o vídeo
        self.browser.close()
        if not v:
            return None
        src = Path(v.path())
        if not video_name:
            src.unlink(missing_ok=True)
            return None
        dst = OUT / f"{video_name}.webm"
        src.replace(dst)
        if shutil.which("ffmpeg"):  # mp4 abre no QuickTime e no celular; o webm só em navegador
            mp4 = dst.with_suffix(".mp4")
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(dst), "-pix_fmt", "yuv420p", str(mp4)],
                capture_output=True,
            )
            if r.returncode == 0:
                dst.unlink()
                return mp4
        return dst

    def url(self, path: str = "/") -> str:
        if not self.token:
            return self.base + path
        return f"{self.base}{path}{'&' if '?' in path else '?'}t={self.token}"

    def goto(self) -> None:
        self.page.goto(self.url("/"))
        if self.password and self.page.url.rstrip("/").endswith("/login"):
            self.page.fill("#p", self.password)
            self.page.click("button[type=submit]")
        self.page.wait_for_selector("#tabs .tab.plus", timeout=20000)
        self.page.wait_for_timeout(300)

    # ---- API (mesmo token/cookie da aba)
    def api(self, path: str, body: Optional[dict] = None) -> dict:
        headers = {"X-Orq-Token": self.token} if self.token else {}
        if body is None:
            r = self.page.request.get(self.base + path, headers=headers)
        else:
            r = self.page.request.post(
                self.base + path,
                data=json.dumps(body),
                headers={**headers, "Content-Type": "application/json"},
            )
        try:
            return r.json()
        except Exception:
            return {"status": r.status, "text": r.text()[:300]}

    def state(self) -> dict:
        return self.api("/api/state")

    def agents(self) -> List[dict]:
        return [a for a in self.state().get("agents", []) if a.get("state") != "closed"]

    # ---- o que a página mostra
    def current(self) -> Optional[str]:
        return self.page.evaluate("document.querySelector('.tab.on')?.dataset.l || null")

    def labels(self) -> List[str]:
        return self.page.evaluate("[...document.querySelectorAll('.tab[data-l]')].map(e => e.dataset.l)")

    def cstate(self) -> str:
        return self.page.evaluate("document.getElementById('cstate').textContent")

    def last_ai(self) -> str:
        return self.page.evaluate("[...document.querySelectorAll('.chat.on .ai')].pop()?.innerText || ''")

    def messages(self) -> List[dict]:
        """Cada bloco do chat atual: {cls, text} (me = você, ai = claude, chip = ferramenta, sys = aviso)."""
        return self.page.evaluate(
            "[...document.querySelectorAll('.chat.on .inner > *')]"
            ".map(e => ({cls: e.className, text: e.innerText.slice(0, 400)}))"
        )

    def toast(self) -> str:
        return self.page.evaluate("document.getElementById('toast').textContent")

    # ---- ações
    def show(self, label: str) -> None:
        self.page.click(f'.tab[data-l="{label}"] .meta')
        self.wait_fn(f"() => document.querySelector('.tab.on')?.dataset.l === {json.dumps(label)}")

    def open_project(self, spec: str, timeout: float = 90) -> str:
        """`maquina:/caminho` pelo +, conversa nova. Espera o agente sair de 'abrindo…'. Devolve o rótulo."""
        host, _, path = spec.partition(":")
        if not path:
            raise ValueError("use maquina:/caminho")
        self.page.click(f'.tab.plus[data-host="{host}"]')
        self.page.wait_for_selector("#picker:not([hidden])")
        self.page.fill("#pnew", spec)
        self.page.click("#popen")
        name = path.rstrip("/").split("/")[-1]
        self.wait_fn(
            "([n, h]) => (document.querySelector('.tab.on')?.dataset.l || '').startsWith(n) "
            "&& document.querySelector('.tab.on').dataset.l.endsWith('@' + h)",
            arg=[name, host],
            timeout=timeout * 1000,
        )
        label = self.current()
        # 'abrindo…' é o chute provisório da página; a máquina confirma o agente pronto em até ~15 s.
        # A aba tem de continuar lá: se ela sumir, o estado fica vazio e isso não é "pronto".
        self.wait_fn(
            "(l) => document.querySelector('.tab.on')?.dataset.l === l"
            " && /esperando|trabalhando|permiss/.test(document.getElementById('cstate').textContent)",
            arg=label,
            timeout=45000,
        )
        return label

    # Turno terminado: depois da última mensagem sua há um texto do claude pronto (sem .live), uma
    # permissão ou um aviso, e o estado não é mais 'trabalhando…'. (Só o estado não basta: no primeiro
    # turno o `init` do claude passa por 'esperando você' antes de a resposta vir.)
    TURN_DONE = """() => {
      const inner = document.querySelector('.chat.on .inner');
      if (!inner) return false;
      const kids = [...inner.children];
      const lastMe = kids.map((e) => e.classList.contains('me')).lastIndexOf(true);
      const done = kids.slice(lastMe + 1).some((e) =>
        (e.classList.contains('ai') && !e.classList.contains('live'))
        || e.classList.contains('perm') || e.classList.contains('sys'));
      return done && !document.getElementById('cstate').textContent.includes('trabalhando');
    }"""

    def wait_fn(self, js: str, arg: Any = None, timeout: float = 30000) -> None:
        """Como `page.wait_for_function(js, arg=…, timeout=ms)`, mas sondando pelo `evaluate` a cada 300 ms.

        O Playwright 1.48 (o mais novo com Chromium para o Ubuntu 20.04 do server0) avalia o predicado
        uma vez pelo CDP e, se não for verdadeiro de cara, passa a sondar com `eval` dentro da página, que
        a CSP (sem `unsafe-eval`) barra. O `evaluate` vai sempre pelo CDP."""
        end = time.time() + timeout / 1000
        while True:
            try:
                if self.page.evaluate(js, arg):
                    return
            except Exception as e:  # noqa: BLE001 - a página pode estar entre dois estados
                if "unsafe-eval" not in str(e) and "Execution context" not in str(e):
                    raise
            if time.time() >= end:
                raise AssertionError(f"esperei {timeout / 1000:.0f} s por: {js.strip()[:120]}…")
            self.page.wait_for_timeout(300)

    def wait_idle(self, timeout: float = 180) -> str:
        """Espera o turno terminar. Devolve o texto do estado ('esperando você', 'pede permissão', 'erro')."""
        self.wait_fn(self.TURN_DONE, timeout=timeout * 1000)
        return self.cstate()

    def say(self, text: str, wait: bool = True, timeout: float = 180) -> str:
        """Digita no chat e envia (Enter). Com `wait`, espera o turno e devolve o último texto do claude."""
        self.page.fill("#input", text)
        self.page.press("#input", "Enter")
        self.wait_fn("() => !document.querySelector('.chat.on .me.pending')", timeout=20000)
        if not wait:
            return ""
        self.page.wait_for_timeout(500)
        self.wait_idle(timeout)
        return self.last_ai()

    def interrupt(self) -> None:
        self.page.press("#input", "Escape")

    def permission(self, allow: bool = True) -> None:
        self.page.click(f'.chat.on .perm:not(.done) .btns button[data-ok="{1 if allow else 0}"]')

    # ---- níveis (professor → executor)
    def upgrade(self, recipe: str = "professor-executor", timeout: float = 90) -> List[str]:
        """⇅ subir nível na aba atual com a receita; espera o painel e o nível de cima pronto.

        Devolve os níveis do painel, do topo para baixo (ex.: ['professor', 'executor'])."""
        self.page.click("#lvl")
        self.page.wait_for_selector(f'#picker:not([hidden]) .p.lvp[data-id="{recipe}"]', timeout=30000)
        self.page.click(f'.p.lvp[data-id="{recipe}"]')
        self.page.wait_for_selector("#levels:not([hidden]) .lcard", timeout=timeout * 1000)
        # a aba passa a falar com o nível de cima; ele sai de 'abrindo…' quando a máquina confirma
        self.wait_fn(
            "() => /esperando|trabalhando|permiss/.test(document.getElementById('cstate').textContent)"
            " && document.querySelectorAll('#levels .lcard').length >= 2",
            timeout=timeout * 1000,
        )
        return self.levels()

    def levels(self) -> List[str]:
        """Níveis do painel, do topo para baixo; [] com o painel escondido (os cartões ficam no DOM)."""
        return self.page.evaluate(
            "document.getElementById('levels').hidden ? []"
            " : [...document.querySelectorAll('#levels .lcard b')].map((e) => e.textContent)"
        )

    def wires(self) -> List[dict]:
        """Fios do painel: {frm: nível que mandou, head, delivered (✓✓), text}.

        Todo SendMessage vira um fio de quem mandou ('professor' delegou, 'executor' reportou um marco)."""
        return self.page.evaluate(
            "[...document.querySelectorAll('#side .wire')].map((e) => ({"
            "frm: e.querySelector('.wh b')?.textContent || '',"
            " head: e.querySelector('.wh').innerText.replace(/\\s+/g, ' ').trim(),"
            " delivered: e.classList.contains('delivered') || e.classList.contains('in'),"
            " text: (e.querySelector('pre')?.textContent || '').slice(0, 300)}))"
        )

    def wait_wire(self, frm: str, contains: str = "", delivered: bool = False, timeout: float = 180) -> dict:
        """Espera um fio do nível `frm` cujo texto tenha `contains` (se dado); com `delivered`, espera o
        ✓✓ (o outro lado recebeu, o que chega um instante depois do fio). Devolve o último que casa."""
        self.wait_fn(
            "([f, c, d]) => [...document.querySelectorAll('#side .wire')].some((e) =>"
            " e.querySelector('.wh b')?.textContent === f"
            " && (e.querySelector('pre')?.textContent || '').toLowerCase().includes(c.toLowerCase())"
            " && (!d || e.classList.contains('delivered') || e.classList.contains('in')))",
            arg=[frm, contains, delivered],
            timeout=timeout * 1000,
        )
        hits = [w for w in self.wires() if w["frm"] == frm and contains.lower() in w["text"].lower()]
        return ([w for w in hits if w["delivered"]] if delivered else hits)[-1]

    def downgrade(self, timeout: float = 60) -> None:
        """↓ desfazer (duas vezes): encerra os níveis de cima; a aba volta ao executor."""
        self.page.click("#levels #down")
        self.page.wait_for_timeout(400)
        self.page.click("#levels #down")
        self.page.wait_for_selector("#levels", state="hidden", timeout=timeout * 1000)

    def stop(self, label: Optional[str] = None, timeout: float = 45) -> None:
        """✕ duas vezes na aba; espera a aba sumir (a máquina confirma o fechamento em até ~15 s)."""
        label = label or self.current()
        if not label:
            raise ValueError("nenhuma aba aberta")
        x = f'.tab[data-l="{label}"] .x'
        self.page.click(x)
        self.page.wait_for_timeout(400)
        self.page.click(x)
        self.page.wait_for_selector(f'.tab[data-l="{label}"]', state="detached", timeout=timeout * 1000)

    def ensure_stopped(self, label: str, timeout: float = 60) -> None:
        """Pela API: encerra o agente se estiver vivo (sobra de outra rodada) e espera ele sumir de vez.

        Espera também o socket do supervisor desaparecer (agente desta máquina): reabrir antes disso faz o
        `orq-agent spawn` responder "já rodando" para um agente que está morrendo.
        """
        sock = STATE_ROOT / re.sub(r"[^a-zA-Z0-9_-]", "-", label) / "agent.sock"  # = config.state_dir
        # um servidor recém-aberto ainda não listou os agentes; o socket diz se há o que encerrar
        if sock.exists() or any(a.get("label") == label for a in self.agents()):
            self.api(f"/api/agent/{quote(label, safe='')}/stop", {})
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not sock.exists() and not any(a.get("label") == label for a in self.agents()):
                return
            time.sleep(1)
        raise RuntimeError(f"{label} não encerrou em {timeout:.0f} s")

    # ---- troca automática de conta
    def account_of(self, label: str) -> Optional[str]:
        return next((a.get("account") for a in self.agents() if a.get("label") == label), None)

    def force_quota(self, label: str, retry: str, msg: str = "You've hit your usage limit (forçado pelo QA)"):
        """Força "sem cota" por baixo: grava `state: quota` no state.json do agente (desta máquina), com a
        mensagem a reenviar em `retry`, exatamente como o supervisor faz ao ver o `rate_limit_event`.

        Na próxima consulta às máquinas (≤ 15 s) o servidor reabre o agente noutra conta, na mesma
        conversa, e reenvia `retry`. Só a detecção é pulada; a troca em si é a de verdade."""
        p = STATE_ROOT / re.sub(r"[^a-zA-Z0-9_-]", "-", label) / "state.json"
        st = json.loads(p.read_text())
        st.update(state="quota", msg=msg, retry=retry, ts=time.time())
        p.write_text(json.dumps(st))
        return p

    def wait_account_change(self, label: str, old: Optional[str], timeout: float = 150) -> str:
        """Espera o agente reaparecer noutra conta, confirmado pela máquina (não só a entrada provisória
        'abrindo…'); devolve a conta nova."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            a = next((x for x in self.agents() if x.get("label") == label), None)
            if a and a.get("account") != old and a.get("state") not in ("starting", "closed"):
                return a["account"]
            self.page.wait_for_timeout(2000)
        raise RuntimeError(
            f"{label} continuou na conta {old} por {timeout:.0f} s; migrações: {self.migrations()}"
        )

    def migrations(self) -> List[dict]:
        return self.state().get("migrations", [])

    # ---- vínculos entre agentes (🔗)
    def link(self, to: str, timeout: float = 120) -> List[str]:
        """🔗 vincular na aba atual: escolhe `to` no seletor; espera o vínculo aparecer no painel."""
        base = self.current()
        self.page.click("#lnk")
        self.page.wait_for_selector("#picker:not([hidden]) #plist .p", timeout=20000)
        self.page.click(f'#plist .p[data-l="{to}"]')
        self.wait_fn(
            "(n) => !document.getElementById('links').hidden"
            " && [...document.querySelectorAll('#links .lcard')].some((e) => e.dataset.l === n)",
            arg=to,
            timeout=timeout * 1000,
        )
        assert self.current() == base
        return self.links()

    def links(self) -> List[str]:
        """Rótulos dos agentes vinculados ao da aba (painel de vínculos)."""
        return self.page.evaluate(
            "document.getElementById('links').hidden ? []"
            " : [...document.querySelectorAll('#links .lcard')].map((e) => e.dataset.l)"
        )

    def unlink(self, to: str, timeout: float = 60) -> None:
        self.page.click(f'#links .lcard[data-l="{to}"] .ul')
        self.page.click(f'#links .lcard[data-l="{to}"] .ul')
        self.wait_fn(
            "(n) => document.getElementById('links').hidden"
            " || ![...document.querySelectorAll('#links .lcard')].some((e) => e.dataset.l === n)",
            arg=to,
            timeout=timeout * 1000,
        )

    # ---- conjuntos (⊞ no topo)
    def open_sets(self) -> List[dict]:
        """⊞ conjuntos: abre o seletor e devolve as linhas {id, text} (text = nome, resumo, já abertos)."""
        if self.page.is_hidden("#picker") or self.page.is_hidden("#setsnap"):
            self.page.click("#sets")
        self.page.wait_for_selector("#picker:not([hidden]) #setsnap", timeout=20000)
        return self.page.evaluate(
            "[...document.querySelectorAll('#plist .setp')]"
            ".map((e) => ({id: e.dataset.id, text: e.innerText}))"
        )

    def save_set(self, data: dict) -> str:
        """Salva um conjunto pela API (o mesmo POST do "enviar JSON"); devolve o id."""
        j = self.api("/api/conjuntos", data)
        assert "id" in j, j
        return j["id"]

    def set_job(self, sid: str) -> Optional[dict]:
        return next((j for j in self.state().get("conjuntos_abrindo", []) if j["id"] == sid), None)

    def open_set(self, sid: str, timeout: float = 300) -> dict:
        """Clica no conjunto no seletor e espera o servidor terminar de abrir (agentes, vínculos,
        professor, tarefas). Devolve o registro do andamento (feitos, erros, top)."""
        self.open_sets()
        self.page.click(f'#plist .setp[data-id="{sid}"]')
        self.wait_fn(
            "() => document.getElementById('picker').hidden"
            " || /já aberto|sendo aberto|não vale/.test(document.getElementById('toast').textContent)",
            timeout=30000,
        )
        if not self.page.is_hidden("#picker"):
            raise AssertionError(f"o conjunto {sid} não abriu: {self.toast()}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            j = self.set_job(sid)
            if j and j["etapa"] == "pronto":
                return j
            self.page.wait_for_timeout(2000)
        raise AssertionError(f"o conjunto {sid} não terminou de abrir: {self.set_job(sid)}")

    def snapshot_set(self, nome: str) -> dict:
        """📷 salvar abas abertas: fotografa, dá o nome e salva. Devolve o JSON que foi salvo."""
        self.open_sets()
        self.page.click("#setsnap")
        self.page.wait_for_selector("#picker:not([hidden]) #snapjson", timeout=20000)
        self.page.fill("#snapname", nome)
        data = json.loads(self.page.input_value("#snapjson"))
        data["nome"] = nome
        self.page.click("#snapsave")
        self.wait_fn("() => /salvo/.test(document.getElementById('toast').textContent)", timeout=20000)
        self.page.click("#pclose")  # a lista volta a aparecer depois de salvar; fecha para liberar a página
        return data

    def delete_set(self, sid: str) -> None:
        self.open_sets()
        for _ in range(2):  # ✕ duas vezes, como o encerrar de uma aba
            self.page.click(f'#plist .setp[data-id="{sid}"] [data-del]')
        self.wait_fn('(s) => !document.querySelector(`#plist .setp[data-id="${s}"]`)', arg=sid, timeout=20000)
        self.page.click("#pclose")

    # ---- arquivos que o agente compartilhou (_outbox/)
    def open_files(self) -> None:
        """📂 na barra do chat: abre a lista de arquivos no painel lateral."""
        if self.page.is_hidden("#outbox"):
            self.page.click("#out")
        self.page.wait_for_selector("#outbox:not([hidden]) .lhead")

    def files(self) -> List[str]:
        """Nomes na lista (painel aberto), do mais novo ao mais antigo."""
        return self.page.evaluate(
            "[...document.querySelectorAll('#outbox .frow b')].map((e) => e.textContent)"
        )

    def wait_file(self, name: str, timeout: float = 120) -> None:
        """Espera `name` aparecer na lista (a página relê a _outbox/ ao fim de cada rodada e a cada 30 s)."""
        self.open_files()
        self.wait_fn(
            "(n) => [...document.querySelectorAll('#outbox .frow b')].some((e) => e.textContent === n)",
            arg=name,
            timeout=timeout * 1000,
        )

    def preview(self, name: str) -> str:
        """Clica no arquivo e devolve a tag do que a prévia mostra (IMG, IFRAME, VIDEO, PRE, DIV)."""
        self.page.click(f'#outbox .frow[data-n="{name}"] .fmeta')
        self.page.wait_for_selector("#view:not([hidden]) .vbody > *")
        self.wait_fn(
            "() => !document.querySelector('#view .vbody').textContent.includes('carregando')", timeout=30000
        )
        return self.page.evaluate("document.querySelector('#view .vbody > *').tagName")

    def close_preview(self) -> None:
        self.page.press("body", "Escape")
        self.page.wait_for_selector("#view", state="hidden")

    def download(self, name: str) -> bytes:
        """Baixa pela API, como o ⬇ da lista faz (mesmo link)."""
        label = self.current()
        headers = {"X-Orq-Token": self.token} if self.token else {}
        r = self.page.request.get(self.url(f"/api/agent/{label}/file?name={name}&dl=1"), headers=headers)
        assert r.status == 200, f"download de {name}: http {r.status}"
        return r.body()

    def answer_permissions(self, timeout: float = 240) -> str:
        """Espera o turno acabar, permitindo tudo que o claude pedir no caminho. Devolve o estado final."""
        end = time.time() + timeout
        while True:
            st = self.wait_idle(max(5.0, end - time.time()))
            if "permiss" not in st:
                return st
            self.permission(True)
            self.page.wait_for_timeout(500)

    # ---- evidências
    def shot(self, name: str) -> Path:
        OUT.mkdir(exist_ok=True)
        p = OUT / f"{name}.png"
        self.page.screenshot(path=str(p), full_page=True)
        return p

    def dump(self, name: str) -> Path:
        OUT.mkdir(exist_ok=True)
        p = OUT / f"{name}.console.txt"
        p.write_text("\n".join(self.console) + ("\n" if self.console else ""))
        return p

    def problems(self) -> List[str]:
        """Erros de página, requisições falhas e respostas >= 400 vistas até agora."""
        return [
            c
            for c in self.console
            if c.startswith(("[pageerror]", "[requestfailed]", "[http ", "[error]"))
            and not ("/stream?" in c and "ERR_ABORTED" in c)  # stream fechado de propósito (aba recriada)
            and not ("/file?" in c and "ERR_ABORTED" in c)  # prévia fechada antes de o arquivo chegar
            and not c.startswith("[error] Failed to load resource")  # a linha [http N] diz qual foi
            and not ("[http 404]" in c and c.endswith("/files"))  # recém-aberto: a página tenta de novo
        ]


# ---------------------------------------------------------------- linha de comando
def fmt_state(s: dict) -> str:
    lines = []
    for a in s.get("agents", []):
        if a.get("state") == "closed":
            continue
        extra = (" nível " + a["level"] if a.get("level") else "") + (
            " · " + a["msg"] if a.get("msg") else ""
        )
        lines.append(
            f"  {a.get('label', '?'):32} {a.get('state', '?'):12} {a.get('account', '?'):10} "
            f"{a.get('kind', '')}{extra}"
        )
    us = s.get("usage", {})
    quota = ", ".join(f"{k} 5h {v.get('5h', '?')}% 7d {v.get('7d', '?')}%" for k, v in us.items())
    return ("agentes:\n" + "\n".join(lines) if lines else "agentes: nenhum") + f"\ncota: {quota or '?'}"


def main(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "up":
        url, started = server_up()
        print(("subiu: " if started else "já estava no ar: ") + url)
        return 0
    if cmd == "down":
        print("encerrado" if server_down() else "não achei o orq-web do QA (ou não encerrou)")
        return 0
    video = bool(os.environ.get("ORQ_QA_VIDEO"))
    with sync_playwright() as pw:
        o = Orq.launch(pw, headed=bool(os.environ.get("ORQ_QA_HEADED")), video=video)
        label = os.environ.get("ORQ_QA_LABEL")
        if label and cmd in ("say", "esc", "allow", "deny", "stop", "shot", "text", "eval", "quota"):
            o.show(label)
        try:
            if cmd == "state":
                print(fmt_state(o.state()))
                print("aba atual:", o.current())
            elif cmd == "shot":
                pass
            elif cmd == "open":
                lab = o.open_project(args[0])
                print(f"aberto: {lab} · {o.cstate()} · toast: {o.toast()}")
            elif cmd == "show":
                o.show(args[0])
                print("aba atual:", o.current())
            elif cmd == "say":
                reply = o.say(" ".join(args), wait="--no-wait" not in args)
                print(f"[{o.current()}] {o.cstate()}\n{reply}")
            elif cmd == "esc":
                o.interrupt()
                o.page.wait_for_timeout(2000)
                print(o.cstate())
            elif cmd in ("allow", "deny"):
                o.permission(cmd == "allow")
                o.page.wait_for_timeout(1500)
                print(o.cstate())
            elif cmd == "stop":
                lab = args[0] if args else o.current()
                o.stop(lab)
                print(f"encerrado: {lab}")
            elif cmd == "link":
                print("vínculos:", ", ".join(o.link(argv[2])) or "nenhum")
            elif cmd == "unlink":
                o.unlink(argv[2])
                print("vínculos:", ", ".join(o.links()) or "nenhum")
            elif cmd == "levels":
                print(
                    "níveis:",
                    " → ".join(o.upgrade(args[0] if args else "professor-executor")),
                    "·",
                    o.cstate(),
                )
            elif cmd == "wires":
                for w in o.wires():
                    print(f"[{'✓✓' if w['delivered'] else '…'}] {w['head']}\n    {w['text'][:200]}")
                print("níveis:", " → ".join(o.levels()) or "nenhum")
            elif cmd == "sets":
                for r in o.open_sets():
                    print(f"  {r['id']:24} {r['text'].replace(chr(10), ' · ')[:120]}")
            elif cmd == "set":
                j = o.open_set(args[0])
                print(f"feitos: {', '.join(j['feitos']) or 'nenhum'}; professor: {j.get('top') or '-'}")
                for k, v in j["erros"].items():
                    print(f"  problema {k}: {v}")
            elif cmd == "undo":
                o.downgrade()
                print("níveis encerrados ·", o.cstate())
            elif cmd == "quota":
                lab = o.current()
                old = o.account_of(lab)
                o.force_quota(lab, " ".join(args) or "Continue de onde parou.")
                print(f"{lab}: conta {old} marcada sem cota; esperando a troca…")
                new = o.wait_account_change(lab, old)
                print(f"reaberto na conta {new} · {o.cstate()} · toast: {o.toast()}")
            elif cmd == "click":
                o.page.click(args[0])
                o.page.wait_for_timeout(800)
            elif cmd == "text":
                print(o.page.inner_text(args[0]))
            elif cmd == "eval":
                print(json.dumps(o.page.evaluate(" ".join(args)), ensure_ascii=False, indent=1))
            elif cmd == "watch":
                secs = float(args[0]) if args else 30
                print(f"ouvindo {secs:.0f} s… (aba {o.current()})")
                o.page.wait_for_timeout(secs * 1000)
            else:
                print(f"comando desconhecido: {cmd}\n{__doc__}")
                return 2
        finally:
            name = f"{cmd}-{ts()}" if cmd != "shot" or not args else args[0]
            print(f"captura: {o.shot(name)}  console: {o.dump(name)}")
            probs = o.problems()
            if probs:
                print("problemas no console:\n  " + "\n  ".join(probs[-10:]))
            v = o.close(name if video else None)
            if v:
                print(f"vídeo: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
