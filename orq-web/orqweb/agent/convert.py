"""Converte um `claude` interativo rodando num tmux desta máquina num agente da página.

A conversa já está em disco (o Claude Code grava cada mensagem), então converter é: fechar o
programa interativo sem violência, e subir o supervisor na mesma pasta e conta com `--mode resume`.
A sequência de fechamento é escalonada: /exit, Ctrl-C duplo, SIGTERM. Nunca SIGKILL.
"""

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .. import config

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _run(cmd: List[str], **kw) -> "subprocess.CompletedProcess[str]":
    env = dict(os.environ)
    env.pop("TMUX", None)  # fala com o servidor tmux padrão, mesmo de dentro de outro tmux
    return subprocess.run(cmd, capture_output=True, text=True, env=env, **kw)


class Tmux:
    """O pouco de tmux que a conversão precisa, por um runner trocável nos testes."""

    def __init__(self, run: Runner = _run, binary: str = "tmux"):
        self.run, self.bin = run, binary

    def has_session(self, sess: str) -> bool:
        return self.run([self.bin, "has-session", "-t", "=" + sess]).returncode == 0

    def display(self, sess: str, fmt: str) -> str:
        r = self.run([self.bin, "display-message", "-p", "-t", sess, fmt])
        return r.stdout.strip() if r.returncode == 0 else ""

    def pane_command(self, sess: str) -> str:
        return self.display(sess, "#{pane_current_command}")

    def pane_pid(self, sess: str) -> Optional[int]:
        s = self.display(sess, "#{pane_pid}")
        return int(s) if s.isdigit() else None

    def send(self, sess: str, *keys: str, literal: bool = False) -> None:
        self.run([self.bin, "send-keys", "-t", sess] + (["-l"] if literal else []) + list(keys))

    def kill_session(self, sess: str) -> None:
        self.run([self.bin, "kill-session", "-t", "=" + sess])


VERSION_NAME = re.compile(r"^\d+(\.\d+)+$")


def is_claude(name: str) -> bool:
    """O processo do Claude Code se chama `claude` (Linux) ou pelo número da versão, nome do binário
    real sob ~/.local/share/claude/versions (macOS)."""
    base = os.path.basename(name or "")
    return base.startswith("claude") or bool(VERSION_NAME.match(base))


def claude_pids_under(pane_pid: int, run: Runner = _run) -> List[int]:
    """PIDs de processos `claude` descendentes do painel (o Claude Code pode estar sob um shell)."""
    r = run(["ps", "-eo", "pid=,ppid=,comm="])
    kids: Dict[int, List[int]] = {}
    names: Dict[int, str] = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, comm = int(parts[0]), int(parts[1]), parts[2]
        kids.setdefault(ppid, []).append(pid)
        names[pid] = comm
    out, stack = [], [pane_pid]
    while stack:
        p = stack.pop()
        if is_claude(names.get(p, "")):
            out.append(p)
        stack += kids.get(p, [])
    return out


def claude_gone(tmux: Tmux, sess: str) -> bool:
    """O claude interativo saiu: a sessão acabou, ou o painel voltou a um shell."""
    return not tmux.has_session(sess) or not is_claude(tmux.pane_command(sess))


def close_claude(
    tmux: Tmux,
    sess: str,
    wait: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    kill: Callable[[int, int], None] = os.kill,
    run: Runner = _run,
) -> str:
    """Fecha o claude do tmux `sess` em escalada. Devolve como: 'exit', 'ctrl-c', 'sigterm' ou 'failed'."""
    if claude_gone(tmux, sess):
        return "exit"

    def settle(how: str) -> Optional[str]:
        for _ in range(int(wait * 2)):
            if claude_gone(tmux, sess):
                return how
            sleep(0.5)
        return None

    tmux.send(sess, "Escape")
    sleep(0.3)
    tmux.send(sess, "/exit", literal=True)
    sleep(0.3)
    tmux.send(sess, "Enter")
    how = settle("exit")
    if how:
        return how
    tmux.send(sess, "C-c")
    sleep(0.4)
    tmux.send(sess, "C-c")
    how = settle("ctrl-c")
    if how:
        return how
    pane = tmux.pane_pid(sess)
    for pid in claude_pids_under(pane, run) if pane else []:
        try:
            kill(pid, signal.SIGTERM)
        except OSError:
            pass
    return settle("sigterm") or "failed"


def terminal_sessions(status: List[dict]) -> List[dict]:
    """Entradas do `orq --status-json` que são claude interativo em tmux (não agentes da página)."""
    return [a for a in status if a.get("kind") != "sdk" and a.get("tmux") and a.get("state") != "closed"]


def others_in_folder(status: List[dict], entry: dict) -> List[dict]:
    """Outros claude interativos vivos na mesma pasta: tornam ambígua a conversa a retomar."""
    d = os.path.realpath(entry.get("dir") or "")
    return [
        a
        for a in terminal_sessions(status)
        if a is not entry and a.get("tmux") != entry.get("tmux") and os.path.realpath(a.get("dir") or "") == d
    ]


def live_terminal_in_folder(status: List[dict], folder: str) -> List[dict]:
    """Claude interativo vivo nesta pasta (qualquer tmux): retomar por cima dele é perigoso."""
    d = os.path.realpath(folder)
    return [a for a in terminal_sessions(status) if os.path.realpath(a.get("dir") or "") == d]


def account_for(dir_: str, account: Optional[str], bin_dir: Path, run: Runner = _run) -> Optional[str]:
    """Conta da sessão: a do processo, ou a da conversa mais recente da pasta (claude-last --projects)."""
    if account and account != "?":
        return account
    try:
        r = run([str(bin_dir / "claude-last"), "--projects"])
        for p in json.loads(r.stdout):
            if os.path.realpath(p.get("cwd", "")) == os.path.realpath(dir_):
                return p.get("account")
    except (OSError, ValueError):
        pass
    return None


def convert(
    entry: dict,
    host: str,
    spawn: Callable[[str, str, str, str], str],
    tmux: Optional[Tmux] = None,
    bin_dir: Optional[Path] = None,
    others: Optional[List[dict]] = None,
    **close_kw,
) -> dict:
    """Converte uma entrada de terminal_sessions(). Devolve {ok, label, how, msg}.

    `others`: outros claude interativos vivos na mesma pasta. Com eles, a conversa mais recente da
    pasta pode ser a de outro processo ainda aberto, então a conversão é recusada sem fechar nada.
    """
    tmux = tmux or Tmux()
    bin_dir = bin_dir or config.bin_dir()
    sess, dir_ = entry["tmux"], entry.get("dir") or ""
    base = os.path.basename(dir_.rstrip("/")) or "home"
    label = f"{base}@{host}"
    if others:
        names = ", ".join(a.get("tmux", "?") for a in others)
        return {
            "ok": False,
            "label": label,
            "how": "",
            "msg": f"há outro claude aberto nesta pasta (tmux {names}); feche-o ou converta-o antes, "
            "senão não dá para saber qual conversa é de qual",
        }
    acc = account_for(dir_, entry.get("account"), bin_dir)
    if not acc:
        return {
            "ok": False,
            "label": label,
            "how": "",
            "msg": f"não descobri a conta da sessão {sess}; passe --account",
        }
    how = close_claude(tmux, sess, **close_kw)
    if how == "failed":
        return {
            "ok": False,
            "label": label,
            "how": how,
            "msg": f"o claude em {sess} não fechou; nada foi aberto",
        }
    if tmux.has_session(sess):
        tmux.kill_session(sess)  # sobrou o shell do alias; a sessão nova é orqs-<pasta>
    try:
        msg = spawn(label, dir_, acc, "resume")
    except RuntimeError as e:
        return {"ok": False, "label": label, "how": how, "msg": str(e)}
    return {"ok": True, "label": label, "how": how, "msg": f"{msg}; conta {acc}, pasta {dir_}"}
