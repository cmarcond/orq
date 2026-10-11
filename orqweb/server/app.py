"""Sobe o orq-web: abre a porta antes de qualquer consulta, guarda o link (com token) num arquivo
para um segundo `orq-web` reaproveitá-lo, e encerra limpo em Ctrl-C ou SIGTERM."""

import errno
import os
import signal
import subprocess
import sys
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .. import config
from .bridge import BridgeRegistry
from .external import External
from .handlers import Context, make_handler
from .state import StateCache

IGNORED = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """Navegador fechou uma conexão ociosa ou saiu no meio do SSE: normal, sem traceback."""
        if isinstance(sys.exc_info()[1], IGNORED):
            return
        super().handle_error(request, client_address)


def url_file(port: int) -> Path:
    return config.state_root() / f"web-{port}.url"


def existing_server_url(port: int) -> Optional[str]:
    """Link do orq-web que já está nesta porta, se ele responde."""
    f = url_file(port)
    url = f.read_text().strip() if f.exists() else ""
    if not url:
        return None
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3).read(64)
    except OSError:
        return None
    return url


def open_browser(url: str) -> None:
    if os.environ.get("ORQ_NO_OPEN"):
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.run([opener, url], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def serve(port: int = 8765, conf: Optional[config.Config] = None, token: Optional[str] = None) -> int:
    conf = conf or config.load()
    ext = External()
    state = StateCache(conf, ext, usage_host=os.environ.get("ORQ_USAGE_HOST") or None)
    ctx = Context(conf, state, BridgeRegistry(), ext.spawn, token, convert=ext.convert)
    ctx.put_file = ext.put_file
    try:
        srv = Server(("127.0.0.1", port), make_handler(ctx))
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        old = existing_server_url(port)
        if old:
            print(f"já existe um orq-web nesta porta: {old}")
            print("(abrindo no navegador; use --port N para outro servidor)")
            open_browser(old)
            return 0
        print(
            f"a porta {port} está ocupada por outro programa. Use: orq-web --port {port + 1}", file=sys.stderr
        )
        return 1
    url = f"http://127.0.0.1:{port}/?t={ctx.token}"
    f = url_file(port)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(url)
    os.chmod(str(f), 0o600)
    state.start_background()
    print(f"orq-web em {url}\n(Ctrl-C para parar; os agentes continuam rodando)")
    open_browser(url)

    def on_term(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop()
        try:
            f.unlink()
        except OSError:
            pass
        srv.server_close()
        os._exit(0)  # não espera threads de ssh presas
    return 0
