"""Chamadas aos utilitários do claude-multiconta e às máquinas remotas (ssh).

Tudo passa por `Runner`, que os testes substituem por respostas prontas.
"""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import config

LAPTOP = "laptop"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]  # (cmd, timeout[, input=texto])


def run(cmd: List[str], timeout: float, input: Optional[str] = None) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)


def usage_remote_cmd() -> str:
    """Cota numa máquina remota: pelo servidor tmux (Keychain no Mac), senão direto."""
    return 'tmux run-shell "$HOME/.local/bin/usage-all --json" 2>/dev/null || ~/.local/bin/usage-all --json'


def ssh_cmd(host: str, remote: str, connect_timeout: int = 5, config: Optional[str] = None) -> List[str]:
    """ssh sem interação; `config` (ORQ_SSH_CONFIG) é um ssh_config próprio, usado no container."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}"]
    if config:
        cmd += ["-F", config]
    return cmd + [host, remote]


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
    def __init__(
        self,
        bin_dir: Optional[Path] = None,
        runner: Runner = run,
        local_host: Optional[str] = LAPTOP,
        ssh_config: Optional[str] = None,
    ):
        self.bin = bin_dir or config.bin_dir()
        self.run = runner
        self.local = local_host  # nome desta máquina; None = tudo por ssh (container)
        self.ssh_config = ssh_config

    def ssh(self, host: str, remote: str, connect_timeout: int = 5) -> List[str]:
        return ssh_cmd(host, remote, connect_timeout, self.ssh_config)

    def all_hosts(self, hosts: List[str]) -> List[str]:
        return ([self.local] if self.local else []) + list(hosts)

    # ---- cota
    def usage(self, host: Optional[str] = None) -> Dict[str, Any]:
        """`usage-all --json`: por conta, % usado, ritmo e segundos até o reset de 5h e 7d. {} se falhar.

        Com `host`, roda lá por ssh. Num Mac a credencial fica no Keychain, que uma sessão ssh não
        enxerga; `tmux run-shell` executa dentro do servidor tmux da sessão gráfica, que enxerga.
        Sem servidor tmux (Linux, ou Mac sem o LaunchAgent) cai no comando direto.
        """
        try:
            if host is None or host == self.local:
                r = self.run([str(self.bin / "usage-all"), "--json"], 60)
            else:
                r = self.run(self.ssh(host, usage_remote_cmd()), 90)
            return json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {}

    # ---- por máquina
    def _local_or_ssh(self, host: str, local: List[str], remote: str, timeout: float):
        return self.run(local, timeout) if host == self.local else self.run(self.ssh(host, remote), timeout)

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

    # ---- enviar arquivo para a pasta do agente
    def put_file(self, host: str, path: str, data: bytes, timeout: float = 300) -> Optional[str]:
        """Grava `data` em `path` na máquina, criando a pasta. Remoto: ssh com os bytes no stdin.

        Devolve texto de erro ou None."""
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
                return (r.stderr.decode(errors="ignore").strip() or "falhou")[-200:]
            return None
        except (OSError, subprocess.TimeoutExpired) as e:
            return str(e)

    # ---- converter claude de terminal em agente da página
    def convert(self, host: str, label: str) -> dict:
        """`orq-agent convert` na máquina do tmux. Devolve o JSON dele ou {ok: False, msg}."""
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
                "msg": (r.stderr.strip() or r.stdout.strip() or "falhou")[-200:],
            }

    # ---- abrir agente
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
    ) -> Optional[str]:
        """Sobe o supervisor na máquina. Devolve mensagem de erro ou None.

        Um nível de cima leva o papel (`role`) pelo stdin do `orq-agent spawn --role-file -`, que o
        guarda na máquina do agente; `parent` é o rótulo do agente logo abaixo e `level` o nome do nível.
        """
        args = ["spawn", "--dir", cwd, "--account", account, "--label", label, "--mode", mode]
        if parent:
            args += ["--parent", parent]
        if level:
            args += ["--level", level]
        extra = {}
        if role is not None:
            args += ["--role-file", "-"]
            extra["input"] = role
        try:
            if host == self.local:
                r = self.run([str(self.bin / "orq-agent")] + args, 60, **extra)
            else:
                import shlex

                r = self.run(self.ssh(host, "~/.local/bin/orq-agent " + shlex.join(args), 8), 60, **extra)
        except (OSError, subprocess.TimeoutExpired) as e:
            return str(e)
        if r.returncode:
            return (r.stderr.strip() or r.stdout.strip() or "falhou")[-200:]
        return None
