"""Vínculo entre agentes: SendMessage/ListAgents entre contas e entre máquinas.

Cada Claude Code vivo tem, em `<config_dir>/sessions/`, um cartão `<pid>.json` (nome, pasta, estado e
`messagingSocketPath`, o socket Unix em que recebe mensagens) e um arquivo `<pid>.<hash>.key` ao lado,
que quem manda mensagem apresenta ao socket. O ListAgents lê a pasta da PRÓPRIA conta; o SendMessage
fala com o socket. Tudo pensado para uma conta numa máquina só.

O vínculo estende isso. Na mesma máquina, entre contas: o cartão e a chave de cada agente são
*espelhados* na pasta `sessions/` do outro (mesmo socket). Entre máquinas (spike de 2026-10-12): um
`ssh -N` estica o socket de cada agente até a outra máquina (`-L` e `-R` com caminhos Unix) e o
espelho aponta para o socket encaminhado, que tem nome próprio (`orq-<pid>.sock`) para nunca se
confundir com o de uma sessão de verdade. Daí cada lado lista e endereça o outro como se fosse local.

Espelhos e sockets encaminhados são RECURSOS COMPARTILHADOS: dois vínculos que trazem o mesmo agente
para a mesma pasta (o agente da aba e o professor acima dele, na mesma conta, ambos ligados ao mesmo
executor remoto) usam o mesmo cartão e o mesmo socket. Cada vínculo anota os recursos de que depende
(`resources` no seu arquivo em `~/.cache/orq/<agente>/links/`); ao morrer, só apaga o que nenhum outro
vínculo vivo desta máquina ainda usa. Um encaminhamento que já está de pé (socket que aceita conexão) é
reaproveitado em vez de refeito; se quem o mantinha sai, o vínculo que sobrou o refaz no próximo ciclo.

`run_link` roda na máquina do agente local e fala com a outra por `orq-agent peer` (ssh). Sai, e
desfaz o que é só seu, quando: unlink (SIGTERM), o agente local fecha, o remoto fecha, ou o túnel cai.
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
POLL = 5.0  # ciclo do vínculo: túnel, agente local, socket encaminhado
REMOTE_POLL = 30.0  # de quanto em quanto pergunta à outra máquina se o remoto ainda vive
ENTRY_WAIT = 40.0  # um agente recém-aberto leva alguns segundos para registrar a sessão
KEY_RE = re.compile(r"^\d+\.[0-9a-fA-F]+\.key$")


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


def agent_sessions_dir(label: str, conf: Optional[config.Config] = None) -> Path:
    """Pasta sessions/ da conta do agente desta máquina (pelo state.json dele). RuntimeError se não há."""
    st = StateFile(config.state_dir(label) / "state.json").read()
    if not st.get("account"):
        raise RuntimeError(f"{label} não é um agente desta máquina")
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
    """Cartão da sessão viva com esse nome (`claude -n`), nas pastas dadas; nunca um espelho."""
    alive = alive or pid_alive  # resolvido na chamada, não na definição (os testes trocam pid_alive)
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


# ---------------------------------------------------------------- espelhos e sockets encaminhados
def mirror_sock(sock_dir: str, pid: int) -> str:
    """Onde o socket encaminhado de um agente de outra máquina fica aqui: nome que nenhuma sessão usa."""
    return os.path.join(sock_dir, f"orq-{pid}.sock")


def write_mirror(sessions_dir: Path, record: Dict[str, Any], sock: str, origin: str) -> List[Path]:
    """Grava o cartão e a chave de uma sessão de outra conta/máquina, apontando para `sock`.

    Recusa (RuntimeError) se já há um cartão de sessão DE VERDADE com esse pid na pasta: pids se repetem
    entre máquinas, e escrever por cima tiraria uma sessão local do ar. Idempotente sobre um espelho."""
    pid = int(record["pid"])
    key_name = record.get("key_name")
    if key_name is not None and not (KEY_RE.match(key_name) and key_name.startswith(f"{pid}.")):
        raise ValueError(f"nome de chave inválido: {key_name!r}")
    card = sessions_dir / f"{pid}.json"
    existing = read_card(card)
    if existing is not None and not existing.get("_orq_mirror"):
        raise RuntimeError(f"há uma sessão local com pid {pid} em {sessions_dir}; não espelho por cima")
    sessions_dir.mkdir(parents=True, exist_ok=True)
    data = dict(record["json"])
    data["messagingSocketPath"] = sock
    data["_orq_mirror"] = origin  # de onde veio; `find_entry` nunca devolve um espelho
    card.write_text(json.dumps(data))
    paths = [card]
    if key_name and record.get("key") is not None:
        p = sessions_dir / key_name
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # nunca legível por outros
        with os.fdopen(fd, "w") as fh:
            fh.write(record["key"])
        paths.append(p)
    return paths


def remove_mirror(sessions_dir: Path, pid: int, sock: Optional[str] = None) -> bool:
    """Apaga o espelho de `pid` (cartão, chave e, se dado, o socket encaminhado). Não toca num cartão
    de sessão de verdade com esse pid. Devolve se apagou."""
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


# ---------------------------------------------------------------- arquivos de vínculo e recursos
# Um recurso é uma string: "card:<pasta sessions>/<pid>" (espelho local), "sock:<caminho>" (socket
# encaminhado aqui) ou "remote:<máquina>:<pasta sessions de lá>/<pid>" (espelho e socket na outra máquina).
def links_dir(label: str) -> Path:
    return config.state_dir(label) / "links"


def link_file(local_label: str, remote_label: str) -> Path:
    return links_dir(local_label) / (config.safe_label(remote_label) + ".json")


def same_host(a: str, b: str) -> bool:
    return config.host_of(a) == config.host_of(b)


def link_alive(st: Dict[str, Any], root: Path) -> bool:
    """Um vínculo por ssh vive enquanto o processo dele vive; um local, enquanto os dois agentes vivem
    com as MESMAS sessões de quando foi feito (agente reaberto = vínculo morto)."""
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
    """Recursos de que os vínculos vivos desta máquina (menos `except_file`) dependem."""
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
    """Apaga os recursos que mais ninguém usa. `remote_unmirror(host, sessions_dir, pid)` cuida dos de
    outra máquina. Devolve os que ficaram por estarem em uso."""
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
    """Vínculos desta máquina (um arquivo por par), com `up` conferido agora. Um vínculo local que
    morreu (agente fechado ou reaberto) tem os espelhos recolhidos aqui mesmo, já que não há processo
    dele para fazê-lo."""
    root = root or config.state_root()
    out = []
    for f, st in read_links(root):
        alive = link_alive(st, root)
        if not alive and st.get("kind") == "local" and st.get("resources"):
            kept = release(st["resources"], root, f)
            st = StateFile(f).write(up=False, why=st.get("why") or "agente fechou", resources=kept)
        st["up"] = alive
        out.append(st)
    return out


# ---------------------------------------------------------------- mesma máquina
def local_link(local_label: str, remote_label: str, conf: Optional[config.Config] = None) -> Dict[str, Any]:
    """Vínculo entre dois agentes desta máquina, sem túnel: contas diferentes ganham o cartão e a chave
    um do outro (mesmo socket). Devolve o estado a gravar; os espelhos entram em `resources`."""
    conf = conf or config.load()
    dirs, entries = {}, {}
    for lab in (local_label, remote_label):
        dirs[lab] = agent_sessions_dir(lab, conf)
        entries[lab] = wait_entry(config.session_name(lab), [dirs[lab]])
        if entries[lab] is None:
            raise RuntimeError(f"{lab} não está rodando com nome {config.session_name(lab)}")
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


# ---------------------------------------------------------------- entre máquinas
def tunnel_command(
    host: str, forwards: List[Tuple[str, str, str]], ssh: Optional[List[str]] = None
) -> List[str]:
    """`ssh -N` com os encaminhamentos pedidos: (flag -L/-R, socket de escuta, socket de destino)."""
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
    """Um vínculo por ssh em execução (o que `orq-agent link-run` roda em primeiro plano).

    `run_remote(args, input)` executa `orq-agent <args>` na outra máquina e devolve o stdout (os testes
    trocam por uma função); `popen` abre o túnel; `root` é a raiz do estado (~/.cache/orq).
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
        self.forwards: List[str] = []  # flags do túnel de agora: "-L", "-R"
        self.state = StateFile(link_file(local_label, remote_label))
        self.tunnel_log = self.state.path.with_suffix(".log")
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
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, input=input)
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"ssh {self.host}: {e}") from None
        if r.returncode:
            raise RuntimeError((r.stderr.strip() or r.stdout.strip() or "falhou")[-300:])
        return r.stdout

    def _peer(self, *extra: str, input: Optional[str] = None) -> Dict[str, Any]:
        """`orq-agent peer REMOTO …` lá; o JSON da última linha. RuntimeError se vier `err`."""
        out = self.run_remote(["peer", self.remote_label, *extra], input=input)
        try:
            data = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError(f"resposta inválida de {self.host}: {out[-200:]}") from None
        if data.get("err"):
            raise RuntimeError(f"{self.host}: {data['err']}")
        return data

    # ---- caminhos
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

    # ---- túnel
    def _forward(self, flag: str) -> Tuple[str, str, str]:
        """O encaminhamento de cada lado: -L traz o remoto até aqui, -R leva o local até lá."""
        if flag == "-L":
            return ("-L", self.local_mirror_sock(), self.remote["sock"])
        return ("-R", self.remote_mirror_sock(), self.local.sock)

    def _needed_forwards(self, remote_alive: bool) -> List[str]:
        """Quais encaminhamentos faltam agora: os que nenhum túnel (meu ou de outro vínculo) mantém."""
        fw = []
        if "-L" in self.forwards or not socket_alive(Path(self.local_mirror_sock())):
            fw.append("-L")
        if "-R" in self.forwards or not remote_alive:
            fw.append("-R")
        return fw

    def _start_tunnel(self, flags: List[str]) -> None:
        """Abre (ou troca) o túnel com esses encaminhamentos; o stderr vai para o log do vínculo."""
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
                raise RuntimeError(f"o túnel ssh para {self.host} saiu: {self._tunnel_tail()}")
            if not wants_local or socket_alive(Path(self.local_mirror_sock())):
                return
            if time.time() > deadline:
                self._kill_tunnel()
                raise RuntimeError(f"o túnel ssh não abriu {self.local_mirror_sock()} em {TUNNEL_WAIT:.0f} s")
            time.sleep(0.2)

    def _tunnel_tail(self) -> str:
        try:
            lines = self.tunnel_log.read_text(errors="ignore").strip().splitlines()
        except OSError:
            lines = []
        return (lines or ["sem mensagem"])[-1][-300:]

    def _kill_tunnel(self) -> None:
        if self.tunnel is not None and self.tunnel.poll() is None:
            self.tunnel.terminate()
            try:
                self.tunnel.wait(5)
            except Exception:
                self.tunnel.kill()
        self.tunnel, self.forwards = None, []

    # ---- subir
    def up(self) -> None:
        name = config.session_name(self.local_label)
        self.local = wait_entry(name, [agent_sessions_dir(self.local_label, self.conf)])
        if self.local is None:
            raise RuntimeError(
                f"{self.local_label} não está rodando com nome {name} (abra-o pela página antes)"
            )
        if not self.local.key_path:
            raise RuntimeError(f"{self.local_label} não tem arquivo-chave em {self.local.path.parent}")
        # cartão do remoto e se o espelho meu já está de pé lá (outro vínculo meu com aquela máquina)
        self.remote = self._peer("--probe", str(self.local.pid))
        if not self.remote.get("pid"):
            raise RuntimeError(f"{self.host}: cartão sem pid")
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
        self.log(f"vínculo {self.local_label} ⇄ {self.remote_label}: {name} e {rname} se enxergam")

    # ---- ficar de pé
    def wait(self, poll: float = POLL, remote_poll: float = REMOTE_POLL) -> str:
        """Fica até o unlink (SIGTERM), um dos agentes fechar ou o túnel cair. Refaz um encaminhamento
        que outro vínculo mantinha e caiu. Devolve o motivo da saída."""
        assert self.local and self.remote
        last_remote = time.time()
        while not self._stop:
            if self.tunnel is not None and self.tunnel.poll() is not None:
                return "túnel caiu: " + self._tunnel_tail()
            if not self.local.path.exists() or not pid_alive(self.local.pid):
                return f"{self.local_label} fechou"
            remote_alive = True
            if time.time() - last_remote >= remote_poll:
                last_remote = time.time()
                try:
                    probe = self._peer("--probe", str(self.local.pid))
                except RuntimeError as e:
                    return f"{self.remote_label} fechou ({e})"
                if int(probe.get("pid") or 0) != int(self.remote["pid"]):
                    return f"{self.remote_label} foi reaberto"
                remote_alive = bool(probe.get("probe_alive"))
            needed = self._needed_forwards(remote_alive)
            if any(f not in self.forwards for f in needed):
                # quem mantinha esse encaminhamento saiu: passa a ser meu (o túnel é trocado inteiro,
                # com o que já era meu mais o que faltou)
                try:
                    self._start_tunnel(needed)
                    self.state.write(
                        forwards=self.forwards, tunnel_pid=self.tunnel.pid if self.tunnel else None
                    )
                    self.log(f"vínculo {self.local_label} ⇄ {self.remote_label}: refiz {self.forwards}")
                except RuntimeError as e:
                    return f"não consegui refazer o túnel: {e}"
            time.sleep(poll)
        return "unlink"

    def stop(self, *_: Any) -> None:
        self._stop = True

    def down(self, why: str = "") -> None:
        """Marca-se como fora primeiro (para os outros vínculos não contarem comigo), solta o que é só
        meu e fecha o túnel."""
        cur = self.state.read()
        self.state.write(up=False, why=why)
        kept: List[str] = []
        if self.local and self.remote:

            def remote_unmirror(host: str, sessions_dir: str, pid: int) -> None:
                try:
                    self._peer("--unmirror", str(pid), "--sock", self.remote_mirror_sock())
                except RuntimeError as e:  # a outra máquina pode ter caído; o resto da limpeza segue
                    self.log(f"aviso: não desfiz o espelho em {host}: {e}")

            kept = release(
                cur.get("resources") or self.resources(), self.root, self.state.path, remote_unmirror
            )
        self._kill_tunnel()
        self.state.write(resources=kept)
        self.log(f"vínculo {self.local_label} ⇄ {self.remote_label} desfeito ({why})")


def run_link(local_label: str, remote_label: str, **kw: Any) -> int:
    """`orq-agent link-run`: sobe, segura e desfaz. 0 se subiu; 1 se nem subiu. Qualquer erro, inclusive
    inesperado, passa por `down`: um túnel órfão seria pior que a mensagem."""
    link = Link(local_label, remote_label, **kw)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, link.stop)
    try:
        link.up()
    except Exception as e:
        link.log(f"vínculo falhou: {e}")
        link.down("falhou: " + str(e)[:200])
        return 1
    try:
        why = link.wait()
    except Exception as e:
        why = f"erro: {e}"
    link.down(why)
    return 0
