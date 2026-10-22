"""Chamadas aos utilitários do claude-multiconta e às máquinas remotas (ssh).

Tudo passa por `Runner`, que os testes substituem por respostas prontas.
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

Runner = Callable[..., "subprocess.CompletedProcess[str]"]  # (cmd, timeout[, input=texto])


def run(cmd: List[str], timeout: float, input: Optional[str] = None) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)


def usage_remote_cmd() -> str:
    """Cota numa máquina remota: pelo servidor tmux (Keychain no Mac), senão direto."""
    return 'tmux run-shell "$HOME/.local/bin/usage-all --json" 2>/dev/null || ~/.local/bin/usage-all --json'


HISTORY_REMOTE = "~/.local/bin/usage-hist painel --json"  # histórico de uso (claude-multiconta, fase 6)


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
        popen: Callable[..., Any] = subprocess.Popen,
    ):
        self.bin = bin_dir or config.bin_dir()
        self.run = runner
        self.popen = popen  # para os downloads, que são longos demais para esperar o fim (run)
        self.links_by_host: Dict[str, List[dict]] = {}
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

    def history(self, host: Optional[str] = None) -> Dict[str, Any]:
        """`usage-hist painel --json`: por conta, hoje, ritmo, previsão e as 24 h por hora; alertas; idade da
        coleta. {} se não houver usage-hist ou banco (a página segue só com a cota)."""
        try:
            if host is None or host == self.local:
                r = self.run([str(self.bin / "usage-hist"), "painel", "--json"], 30)
            else:
                r = self.run(self.ssh(host, HISTORY_REMOTE), 45)
            data = json.loads(r.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {}
        return data if isinstance(data, dict) and isinstance(data.get("accounts"), dict) else {}

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

    LINKS_MARK = "---orq-links---"

    def agents(self, host: str) -> Tuple[str, List[dict], Optional[str]]:
        """Agentes da máquina (orq --status-json) e, na mesma conexão, os vínculos dela (orq-agent links,
        guardados em `self.links_by_host`); rótulos ganham @host se não tiverem."""
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
                # duas conexões ssh seguidas à mesma máquina costumam cair: tudo numa só
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
            self.links_by_host.pop(host, None)  # sem a máquina, os vínculos dela não contam
            return host, [], "sem resposta"
        self.links_by_host[host] = [lk for lk in links if isinstance(lk, dict)]
        for a in items:
            a["host"] = host
            if "@" not in a.get("label", ""):
                a["label"] = f"{a.get('label', '?')}@{host}"
        return host, dedupe_labels(items), None

    @property
    def links(self) -> List[dict]:
        """Vínculos de todas as máquinas, como vistos na última consulta a cada uma."""
        return [lk for host in sorted(self.links_by_host) for lk in self.links_by_host[host]]

    # ---- vínculo entre agentes (orq-agent link/unlink na máquina do agente local)
    def link(self, host: str, local_label: str, remote_label: str) -> Optional[str]:
        """Devolve texto de erro ou None."""
        return self._link_cmd(host, "link", local_label, remote_label, 150)  # o link espera cartões e túnel

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
            return (r.stderr.strip() or r.stdout.strip() or "falhou")[-300:]
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

    # ---- arquivos que o agente compartilha (a _outbox/ do projeto)
    def list_files(self, host: str, folder: str) -> Tuple[List[dict], Optional[str]]:
        """Arquivos da pasta na máquina (`orq-agent ls`): ([{name, size, mtime}], erro ou None)."""
        if host == self.local:
            return F.list_dir(Path(os.path.expanduser(folder))), None
        try:
            r = self.run(self.ssh(host, "~/.local/bin/orq-agent ls " + shlex.quote(folder), 8), 30)
            if r.returncode:
                err = (r.stderr.strip() or r.stdout.strip() or "falhou")[-200:]
                if "invalid choice" in err or "argument cmd" in err:
                    err = f"o orq-agent de {host} é antigo (sem `ls`): rode orq-web/deploy.sh {host}"
                return [], err
            return json.loads(r.stdout), None
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            return [], (str(e)[:120] or "sem resposta")

    def open_file(self, host: str, path: str, start: int = 0, end: Optional[int] = None) -> "FileStream":
        """Bytes de `path` (ou da faixa, fim inclusivo) na máquina, com o tamanho total na frente.

        Local: o arquivo aberto. Remoto: `orq-agent cat` por ssh, lendo a linha JSON que ele manda
        antes dos bytes. Levanta FileNotFoundError/ValueError (faixa) ou OSError (ssh)."""
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
            if "fora do arquivo" in msg:
                raise ValueError(msg)
            raise FileNotFoundError(msg)
        return FileStream(int(head["size"]), int(head["length"]), proc.stdout, proc)

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
        restart: bool = False,
        permission: Optional[str] = None,
        set_id: Optional[str] = None,
        pipeline: Optional[str] = None,
    ) -> Optional[str]:
        """Sobe o supervisor na máquina. Devolve mensagem de erro ou None.

        O papel (`role`: de um nível, ou o prompt próprio de um agente de conjunto) vai pelo stdin do
        `orq-agent spawn --role-file -`, que o guarda na máquina do agente; `parent` é o rótulo do agente
        logo abaixo e `level` o nome do nível. `permission` troca o modo de permissão só deste agente e
        `set_id` marca o conjunto de origem. `restart` fecha o agente que estiver rodando com esse rótulo
        antes (reabrir com nome, trocar conta).
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
            return (r.stderr.strip() or r.stdout.strip() or "falhou")[-200:]
        return None


class FileStream:
    """Um arquivo (ou faixa) vindo de uma máquina: tamanho total, bytes a ler e de onde lê-los."""

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
