"""Starts orq-web: opens the port before any query, saves the link (with token) in a file
for a second `orq-web` to reuse, and shuts down cleanly on Ctrl-C or SIGTERM."""

import errno
import os
import signal
import subprocess
import sys
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .. import config
from .auth import Auth
from .bridge import BridgeRegistry
from .external import External
from .handlers import Context, make_handler
from .migrate import Migrator
from .state import StateCache

LOCAL_ADDRS = ("127.0.0.1", "localhost", "::1")
DEFAULT_PASSWORD_FILE = Path.home() / ".config/orq-web/password"


def env_list(name: str) -> list:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def local_host_from_env() -> Optional[str]:
    """ORQ_LOCAL_HOST: this machine's name on the page (default laptop); none/nenhum = no local agents."""
    v = os.environ.get("ORQ_LOCAL_HOST", "laptop").strip()
    return None if v.lower() in ("", "none", "nenhum", "-") else v


def check_bind(bind: str, auth_enabled: bool) -> Optional[str]:
    """Listening outside 127.0.0.1 without a password would expose the agents to anyone on the network."""
    if bind in LOCAL_ADDRS or auth_enabled:
        return None
    return (
        f"refused: ORQ_BIND={bind} would expose orq-web on the network without a password. Create the "
        f"password file (ORQ_PASSWORD_FILE, default {DEFAULT_PASSWORD_FILE}) or listen only on 127.0.0.1."
    )


IGNORED = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """The browser closed an idle connection or left in the middle of the SSE: normal, no traceback."""
        if isinstance(sys.exc_info()[1], IGNORED):
            return
        super().handle_error(request, client_address)


def url_file(port: int) -> Path:
    return config.state_root() / f"web-{port}.url"


def saved_token(port: int) -> Optional[str]:
    """Token of the last orq-web on this port, so that the open tab stays valid after a restart."""
    try:
        url = url_file(port).read_text().strip()
    except OSError:
        return None
    tok = parse_qs(urlparse(url).query).get("t", [""])[0]
    return tok or None


def existing_server_url(port: int) -> Optional[str]:
    """Link of the orq-web that is already on this port, if it responds."""
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
    bind = os.environ.get("ORQ_BIND", "127.0.0.1").strip() or "127.0.0.1"
    auth = Auth(Path(os.path.expanduser(os.environ.get("ORQ_PASSWORD_FILE") or str(DEFAULT_PASSWORD_FILE))))
    err = check_bind(bind, auth.enabled)
    if err:
        print(err, file=sys.stderr)
        return 1
    local = local_host_from_env()
    conf.hosts = [h for h in conf.hosts + env_list("ORQ_EXTRA_HOSTS") if h != local]
    ssh_config = os.environ.get("ORQ_SSH_CONFIG") or None
    ext = External(local_host=local, ssh_config=ssh_config)
    state = StateCache(conf, ext, usage_host=os.environ.get("ORQ_USAGE_HOST") or None)
    bridges = BridgeRegistry(local_host=local, ssh_config=ssh_config)
    ctx = Context(conf, state, bridges, ext.spawn, token or saved_token(port), convert=ext.convert)
    migrator = Migrator(
        conf,
        state,
        ext.spawn,
        lambda lab, obj: bridges.deliver(lab, obj, fresh=True),
        enabled=conf.auto_switch,
    )
    state.on_agents = migrator.check
    ctx.migrator = migrator
    ctx.put_file = ext.put_file
    ctx.list_files = ext.list_files
    ctx.link = ext.link
    ctx.unlink = ext.unlink
    ctx.open_file = ext.open_file
    ctx.auth = auth
    ctx.local_host = local
    ctx.allowed_hosts |= {bind, *env_list("ORQ_ALLOWED_HOSTS")}
    try:
        srv = Server((bind, port), make_handler(ctx))
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        old = existing_server_url(port)
        if old:
            print(f"there is already an orq-web on this port: {old}")
            print("(opening it in the browser; use --port N for another server)")
            open_browser(old)
            return 0
        print(f"port {port} is in use by another program. Use: orq-web --port {port + 1}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{port}/?t={ctx.token}"
    if bind not in LOCAL_ADDRS:
        print(f"orq-web on the network at http://{bind}:{port}/  (log in with the password from {auth.file})")
    f = url_file(port)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(url)
    os.chmod(str(f), 0o600)
    state.start_background()
    print(f"orq-web at {url}\n(Ctrl-C to stop; the agents keep running)")
    open_browser(url)

    def on_term(*_):
        # a second SIGTERM (`uv run` forwards its own) would land in the middle of the cleanup below:
        # ignore the next ones
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop()
        # the link file stays: the next orq-web on this port reuses the token (the tab stays valid);
        # existing_server_url checks that someone is really responding before trusting it
        srv.server_close()
        os._exit(0)  # does not wait for stuck ssh threads
    return 0
