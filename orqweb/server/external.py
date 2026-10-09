"""Chamadas aos utilitários do claude-multiconta e às máquinas remotas (ssh).

Tudo passa por `Runner`, que os testes substituem por respostas prontas.
"""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import config

LAPTOP = "laptop"

Runner = Callable[[List[str], float], "subprocess.CompletedProcess[str]"]


def run(cmd: List[str], timeout: float) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def ssh_cmd(host: str, remote: str, connect_timeout: int = 5) -> List[str]:
    return ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}", host, remote]


def dedupe_labels(items: List[dict]) -> List[dict]:
    """Duas sessões de terminal com o mesmo rótulo (mesma pasta) viram `pasta-2@host`, `pasta-3@host`…

    Agentes da página (kind=sdk) ficam com o rótulo exato: ele é a chave do socket.
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
    def __init__(self, bin_dir: Optional[Path] = None, runner: Runner = run):
        self.bin = bin_dir or config.bin_dir()
        self.run = runner

    # ---- cota
    def usage(self, script: str) -> Dict[str, Any]:
        """`claude-usage-fast --json` ou `codex-usage-fast --json`."""
        try:
            r = self.run([str(self.bin / script), "--json"], 60)
            return json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {"err": "falha ao consultar"} if script.startswith("codex") else {}

    # ---- por máquina
    def _local_or_ssh(self, host: str, local: List[str], remote: str, timeout: float):
        return self.run(local, timeout) if host == LAPTOP else self.run(ssh_cmd(host, remote), timeout)

    def projects(self, host: str) -> Tuple[str, List[dict], Optional[str]]:
        """Conversa mais recente de cada pasta da máquina (claude-last --projects)."""
        try:
            r = self._local_or_ssh(
                host, [str(self.bin / "claude-last"), "--projects"], "~/.local/bin/claude-last --projects", 20
            )
            items = json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            return host, [], str(e)[:80] or "sem resposta"
        for p in items:
            p["host"] = host
        return host, items, None

    def agents(self, host: str) -> Tuple[str, List[dict], Optional[str]]:
        """Agentes da máquina (orq --status-json); rótulos ganham @host se não tiverem."""
        try:
            r = self._local_or_ssh(
                host, [str(self.bin / "orq"), "--status-json"], "~/.local/bin/orq --status-json", 20
            )
            items = json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return host, [], "sem resposta"
        for a in items:
            a["host"] = host
            if "@" not in a.get("label", ""):
                a["label"] = f"{a.get('label', '?')}@{host}"
        return host, dedupe_labels(items), None

    def all_projects(self, hosts: List[str], limit: int = 30) -> Tuple[List[dict], Dict[str, str]]:
        with ThreadPoolExecutor(max_workers=12) as ex:
            res = list(ex.map(self.projects, [LAPTOP] + list(hosts)))
        items, errs = [], {}
        for host, lst, err in res:
            if err and not lst:
                errs[host] = err
            items += lst
        items.sort(key=lambda p: -p.get("mtime", 0))
        return items[:limit], errs

    def all_agents(self, hosts: List[str]) -> Tuple[List[dict], Dict[str, str]]:
        with ThreadPoolExecutor(max_workers=12) as ex:
            res = list(ex.map(self.agents, [LAPTOP] + list(hosts)))
        return [a for _, lst, _ in res for a in lst], {h: e for h, _, e in res if e}

    # ---- converter claude de terminal em agente da página
    def convert(self, host: str, label: str) -> dict:
        """`orq-agent convert` na máquina do tmux. Devolve o JSON dele ou {ok: False, msg}."""
        try:
            if host == LAPTOP:
                r = self.run([str(self.bin / "orq-agent"), "convert", label], 90)
            else:
                import shlex

                r = self.run(ssh_cmd(host, "~/.local/bin/orq-agent convert " + shlex.quote(label), 8), 90)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"ok": False, "label": label, "how": "", "msg": str(e)}
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {
                "ok": False,
                "label": label,
                "how": "",
                "msg": (r.stderr.strip() or r.stdout.strip() or "falhou")[-200:],
            }

    # ---- abrir agente
    def spawn(self, host: str, label: str, cwd: str, account: str, mode: str) -> Optional[str]:
        """Sobe o supervisor na máquina. Devolve mensagem de erro ou None."""
        args = ["spawn", "--dir", cwd, "--account", account, "--label", label, "--mode", mode]
        try:
            if host == LAPTOP:
                r = self.run([str(self.bin / "orq-agent")] + args, 60)
            else:
                import shlex

                r = self.run(ssh_cmd(host, "~/.local/bin/orq-agent " + shlex.join(args), 8), 60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return str(e)
        if r.returncode:
            return (r.stderr.strip() or r.stdout.strip() or "falhou")[-200:]
        return None
