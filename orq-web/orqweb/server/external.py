"""Calls to the claude-multiconta utilities and to the remote machines (ssh).

Everything goes through `Runner`, which the tests replace with canned responses.
"""

import json
import os
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Optional, Tuple

from .. import config
from ..agent import files as F

LAPTOP = "laptop"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]  # (cmd, timeout[, input=text])


def run(cmd: List[str], timeout: float, input: Optional[str] = None) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)


def usage_remote_cmd() -> str:
    """Quota on a remote machine: through the tmux server (Keychain on the Mac), otherwise directly."""
    return 'tmux run-shell "$HOME/.local/bin/usage-all --json" 2>/dev/null || ~/.local/bin/usage-all --json'


HISTORY_REMOTE = "~/.local/bin/usage-hist painel --json"  # usage history (claude-multiconta, phase 6)


def ssh_cmd(host: str, remote: str, connect_timeout: int = 5, config: Optional[str] = None) -> List[str]:
    """Non-interactive ssh; `config` (ORQ_SSH_CONFIG) is a dedicated ssh_config, used in the container."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}"]
    if config:
        cmd += ["-F", config]
    return cmd + [host, remote]


def dedupe_labels(items: List[dict]) -> List[dict]:
    """Two terminal sessions with the same label (same folder) become `folder-2@host`, `folder-3@host`…

    Page agents (kind=sdk) keep the exact label: it is the key of the socket.
    """
    seen: Dict[str, int] = {}
    for a in items:
        lab = a.get("label", "?")
        if a.get("kind") == "sdk":
            seen[lab] = seen.get(lab, 0) + 1
    for a in items:
        if a.get("kind") == "sdk":
            continue
        lab = a.get("label", "?")
        n = seen.get(lab, 0)
        seen[lab] = n + 1
        if n:
            base, _, host = lab.rpartition("@")
            a["label"] = f"{base}-{n + 1}@{host}" if host else f"{lab}-{n + 1}"
    return items


class External:
    def __init__(
        self,
        bin_dir: Optional[Path] = None,
        runner: Runner = run,
        local_host: Optional[str] = LAPTOP,
        ssh_config: Optional[str] = None,
        popen: Callable[..., Any] = subprocess.Popen,
    ):
        self.bin = bin_dir or config.bin_dir()
        self.run = runner
        self.popen = popen  # for downloads, which are too long to wait for the end (run)
        self.links_by_host: Dict[str, List[dict]] = {}
        self.local = local_host  # this machine's name; None = everything over ssh (container)
        self.ssh_config = ssh_config

    def ssh(self, host: str, remote: str, connect_timeout: int = 5) -> List[str]:
        return ssh_cmd(host, remote, connect_timeout, self.ssh_config)

    def all_hosts(self, hosts: List[str]) -> List[str]:
        return ([self.local] if self.local else []) + list(hosts)

    # ---- quota
    def usage(self, host: Optional[str] = None) -> Dict[str, Any]:
        """`usage-all --json`: per account, % used, pace and seconds until the 5h and 7d reset. {} on failure.

        With `host`, it runs there over ssh. On a Mac the credential lives in the Keychain, which an ssh
        session cannot see; `tmux run-shell` runs inside the tmux server of the graphical session, which can.
        Without a tmux server (Linux, or a Mac without the LaunchAgent) it falls back to the direct command.
        """
        try:
            if host is None or host == self.local:
                r = self.run([str(self.bin / "usage-all"), "--json"], 60)
            else:
                r = self.run(self.ssh(host, usage_remote_cmd()), 90)
            return json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {}

    def history(self, host: Optional[str] = None) -> Dict[str, Any]:
        """`usage-hist painel --json`: per account, today, pace, forecast and the 24 h by hour; alerts; age of
        the collection. {} if there is no usage-hist or database (the page carries on with the quota only)."""
        try:
            if host is None or host == self.local:
                r = self.run([str(self.bin / "usage-hist"), "painel", "--json"], 30)
            else:
                r = self.run(self.ssh(host, HISTORY_REMOTE), 45)
            data = json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {}
        return data if isinstance(data, dict) and isinstance(data.get("accounts"), dict) else {}

    def account(self, host: Optional[str], name: str) -> Dict[str, Any]:
        """`usage-hist conta NAME --json`: the panel of one account (24 h, 7 days, week curve, projects).
        {} if there is no usage-hist or database."""
        import shlex

        try:
            if host is None or host == self.local:
                r = self.run([str(self.bin / "usage-hist"), "conta", name, "--json"], 30)
            else:
                r = self.run(
                    self.ssh(host, "~/.local/bin/usage-hist conta " + shlex.quote(name) + " --json"), 45
                )
            data = json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {}
        return data if isinstance(data, dict) and data.get("account") == name else {}

    def usage_table(self, host: Optional[str], hours: float) -> Dict[str, Any]:
        """`usage-hist uso --horas N --json`: who is spending each account in the window (the page's usage
        tab). {} if there is no usage-hist or database."""
        args = ["uso", "--horas", f"{hours:g}", "--json"]
        try:
            if host is None or host == self.local:
                r = self.run([str(self.bin / "usage-hist")] + args, 30)
            else:
                r = self.run(self.ssh(host, "~/.local/bin/usage-hist " + " ".join(args)), 45)
            data = json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {}
        return data if isinstance(data, dict) and isinstance(data.get("accounts"), list) else {}

    # ---- per machine
    def _local_or_ssh(self, host: str, local: List[str], remote: str, timeout: float):
        return self.run(local, timeout) if host == self.local else self.run(self.ssh(host, remote), timeout)

    def projects(self, host: str) -> Tuple[str, List[dict], Optional[str]]:
        """Most recent conversation of each folder on the machine (claude-last --projects)."""
        try:
            r = self._local_or_ssh(
                host, [str(self.bin / "claude-last"), "--projects"], "~/.local/bin/claude-last --projects", 20
            )
            items = json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            return host, [], str(e)[:80] or "no response"
        for p in items:
            p["host"] = host
        return host, items, None

    LINKS_MARK = "---orq-links---"

    def agents(self, host: str) -> Tuple[str, List[dict], Optional[str]]:
        """The machine's agents (orq --status-json) and, on the same connection, its links (orq-agent links,
        kept in `self.links_by_host`); labels get @host if they do not have it."""
        try:
            if host == self.local:
                r = self.run([str(self.bin / "orq"), "--status-json"], 20)
                items = json.loads(r.stdout)
                try:
                    lr = self.run([str(self.bin / "orq-agent"), "links"], 20)
                    links = json.loads(lr.stdout or "[]")
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    links = []
            else:
                # two ssh connections in a row to the same machine tend to drop: everything in one
                remote = (
                    "~/.local/bin/orq --status-json; echo; echo "
                    + self.LINKS_MARK
                    + "; ~/.local/bin/orq-agent links 2>/dev/null"
                )
                r = self.run(self.ssh(host, remote), 20)
                head, _, tail = r.stdout.partition(self.LINKS_MARK)
                items = json.loads(head)
                try:
                    links = json.loads(tail.strip() or "[]")
                except ValueError:
                    links = []
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.links_by_host.pop(host, None)  # without the machine, its links do not count
            return host, [], "no response"
        self.links_by_host[host] = [lk for lk in links if isinstance(lk, dict)]
        for a in items:
            a["host"] = host
            if "@" not in a.get("label", ""):
                a["label"] = f"{a.get('label', '?')}@{host}"
        return host, dedupe_labels(items), None

    @property
    def links(self) -> List[dict]:
        """Links of all the machines, as seen in the last query to each one."""
        return [lk for host in sorted(self.links_by_host) for lk in self.links_by_host[host]]

    # ---- link between agents (orq-agent link/unlink on the local agent's machine)
    def link(self, host: str, local_label: str, remote_label: str) -> Optional[str]:
        """Returns error text or None."""
        return self._link_cmd(
            host, "link", local_label, remote_label, 150
        )  # the link waits for cards and tunnel

    def unlink(self, host: str, local_label: str, remote_label: str) -> Optional[str]:
        return self._link_cmd(host, "unlink", local_label, remote_label, 45)

    def _link_cmd(self, host: str, op: str, a: str, b: str, timeout: float) -> Optional[str]:
        try:
            if host == self.local:
                r = self.run([str(self.bin / "orq-agent"), op, a, b], timeout)
            else:
                r = self.run(self.ssh(host, "~/.local/bin/orq-agent " + shlex.join([op, a, b]), 8), timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return str(e)
        if r.returncode:
            return (r.stderr.strip() or r.stdout.strip() or "failed")[-300:]
        return None

    def all_projects(self, hosts: List[str], limit: int = 30) -> Tuple[List[dict], Dict[str, str]]:
        with ThreadPoolExecutor(max_workers=12) as ex:
            res = list(ex.map(self.projects, self.all_hosts(hosts)))
        items, errs = [], {}
        for host, lst, err in res:
            if err and not lst:
                errs[host] = err
            items += lst
        items.sort(key=lambda p: -p.get("mtime", 0))
        return items[:limit], errs

    def all_agents(self, hosts: List[str]) -> Tuple[List[dict], Dict[str, str]]:
        with ThreadPoolExecutor(max_workers=12) as ex:
            res = list(ex.map(self.agents, self.all_hosts(hosts)))
        return [a for _, lst, _ in res for a in lst], {h: e for h, _, e in res if e}

    # ---- send a file to the agent's folder
    def put_file(self, host: str, path: str, data: bytes, timeout: float = 300) -> Optional[str]:
        """Writes `data` to `path` on the machine, creating the folder. Remote: ssh with the bytes on stdin.

        Returns error text or None."""
        import shlex

        try:
            if host == self.local:
                p = Path(os.path.expanduser(path))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                return None
            remote = f"mkdir -p {shlex.quote(os.path.dirname(path))} && cat > {shlex.quote(path)}"
            r = subprocess.run(self.ssh(host, remote, 8), input=data, capture_output=True, timeout=timeout)
            if r.returncode:
                return (r.stderr.decode(errors="ignore").strip() or "failed")[-200:]
            return None
        except (OSError, subprocess.TimeoutExpired) as e:
            return str(e)

    # ---- files the agent shares (the project's _outbox/)
    def list_files(self, host: str, folder: str) -> Tuple[List[dict], Optional[str]]:
        """Files of the folder on the machine (`orq-agent ls`): ([{name, size, mtime}], error or None)."""
        if host == self.local:
            return F.list_dir(Path(os.path.expanduser(folder))), None
        try:
            r = self.run(self.ssh(host, "~/.local/bin/orq-agent ls " + shlex.quote(folder), 8), 30)
            if r.returncode:
                err = (r.stderr.strip() or r.stdout.strip() or "failed")[-200:]
                if "invalid choice" in err or "argument cmd" in err:
                    err = f"the orq-agent on {host} is old (no `ls`): run orq-web/deploy.sh {host}"
                return [], err
            return json.loads(r.stdout), None
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            return [], (str(e)[:120] or "no response")

    def open_file(self, host: str, path: str, start: int = 0, end: Optional[int] = None) -> "FileStream":
        """Bytes of `path` (or of the range, end inclusive) on the machine, with the total size up front.

        Local: the open file. Remote: `orq-agent cat` over ssh, reading the JSON line it sends
        before the bytes. Raises FileNotFoundError/ValueError (range) or OSError (ssh)."""
        if host == self.local:
            size, length, f = F.open_range(Path(os.path.expanduser(path)), start, end)
            return FileStream(size, length, f)
        spec = f"{start}-{'' if end is None else end}"
        cmd = self.ssh(host, "~/.local/bin/orq-agent cat " + shlex.quote(path) + " --range " + spec, 8)
        proc = self.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        head = F.read_header(proc.stdout)
        if "err" in head:
            proc.stdout.close()
            proc.wait()
            msg = str(head["err"])
            if "outside the file" in msg or "fora do arquivo" in msg:  # second form: agents not yet updated
                raise ValueError(msg)
            raise FileNotFoundError(msg)
        return FileStream(int(head["size"]), int(head["length"]), proc.stdout, proc)

    # ---- convert a terminal claude into a page agent
    def convert(self, host: str, label: str) -> dict:
        """`orq-agent convert` on the tmux machine. Returns its JSON or {ok: False, msg}."""
        try:
            if host == self.local:
                r = self.run([str(self.bin / "orq-agent"), "convert", label], 90)
            else:
                import shlex

                r = self.run(self.ssh(host, "~/.local/bin/orq-agent convert " + shlex.quote(label), 8), 90)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"ok": False, "label": label, "how": "", "msg": str(e)}
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {
                "ok": False,
                "label": label,
                "how": "",
                "msg": (r.stderr.strip() or r.stdout.strip() or "failed")[-200:],
            }

    # ---- open an agent
    def spawn(
        self,
        host: str,
        label: str,
        cwd: str,
        account: str,
        mode: str,
        role: Optional[str] = None,
        parent: Optional[str] = None,
        level: Optional[str] = None,
        restart: bool = False,
        permission: Optional[str] = None,
        set_id: Optional[str] = None,
        pipeline: Optional[str] = None,
    ) -> Optional[str]:
        """Starts the supervisor on the machine. Returns an error message or None.

        The role (`role`: of a level, or the own prompt of a set's agent) goes through the stdin of
        `orq-agent spawn --role-file -`, which keeps it on the agent's machine; `parent` is the label of the
        agent right below and `level` the name of the level. `permission` changes the permission mode of this
        agent only and `set_id` marks the set it came from. `restart` first closes the agent that is running
        with that label (reopen with a name, switch account).
        """
        args = ["spawn", "--dir", cwd, "--account", account, "--label", label, "--mode", mode]
        if parent:
            args += ["--parent", parent]
        if level:
            args += ["--level", level]
        if permission:
            args += ["--permission-mode", permission]
        if set_id:
            args += ["--set", set_id]
        if pipeline:
            args += ["--pipeline", pipeline]
        if restart:
            args.append("--restart")
        extra = {}
        if role is not None:
            args += ["--role-file", "-"]
            extra["input"] = role
        try:
            if host == self.local:
                r = self.run([str(self.bin / "orq-agent")] + args, 90, **extra)
            else:
                import shlex

                r = self.run(self.ssh(host, "~/.local/bin/orq-agent " + shlex.join(args), 8), 90, **extra)
        except (OSError, subprocess.TimeoutExpired) as e:
            return str(e)
        if r.returncode:
            return (r.stderr.strip() or r.stdout.strip() or "failed")[-200:]
        return None


class FileStream:
    """A file (or range) coming from a machine: total size, bytes to read and where to read them from."""

    def __init__(self, size: int, length: int, stream: BinaryIO, proc: Any = None):
        self.size, self.length, self.stream, self.proc = size, length, stream, proc

    def chunks(self, chunk: int = F.CHUNK):
        left = self.length
        while left > 0:
            b = self.stream.read(min(chunk, left))
            if not b:
                break
            left -= len(b)
            yield b

    def close(self) -> None:
        try:
            self.stream.close()
        except OSError:
            pass
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
            self.proc.wait()
