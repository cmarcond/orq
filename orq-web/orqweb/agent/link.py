"""Link between agents: SendMessage/ListAgents across accounts and across machines.

Every live Claude Code has, in `<config_dir>/sessions/`, a card `<pid>.json` (name, folder, state and
`messagingSocketPath`, the Unix socket on which it receives messages) and a file `<pid>.<hash>.key` next
to it, which whoever sends a message presents to the socket. ListAgents reads the folder of its OWN
account; SendMessage talks to the socket. All of it designed for one account on a single machine.

The link extends that. On the same machine, across accounts: the card and the key of each agent are
*mirrored* into the other's `sessions/` folder (same socket). Across machines (spike of 2026-10-12): an
`ssh -N` stretches each agent's socket to the other machine (`-L` and `-R` with Unix paths) and the
mirror points to the forwarded socket, which has a name of its own (`orq-<pid>.sock`) so it is never
mistaken for that of a real session. From then on each side lists and addresses the other as if it were
local.

Mirrors and forwarded sockets are SHARED RESOURCES: two links that bring the same agent into the same
folder (the tab's agent and the teacher above it, on the same account, both linked to the same remote
executor) use the same card and the same socket. Each link records the resources it depends on
(`resources` in its file under `~/.cache/orq/<agent>/links/`); when it dies, it only deletes what no
other live link of this machine still uses. A forward that is already up (a socket that accepts
connections) is reused instead of redone; if whoever kept it leaves, the remaining link redoes it on the
next cycle.

`run_link` runs on the local agent's machine and talks to the other one through `orq-agent peer` (ssh).
It exits, and undoes what is only its own, when: unlink (SIGTERM), the local agent closes, the remote one
closes, or the tunnel drops.
"""

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import config
from .state import StateFile, pid_alive
from .supervisor import socket_alive

TUNNEL_WAIT = 20.0
POLL = 5.0  # link cycle: tunnel, local agent, forwarded socket
REMOTE_POLL = 30.0  # how often it asks the other machine whether the remote is still alive
ENTRY_WAIT = 40.0  # a freshly opened agent takes a few seconds to register the session
KEY_RE = re.compile(r"^\d+\.[0-9a-fA-F]+\.key$")


# ---------------------------------------------------------------- session registry
@dataclass
class Entry:
    """A session card and the key file next to it."""

    path: Path
    key_path: Optional[Path]
    data: Dict[str, Any]

    @property
    def pid(self) -> int:
        return int(self.data.get("pid") or int(self.path.stem))

    @property
    def sock(self) -> str:
        return str(self.data.get("messagingSocketPath") or "")

    @property
    def sock_dir(self) -> str:
        return os.path.dirname(self.sock)

    def record(self) -> Dict[str, Any]:
        """What travels to the other machine: card, name of the key file and its content."""
        return {
            "name": self.data.get("name"),
            "pid": self.pid,
            "sock": self.sock,
            "sessions_dir": str(self.path.parent),
            "json": self.data,
            "key_name": self.key_path.name if self.key_path else None,
            "key": self.key_path.read_text() if self.key_path else None,
        }


def agent_sessions_dir(label: str, conf: Optional[config.Config] = None) -> Path:
    """sessions/ folder of the account of this machine's agent (from its state.json). RuntimeError if none."""
    st = StateFile(config.state_dir(label) / "state.json").read()
    if not st.get("account"):
        raise RuntimeError(f"{label} is not an agent of this machine")
    try:
        return (conf or config.load()).account(st["account"]).config_dir / "sessions"
    except KeyError as e:
        raise RuntimeError(e.args[0]) from None


def read_card(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def find_entry(
    name: str, dirs: List[Path], alive: Optional[Callable[[Optional[int]], bool]] = None
) -> Optional[Entry]:
    """Card of the live session with this name (`claude -n`), in the given folders; never a mirror."""
    alive = alive or pid_alive  # resolved at call time, not at definition (the tests swap pid_alive)
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            data = read_card(f)
            if data is None or data.get("name") != name or data.get("_orq_mirror"):
                continue
            pid = data.get("pid")
            if not alive(pid):
                continue
            keys = sorted(d.glob(f"{pid}.*.key"))
            return Entry(f, keys[0] if keys else None, data)
    return None


def wait_entry(name: str, dirs: List[Path], timeout: float = ENTRY_WAIT) -> Optional[Entry]:
    deadline = time.time() + timeout
    while True:
        e = find_entry(name, dirs)
        if e is not None or time.time() > deadline:
            return e
        time.sleep(0.5)


# ---------------------------------------------------------------- mirrors and forwarded sockets
def mirror_sock(sock_dir: str, pid: int) -> str:
    """Where the forwarded socket of an agent from another machine lives here: a name no session uses."""
    return os.path.join(sock_dir, f"orq-{pid}.sock")


def write_mirror(sessions_dir: Path, record: Dict[str, Any], sock: str, origin: str) -> List[Path]:
    """Writes the card and the key of a session from another account/machine, pointing to `sock`.

    Refuses (RuntimeError) if there is already a REAL session card with this pid in the folder: pids
    repeat across machines, and writing over it would take a local session off the air. Idempotent over a
    mirror."""
    pid = int(record["pid"])
    key_name = record.get("key_name")
    if key_name is not None and not (KEY_RE.match(key_name) and key_name.startswith(f"{pid}.")):
        raise ValueError(f"invalid key name: {key_name!r}")
    card = sessions_dir / f"{pid}.json"
    existing = read_card(card)
    if existing is not None and not existing.get("_orq_mirror"):
        raise RuntimeError(
            f"there is a local session with pid {pid} in {sessions_dir}; not mirroring over it"
        )
    sessions_dir.mkdir(parents=True, exist_ok=True)
    data = dict(record["json"])
    data["messagingSocketPath"] = sock
    data["_orq_mirror"] = origin  # where it came from; `find_entry` never returns a mirror
    card.write_text(json.dumps(data))
    paths = [card]
    if key_name and record.get("key") is not None:
        p = sessions_dir / key_name
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never readable by others
        with os.fdopen(fd, "w") as fh:
            fh.write(record["key"])
        paths.append(p)
    return paths


def remove_mirror(sessions_dir: Path, pid: int, sock: Optional[str] = None) -> bool:
    """Deletes the mirror of `pid` (card, key and, if given, the forwarded socket). Does not touch a real
    session card with this pid. Returns whether it deleted."""
    card = sessions_dir / f"{pid}.json"
    existing = read_card(card)
    if existing is not None and not existing.get("_orq_mirror"):
        return False
    for f in [card] + list(sessions_dir.glob(f"{pid}.*.key")):
        try:
            f.unlink()
        except OSError:
            pass
    if sock:
        try:
            os.unlink(sock)
        except OSError:
            pass
    return True


# ---------------------------------------------------------------- link files and resources
# A resource is a string: "card:<sessions folder>/<pid>" (local mirror), "sock:<path>" (socket
# forwarded here) or "remote:<machine>:<sessions folder there>/<pid>" (mirror and socket on the other
# machine).
def links_dir(label: str) -> Path:
    return config.state_dir(label) / "links"


def link_file(local_label: str, remote_label: str) -> Path:
    return links_dir(local_label) / (config.safe_label(remote_label) + ".json")


def same_host(a: str, b: str) -> bool:
    return config.host_of(a) == config.host_of(b)


def link_alive(st: Dict[str, Any], root: Path) -> bool:
    """An ssh link lives as long as its process lives; a local one, as long as both agents live with the
    SAME sessions as when it was made (agent reopened = dead link)."""
    if not st.get("up"):
        return False
    if st.get("kind") != "local":
        return pid_alive(st.get("pid"))
    for lab in (st.get("local", ""), st.get("remote", "")):
        if not (root / config.safe_label(lab) / "agent.sock").exists():
            return False
    pids = st.get("sessions") or {}
    return bool(pids) and all(pid_alive(pid) for pid in pids.values())


def read_links(root: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    out = []
    for f in sorted(root.glob("*/links/*.json")):
        st = StateFile(f).read()
        if st:
            out.append((f, st))
    return out


def resources_in_use(root: Path, except_file: Optional[Path] = None) -> set:
    """Resources that the live links of this machine (except `except_file`) depend on."""
    used: set = set()
    for f, st in read_links(root):
        if except_file is not None and f.resolve() == except_file.resolve():
            continue
        if link_alive(st, root):
            used.update(st.get("resources") or [])
    return used


def release(
    resources: List[str],
    root: Path,
    except_file: Optional[Path],
    remote_unmirror: Optional[Callable[[str, str, int], None]] = None,
) -> List[str]:
    """Deletes the resources nobody else uses. `remote_unmirror(host, sessions_dir, pid)` takes care of
    those on another machine. Returns the ones that stayed because they are in use."""
    used = resources_in_use(root, except_file)
    kept = []
    for r in resources:
        if r in used:
            kept.append(r)
            continue
        kind, _, rest = r.partition(":")
        if kind == "card":
            d, pid = rest.rsplit("/", 1)
            remove_mirror(Path(d), int(pid))
        elif kind == "sock":
            try:
                os.unlink(rest)
            except OSError:
                pass
        elif kind == "remote" and remote_unmirror is not None:
            host, _, path = rest.partition(":")
            d, pid = path.rsplit("/", 1)
            remote_unmirror(host, d, int(pid))
    return kept


def list_links(root: Optional[Path] = None) -> List[dict]:
    """Links of this machine (one file per pair), with `up` checked now. A local link that died (agent
    closed or reopened) has its mirrors collected right here, since there is no process of its own to do
    it."""
    root = root or config.state_root()
    out = []
    for f, st in read_links(root):
        alive = link_alive(st, root)
        if not alive and st.get("kind") == "local" and st.get("resources"):
            kept = release(st["resources"], root, f)
            st = StateFile(f).write(up=False, why=st.get("why") or "agent closed", resources=kept)
        st["up"] = alive
        out.append(st)
    return out


# ---------------------------------------------------------------- same machine
def local_link(local_label: str, remote_label: str, conf: Optional[config.Config] = None) -> Dict[str, Any]:
    """Link between two agents of this machine, without a tunnel: different accounts get each other's card
    and key (same socket). Returns the state to write; the mirrors go into `resources`."""
    conf = conf or config.load()
    dirs, entries = {}, {}
    for lab in (local_label, remote_label):
        dirs[lab] = agent_sessions_dir(lab, conf)
        entries[lab] = wait_entry(config.session_name(lab), [dirs[lab]])
        if entries[lab] is None:
            raise RuntimeError(f"{lab} is not running under the name {config.session_name(lab)}")
    resources: List[str] = []
    if dirs[local_label].resolve() != dirs[remote_label].resolve():
        for me, other in ((local_label, remote_label), (remote_label, local_label)):
            e = entries[other]
            write_mirror(dirs[me], e.record(), e.sock, other)
            resources.append(f"card:{dirs[me]}/{e.pid}")
    return dict(
        local=local_label,
        remote=remote_label,
        host=config.host_of(local_label),
        remote_host=config.host_of(remote_label),
        local_name=config.session_name(local_label),
        remote_name=config.session_name(remote_label),
        kind="local",
        pid=None,
        sessions={lab: entries[lab].pid for lab in (local_label, remote_label)},
        resources=resources,
        up=True,
    )


# ---------------------------------------------------------------- across machines
def tunnel_command(
    host: str, forwards: List[Tuple[str, str, str]], ssh: Optional[List[str]] = None
) -> List[str]:
    """`ssh -N` with the requested forwards: (flag -L/-R, listening socket, target socket)."""
    cmd = (ssh or ["ssh"]) + [
        "-N",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StreamLocalBindUnlink=yes",
    ]
    for flag, listen, target in forwards:
        cmd += [flag, f"{listen}:{target}"]
    return cmd + [host]


class Link:
    """A running ssh link (what `orq-agent link-run` runs in the foreground).

    `run_remote(args, input)` executes `orq-agent <args>` on the other machine and returns the stdout (the
    tests swap in a function); `popen` opens the tunnel; `root` is the state root (~/.cache/orq).
    """

    def __init__(
        self,
        local_label: str,
        remote_label: str,
        conf: Optional[config.Config] = None,
        run_remote: Optional[Callable[..., str]] = None,
        popen: Callable[..., Any] = subprocess.Popen,
        log: Callable[[str], None] = lambda s: print(s, file=sys.stderr, flush=True),
        ssh: Optional[List[str]] = None,
        root: Optional[Path] = None,
    ):
        self.local_label, self.remote_label = local_label, remote_label
        self.host = config.host_of(remote_label)
        self.conf = conf or config.load()
        self.run_remote = run_remote or self._ssh_remote
        self.popen, self.log, self.ssh = popen, log, ssh or ["ssh"]
        self.root = root or config.state_root()
        self.local: Optional[Entry] = None
        self.remote: Optional[Dict[str, Any]] = None
        self.tunnel: Any = None
        self.forwards: List[str] = []  # flags of the current tunnel: "-L", "-R"
        self.state = StateFile(link_file(local_label, remote_label))
        self.tunnel_log = self.state.path.with_suffix(".log")
        self._stop = False

    # ---- the other machine
    def _ssh_remote(self, args: List[str], input: Optional[str] = None) -> str:
        cmd = self.ssh + [
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            self.host,
            "~/.local/bin/orq-agent " + shlex.join(args),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, input=input)
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"ssh {self.host}: {e}") from None
        if r.returncode:
            raise RuntimeError((r.stderr.strip() or r.stdout.strip() or "failed")[-300:])
        return r.stdout

    def _peer(self, *extra: str, input: Optional[str] = None) -> Dict[str, Any]:
        """`orq-agent peer REMOTE …` over there; the JSON of the last line. RuntimeError if `err` comes."""
        out = self.run_remote(["peer", self.remote_label, *extra], input=input)
        try:
            data = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError(f"invalid response from {self.host}: {out[-200:]}") from None
        if data.get("err"):
            raise RuntimeError(f"{self.host}: {data['err']}")
        return data

    # ---- paths
    def local_mirror_sock(self) -> str:
        assert self.local and self.remote
        return mirror_sock(self.local.sock_dir, self.remote["pid"])

    def remote_mirror_sock(self) -> str:
        assert self.local and self.remote
        return mirror_sock(os.path.dirname(self.remote["sock"]), self.local.pid)

    def resources(self) -> List[str]:
        assert self.local and self.remote
        return [
            f"card:{self.local.path.parent}/{self.remote['pid']}",
            f"sock:{self.local_mirror_sock()}",
            f"remote:{self.host}:{self.remote['sessions_dir']}/{self.local.pid}",
        ]

    # ---- tunnel
    def _forward(self, flag: str) -> Tuple[str, str, str]:
        """The forward of each side: -L brings the remote here, -R takes the local there."""
        if flag == "-L":
            return ("-L", self.local_mirror_sock(), self.remote["sock"])
        return ("-R", self.remote_mirror_sock(), self.local.sock)

    def _needed_forwards(self, remote_alive: bool) -> List[str]:
        """Which forwards are missing now: those that no tunnel (mine or another link's) keeps up."""
        fw = []
        if "-L" in self.forwards or not socket_alive(Path(self.local_mirror_sock())):
            fw.append("-L")
        if "-R" in self.forwards or not remote_alive:
            fw.append("-R")
        return fw

    def _start_tunnel(self, flags: List[str]) -> None:
        """Opens (or replaces) the tunnel with these forwards; the stderr goes to the link's log."""
        self._kill_tunnel()
        forwards = [self._forward(f) for f in flags]
        if not forwards:
            return
        self.tunnel_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.tunnel_log, "ab") as errlog:
            self.tunnel = self.popen(
                tunnel_command(self.host, forwards, self.ssh),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=errlog,
            )
        self.forwards = [f[0] for f in forwards]
        wants_local = any(f[0] == "-L" for f in forwards)
        deadline = time.time() + TUNNEL_WAIT
        while True:
            if self.tunnel.poll() is not None:
                raise RuntimeError(f"the ssh tunnel to {self.host} exited: {self._tunnel_tail()}")
            if not wants_local or socket_alive(Path(self.local_mirror_sock())):
                return
            if time.time() > deadline:
                self._kill_tunnel()
                raise RuntimeError(
                    f"the ssh tunnel did not open {self.local_mirror_sock()} within {TUNNEL_WAIT:.0f} s"
                )
            time.sleep(0.2)

    def _tunnel_tail(self) -> str:
        try:
            lines = self.tunnel_log.read_text(errors="ignore").strip().splitlines()
        except OSError:
            lines = []
        return (lines or ["no message"])[-1][-300:]

    def _kill_tunnel(self) -> None:
        if self.tunnel is not None and self.tunnel.poll() is None:
            self.tunnel.terminate()
            try:
                self.tunnel.wait(5)
            except Exception:
                self.tunnel.kill()
        self.tunnel, self.forwards = None, []

    # ---- bring up
    def up(self) -> None:
        name = config.session_name(self.local_label)
        self.local = wait_entry(name, [agent_sessions_dir(self.local_label, self.conf)])
        if self.local is None:
            raise RuntimeError(
                f"{self.local_label} is not running under the name {name} (open it from the page first)"
            )
        if not self.local.key_path:
            raise RuntimeError(f"{self.local_label} has no key file in {self.local.path.parent}")
        # the remote's card and whether my mirror is already up there (another link of mine with that machine)
        self.remote = self._peer("--probe", str(self.local.pid))
        if not self.remote.get("pid"):
            raise RuntimeError(f"{self.host}: card without pid")
        self._start_tunnel(self._needed_forwards(bool(self.remote.get("probe_alive"))))
        write_mirror(self.local.path.parent, self.remote, self.local_mirror_sock(), self.remote_label)
        self._peer(
            "--mirror",
            "--sock",
            self.remote_mirror_sock(),
            "--from",
            self.local_label,
            input=json.dumps(self.local.record()),
        )
        self.state.write(
            local=self.local_label,
            remote=self.remote_label,
            host=config.host_of(self.local_label),
            remote_host=self.host,
            local_name=name,
            remote_name=self.remote.get("name"),
            kind="ssh",
            pid=os.getpid(),
            tunnel_pid=self.tunnel.pid if self.tunnel else None,
            forwards=self.forwards,
            resources=self.resources(),
            up=True,
            why=None,
        )
        rname = self.remote.get("name")
        self.log(f"link {self.local_label} ⇄ {self.remote_label}: {name} and {rname} see each other")

    # ---- stay up
    def wait(self, poll: float = POLL, remote_poll: float = REMOTE_POLL) -> str:
        """Stays until the unlink (SIGTERM), one of the agents closes or the tunnel drops. Redoes a forward
        that another link kept up and that dropped. Returns the reason for leaving."""
        assert self.local and self.remote
        last_remote = time.time()
        while not self._stop:
            if self.tunnel is not None and self.tunnel.poll() is not None:
                return "tunnel dropped: " + self._tunnel_tail()
            if not self.local.path.exists() or not pid_alive(self.local.pid):
                return f"{self.local_label} closed"
            remote_alive = True
            if time.time() - last_remote >= remote_poll:
                last_remote = time.time()
                try:
                    probe = self._peer("--probe", str(self.local.pid))
                except RuntimeError as e:
                    return f"{self.remote_label} closed ({e})"
                if int(probe.get("pid") or 0) != int(self.remote["pid"]):
                    return f"{self.remote_label} was reopened"
                remote_alive = bool(probe.get("probe_alive"))
            needed = self._needed_forwards(remote_alive)
            if any(f not in self.forwards for f in needed):
                # whoever kept this forward up left: it becomes mine (the tunnel is replaced whole,
                # with what was already mine plus what was missing)
                try:
                    self._start_tunnel(needed)
                    self.state.write(
                        forwards=self.forwards, tunnel_pid=self.tunnel.pid if self.tunnel else None
                    )
                    self.log(f"link {self.local_label} ⇄ {self.remote_label}: redid {self.forwards}")
                except RuntimeError as e:
                    return f"could not redo the tunnel: {e}"
            time.sleep(poll)
        return "unlink"

    def stop(self, *_: Any) -> None:
        self._stop = True

    def down(self, why: str = "") -> None:
        """Marks itself as down first (so the other links do not count on me), releases what is only
        mine and closes the tunnel."""
        cur = self.state.read()
        self.state.write(up=False, why=why)
        kept: List[str] = []
        if self.local and self.remote:

            def remote_unmirror(host: str, sessions_dir: str, pid: int) -> None:
                try:
                    self._peer("--unmirror", str(pid), "--sock", self.remote_mirror_sock())
                except RuntimeError as e:  # the other machine may be down; the rest of the cleanup goes on
                    self.log(f"warning: could not undo the mirror on {host}: {e}")

            kept = release(
                cur.get("resources") or self.resources(), self.root, self.state.path, remote_unmirror
            )
        self._kill_tunnel()
        self.state.write(resources=kept)
        self.log(f"link {self.local_label} ⇄ {self.remote_label} undone ({why})")


def run_link(local_label: str, remote_label: str, **kw: Any) -> int:
    """`orq-agent link-run`: brings up, holds and undoes. 0 if it came up; 1 if it never did. Any error,
    even an unexpected one, goes through `down`: an orphan tunnel would be worse than the message."""
    link = Link(local_label, remote_label, **kw)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, link.stop)
    try:
        link.up()
    except Exception as e:
        link.log(f"link failed: {e}")
        link.down("failed: " + str(e)[:200])
        return 1
    try:
        why = link.wait()
    except Exception as e:
        why = f"error: {e}"
    link.down(why)
    return 0
