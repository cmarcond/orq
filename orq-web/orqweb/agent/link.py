"""Vínculo entre agentes de máquinas diferentes: SendMessage/ListAgents atravessando o ssh.

Cada Claude Code vivo tem, em `<config_dir>/sessions/`, um cartão `<pid>.json` (nome, pasta, estado e
`messagingSocketPath`, o socket Unix em que recebe mensagens) e um arquivo `<pid>.<hash>.key` ao lado,
que quem manda mensagem apresenta ao socket. O ListAgents lê a pasta; o SendMessage fala com o socket.
Tudo pensado para uma máquina só.

O vínculo estende isso a duas máquinas, como no spike de 2026-10-12: um `ssh -N` estica o socket de cada
agente até a outra máquina (`-L` e `-R` com caminhos Unix), e o cartão e a chave de cada um são
*espelhados* na pasta `sessions/` do outro, com o `messagingSocketPath` apontando para o socket
encaminhado. Daí cada lado lista e endereça o outro como se fosse local; o resto é o SendMessage normal.

`run_link` roda na máquina do agente local e fala com a outra por `orq-agent peer` (ssh). Ao sair
(unlink, agente fechou, túnel caiu) desfaz os espelhos: entrada de outra máquina não é podada sozinha.
"""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .. import config
from .state import StateFile, pid_alive

TUNNEL_WAIT = 20.0
POLL = 5.0


# ---------------------------------------------------------------- registro de sessões
@dataclass
class Entry:
    """Um cartão de sessão e o arquivo-chave ao lado dele."""

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
        """O que viaja para a outra máquina: cartão, nome do arquivo-chave e seu conteúdo."""
        return {
            "name": self.data.get("name"),
            "pid": self.pid,
            "sock": self.sock,
            "sessions_dir": str(self.path.parent),
            "json": self.data,
            "key_name": self.key_path.name if self.key_path else None,
            "key": self.key_path.read_text() if self.key_path else None,
        }


def sessions_dirs(conf: config.Config) -> List[Path]:
    out: List[Path] = []
    for acc in conf.accounts.values():
        d = acc.config_dir / "sessions"
        if d not in out:
            out.append(d)
    return out


def find_entry(
    name: str, dirs: List[Path], alive: Optional[Callable[[Optional[int]], bool]] = None
) -> Optional[Entry]:
    """Cartão da sessão viva com esse nome (`claude -n`), nas pastas dadas. None se não há."""
    alive = alive or pid_alive  # resolvido na chamada, não na definição (os testes trocam pid_alive)
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if data.get("name") != name or data.get("_orq_mirror"):
                continue
            pid = data.get("pid")
            if not alive(pid):
                continue
            keys = sorted(d.glob(f"{pid}.*.key"))
            return Entry(f, keys[0] if keys else None, data)
    return None


ENTRY_WAIT = 40.0


def wait_entry(name: str, dirs: List[Path], timeout: float = ENTRY_WAIT) -> Optional[Entry]:
    """`find_entry` com paciência: um agente recém-aberto leva alguns segundos para registrar a sessão."""
    deadline = time.time() + timeout
    while True:
        e = find_entry(name, dirs)
        if e is not None or time.time() > deadline:
            return e
        time.sleep(0.5)


def write_mirror(sessions_dir: Path, record: Dict[str, Any], sock: str, origin: str) -> List[Path]:
    """Grava o cartão e a chave de uma sessão de outra máquina, apontando para o socket encaminhado."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    data = dict(record["json"])
    data["messagingSocketPath"] = sock
    data["_orq_mirror"] = origin  # de onde veio; `find_entry` nunca devolve um espelho
    paths = [sessions_dir / f"{record['pid']}.json"]
    paths[0].write_text(json.dumps(data))
    if record.get("key_name") and record.get("key") is not None:
        p = sessions_dir / record["key_name"]
        p.write_text(record["key"])
        os.chmod(str(p), 0o600)
        paths.append(p)
    return paths


def remove_mirror(sessions_dir: Path, pid: int, sock: Optional[str] = None) -> None:
    """Apaga o espelho de `pid` (cartão e chave) e, se dado, o arquivo do socket encaminhado."""
    for f in list(sessions_dir.glob(f"{pid}.json")) + list(sessions_dir.glob(f"{pid}.*.key")):
        try:
            f.unlink()
        except OSError:
            pass
    if sock:
        try:
            os.unlink(sock)
        except OSError:
            pass


# ---------------------------------------------------------------- o túnel
def tunnel_command(
    host: str, local: Entry, remote: Dict[str, Any], ssh: Optional[List[str]] = None
) -> List[str]:
    """`ssh -N` com o socket do remoto esticado até aqui e o do local esticado até lá."""
    local_mirror_sock = os.path.join(local.sock_dir, f"{remote['pid']}.sock")
    remote_mirror_sock = os.path.join(os.path.dirname(remote["sock"]), f"{local.pid}.sock")
    return (ssh or ["ssh"]) + [
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
        "-L",
        f"{local_mirror_sock}:{remote['sock']}",
        "-R",
        f"{remote_mirror_sock}:{local.sock}",
        host,
    ]


def links_dir(label: str) -> Path:
    return config.state_dir(label) / "links"


def link_file(local_label: str, remote_label: str) -> Path:
    return links_dir(local_label) / (config.safe_label(remote_label) + ".json")


def same_host(a: str, b: str) -> bool:
    return config.host_of(a) == config.host_of(b)


def agent_sessions_dir(label: str, conf: Optional[config.Config] = None) -> Path:
    """Pasta sessions/ da conta do agente desta máquina (pelo state.json dele). RuntimeError se não há."""
    st = StateFile(config.state_dir(label) / "state.json").read()
    if not st.get("account"):
        raise RuntimeError(f"{label} não é um agente desta máquina")
    try:
        return (conf or config.load()).account(st["account"]).config_dir / "sessions"
    except KeyError as e:
        raise RuntimeError(e.args[0]) from None


def local_link(local_label: str, remote_label: str, conf: Optional[config.Config] = None) -> Dict[str, Any]:
    """Vínculo entre dois agentes desta máquina: sem túnel. O ListAgents só lê a pasta sessions/ da
    própria conta, então agentes de contas diferentes precisam do cartão e da chave um do outro
    espelhados (mesmo socket, já que é a mesma máquina). Devolve o estado a gravar."""
    conf = conf or config.load()
    dirs, entries = {}, {}
    for lab in (local_label, remote_label):
        dirs[lab] = agent_sessions_dir(lab, conf)
        entries[lab] = wait_entry(config.session_name(lab), [dirs[lab]])
        if entries[lab] is None:
            raise RuntimeError(f"{lab} não está rodando com nome {config.session_name(lab)}")
    mirrors: List[str] = []
    if dirs[local_label].resolve() != dirs[remote_label].resolve():
        for me, other in ((local_label, remote_label), (remote_label, local_label)):
            e = entries[other]
            mirrors += [str(p) for p in write_mirror(dirs[me], e.record(), e.sock, other)]
    return dict(
        local=local_label,
        remote=remote_label,
        host=config.host_of(remote_label),
        local_name=config.session_name(local_label),
        remote_name=config.session_name(remote_label),
        kind="local",
        pid=None,
        # as sessões de então: se um agente for fechado e reaberto, o vínculo (e os espelhos) morreu com ele
        sessions={lab: entries[lab].pid for lab in (local_label, remote_label)},
        mirrors=mirrors,
        up=True,
    )


def local_link_alive(st: Dict[str, Any], root: Path) -> bool:
    """Um vínculo local vive enquanto os dois agentes vivem com as MESMAS sessões de quando foi feito."""
    for lab in (st.get("local", ""), st.get("remote", "")):
        if not (root / config.safe_label(lab) / "agent.sock").exists():
            return False
    pids = st.get("sessions") or {}
    return bool(pids) and all(pid_alive(pid) for pid in pids.values())  # registro sem pids (antigo) = morto


def local_unlink(state: Dict[str, Any]) -> None:
    for m in state.get("mirrors") or []:
        try:
            os.unlink(m)
        except OSError:
            pass


def list_links(root: Optional[Path] = None) -> List[dict]:
    """Vínculos desta máquina (um arquivo por par). `up`: o processo do vínculo vive (ou, num vínculo
    local, os dois agentes vivem)."""
    out = []
    root = root or config.state_root()
    for f in sorted(root.glob("*/links/*.json")):
        st = StateFile(f).read()
        if not st:
            continue
        if st.get("kind") == "local":
            st["up"] = bool(st.get("up")) and local_link_alive(st, root)
        else:
            st["up"] = bool(st.get("up")) and pid_alive(st.get("pid"))
        out.append(st)
    return out


class Link:
    """Um vínculo em execução (o que `orq-agent link-run` roda em primeiro plano).

    `run_remote(args, input)` executa `orq-agent <args>` na outra máquina e devolve o stdout (os testes
    trocam por uma função); `popen` abre o túnel.
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
    ):
        self.local_label, self.remote_label = local_label, remote_label
        self.host = config.host_of(remote_label)
        self.conf = conf or config.load()
        self.run_remote = run_remote or self._ssh_remote
        self.popen, self.log, self.ssh = popen, log, ssh or ["ssh"]
        self.local: Optional[Entry] = None
        self.remote: Optional[Dict[str, Any]] = None
        self.tunnel: Any = None
        self.state = StateFile(link_file(local_label, remote_label))
        self._stop = False

    # ---- a outra máquina
    def _ssh_remote(self, args: List[str], input: Optional[str] = None) -> str:
        cmd = self.ssh + [
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            self.host,
            "~/.local/bin/orq-agent " + shlex.join(args),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, input=input)
        if r.returncode:
            raise RuntimeError((r.stderr.strip() or r.stdout.strip() or "falhou")[-300:])
        return r.stdout

    def local_mirror_sock(self) -> str:
        assert self.local and self.remote
        return os.path.join(self.local.sock_dir, f"{self.remote['pid']}.sock")

    def remote_mirror_sock(self) -> str:
        assert self.local and self.remote
        return os.path.join(os.path.dirname(self.remote["sock"]), f"{self.local.pid}.sock")

    # ---- subir
    def up(self) -> None:
        name = config.session_name(self.local_label)
        self.local = wait_entry(name, sessions_dirs(self.conf))
        if self.local is None:
            raise RuntimeError(
                f"{self.local_label} não está rodando com nome {name} (abra-o pela página antes)"
            )
        if not self.local.key_path:
            raise RuntimeError(f"{self.local_label} não tem arquivo-chave em {self.local.path.parent}")
        out = self.run_remote(["peer", self.remote_label])
        try:
            self.remote = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError(f"resposta inválida de {self.host}: {out[-200:]}") from None
        if self.remote.get("err") or not self.remote.get("pid"):
            err, self.remote = self.remote.get("err") or "cartão sem pid", None
            raise RuntimeError(f"{self.host}: {err}")
        # sobras de um vínculo anterior entre os mesmos dois: o -R recusa caminho que já existe
        remove_mirror(self.local.path.parent, self.remote["pid"], self.local_mirror_sock())
        self.run_remote(["peer", self.remote_label, "--unmirror", str(self.local.pid)])
        self.tunnel = self.popen(
            tunnel_command(self.host, self.local, self.remote, self.ssh),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        deadline = time.time() + TUNNEL_WAIT
        while not os.path.exists(self.local_mirror_sock()):
            if self.tunnel.poll() is not None:
                err = (
                    self.tunnel.stderr.read().decode(errors="ignore").strip()[-300:]
                    if self.tunnel.stderr
                    else ""
                )
                raise RuntimeError(f"o túnel ssh para {self.host} saiu: {err or 'sem mensagem'}")
            if time.time() > deadline:
                self.tunnel.kill()
                raise RuntimeError(f"o túnel ssh não abriu {self.local_mirror_sock()} em {TUNNEL_WAIT:.0f} s")
            time.sleep(0.2)
        write_mirror(self.local.path.parent, self.remote, self.local_mirror_sock(), self.remote_label)
        self.run_remote(
            [
                "peer",
                self.remote_label,
                "--mirror",
                "--sock",
                self.remote_mirror_sock(),
                "--from",
                self.local_label,
            ],
            input=json.dumps(self.local.record()),
        )
        self.state.write(
            local=self.local_label,
            remote=self.remote_label,
            host=self.host,
            local_name=name,
            remote_name=self.remote.get("name"),
            pid=os.getpid(),
            tunnel_pid=self.tunnel.pid,
            up=True,
        )
        rname = self.remote.get("name")
        self.log(f"vínculo {self.local_label} ⇄ {self.remote_label}: {name} e {rname} se enxergam")

    # ---- ficar de pé
    def wait(self, poll: float = POLL) -> str:
        """Fica até o unlink (SIGTERM), o agente local fechar ou o túnel cair. Devolve o motivo."""
        assert self.local and self.tunnel
        while not self._stop:
            if self.tunnel.poll() is not None:
                return "túnel caiu"
            if not self.local.path.exists():
                return f"{self.local_label} fechou"
            time.sleep(poll)
        return "unlink"

    def stop(self, *_: Any) -> None:
        self._stop = True

    def down(self, why: str = "") -> None:
        if self.local and self.remote:
            remove_mirror(self.local.path.parent, self.remote["pid"], self.local_mirror_sock())
            try:
                self.run_remote(["peer", self.remote_label, "--unmirror", str(self.local.pid)])
            except Exception as e:  # a outra máquina pode ter caído; o resto da limpeza segue
                self.log(f"aviso: não desfiz o espelho em {self.host}: {e}")
        if self.tunnel is not None and self.tunnel.poll() is None:
            self.tunnel.terminate()
            try:
                self.tunnel.wait(5)
            except Exception:
                self.tunnel.kill()
        self.state.write(up=False, why=why)
        self.log(f"vínculo {self.local_label} ⇄ {self.remote_label} desfeito ({why})")


def run_link(local_label: str, remote_label: str, **kw: Any) -> int:
    """`orq-agent link-run`: sobe, segura e desfaz. 0 se subiu; 1 se nem subiu."""
    link = Link(local_label, remote_label, **kw)
    signal.signal(signal.SIGTERM, link.stop)
    signal.signal(signal.SIGINT, link.stop)
    try:
        link.up()
    except RuntimeError as e:
        link.log(f"vínculo falhou: {e}")
        link.down("falhou: " + str(e)[:120])
        return 1
    why = link.wait()
    link.down(why)
    return 0
