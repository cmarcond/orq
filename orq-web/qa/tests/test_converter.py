"""⇄ converter: um `claude` interativo rodando num tmux desta máquina vira agente da página, na mesma conversa.
O teste sobe um claude de verdade num tmux na pasta do QA (como o alias `claude-<conta>` faz), conversa com ele
pelo tmux (guarda uma palavra-chave), espera a página listá-lo como aba tracejada, converte pelo botão do painel
e pergunta a palavra-chave ao agente novo: ele tem de lembrar. Dois turnos de cota, na conta escolhida
(ORQ_QA_ACCOUNT; senão a primeira conta que não é a padrão, para o CLAUDE_CONFIG_DIR dizer a conta)."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from conftest import PROJECT
from orqqa import local_host_name, qa_name
from orqweb import config

SECRET = "carambola-528"


def tmux(*args: str, check: bool = False) -> "subprocess.CompletedProcess[str]":
    env = dict(os.environ)
    env.pop("TMUX", None)  # o servidor tmux padrão, mesmo rodando dentro de outro tmux
    return subprocess.run(["tmux", *args], capture_output=True, text=True, env=env, check=check)


def has_session(sess: str) -> bool:
    return tmux("has-session", "-t", "=" + sess).returncode == 0


def pane(sess: str, fmt: str) -> str:
    r = tmux("display-message", "-p", "-t", sess, fmt)
    return r.stdout.strip() if r.returncode == 0 else ""


def wait_idle(sess: str, timeout: float = 120) -> None:
    """Espera o claude do tmux parado (título do painel começa com ✳), aceitando o diálogo de confiança da pasta."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not has_session(sess):
            raise RuntimeError(f"a sessão tmux {sess} acabou")
        title = pane(sess, "#{pane_title}")
        if title.startswith("✳"):
            return
        text = tmux("capture-pane", "-p", "-t", sess).stdout
        if re.search(r"trust this folder|confia", text, re.I):  # vem com "No, exit" selecionado: desce para o "Yes"
            tmux("send-keys", "-t", sess, "Down")
            time.sleep(0.3)
            tmux("send-keys", "-t", sess, "Enter")
            time.sleep(2)
        time.sleep(1)
    raise RuntimeError(f"o claude em {sess} não ficou pronto: título {pane(sess, '#{pane_title}')!r}")


def type_in(sess: str, text: str) -> None:
    tmux("send-keys", "-t", sess, "-l", text)
    time.sleep(0.5)
    tmux("send-keys", "-t", sess, "Enter")


def last_assistant(config_dir: Path, folder: Path) -> str:
    """Último texto do claude na conversa mais recente da pasta, pelo transcript que o Claude Code grava."""
    base = config_dir / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(folder))
    files = sorted(base.glob("*.jsonl"), key=lambda f: f.stat().st_mtime) if base.exists() else []
    if not files:
        return ""
    out = ""
    for line in files[-1].read_text(errors="ignore").splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") == "assistant":
            for c in (o.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text":
                    out = c["text"]
    return out


def wait_reply(config_dir: Path, folder: Path, contains: str, timeout: float = 180) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        txt = last_assistant(config_dir, folder)
        if contains in txt.lower():
            return txt
        time.sleep(2)
    raise RuntimeError(f"o claude do tmux não respondeu {contains!r}: {last_assistant(config_dir, folder)!r}")


def pick_account():
    conf = config.load()
    name = os.environ.get("ORQ_QA_ACCOUNT")
    if name:
        return conf.account(name)
    for a in conf.accounts.values():
        if not a.is_default:
            return a
    return next(iter(conf.accounts.values()))


def test_converte_claude_do_tmux(orq):
    host, _, path = PROJECT.partition(":")
    if host != local_host_name():
        pytest.skip(f"o claude interativo sobe nesta máquina ({local_host_name()}), e ORQ_QA_PROJECT está em {host}")
    folder = Path(os.path.expanduser(path))
    name = qa_name(path)
    label = f"{name}@{host}"
    acc = pick_account()
    sess = f"claude-{acc.name}-{name}"  # o nome que o alias claude-<conta> dá à sessão
    orq.purge(PROJECT)
    if has_session(sess):
        tmux("kill-session", "-t", "=" + sess)
    folder.mkdir(parents=True)
    try:
        _run(orq, folder, label, acc, sess)
    finally:
        if has_session(sess):
            tmux("kill-session", "-t", "=" + sess)
        orq.purge(PROJECT)


def _run(orq, folder: Path, label: str, acc, sess: str):
    # um claude interativo de verdade, no tmux, como o alias faz (CLAUDE_CONFIG_DIR diz a conta)
    tmux(
        "new-session", "-d", "-s", sess, "-c", str(folder), "-x", "140", "-y", "40",
        f"env CLAUDE_CONFIG_DIR={acc.config_dir} claude",
        check=True,
    )
    wait_idle(sess)
    type_in(sess, f"Guarde a palavra-chave {SECRET}. Responda apenas: guardado.")
    wait_reply(acc.config_dir, folder, "guardado")
    wait_idle(sess)
    # a página lista o claude do terminal como aba tracejada (consulta às máquinas a cada 15 s)
    orq.page.wait_for_selector(f'.tab.term[data-l="{label}"]', timeout=90000)
    orq.page.click(f'.tab.term[data-l="{label}"]')
    orq.page.wait_for_selector(".chat.on .perm.term")
    panel = orq.page.inner_text(".chat.on .perm.term")
    assert "Sessão do claude no terminal" in panel and sess in panel and str(folder) in panel, panel
    assert orq.page.is_hidden("#composer"), "não se fala com o terminal pela página"
    orq.shot("converter-terminal")
    orq.page.wait_for_timeout(2000)  # pausas: o vídeo é para uma pessoa conferir
    # ⇄ converter: /exit no tmux e a mesma conversa reaberta como agente da página
    orq.page.click(".chat.on .perm.term button.ok")
    orq.wait_fn("() => document.getElementById('toast').textContent.includes('convertido')", timeout=120000)
    toast = orq.toast()
    assert "fechado por exit" in toast, toast
    orq.page.wait_for_selector(f'.tab[data-l="{label}"]:not(.term)', timeout=60000)
    orq.wait_fn(
        "(l) => document.querySelector('.tab.on')?.dataset.l === l"
        " && document.getElementById('cstate').textContent.includes('esperando')",
        arg=label,
        timeout=90000,
    )
    assert not has_session(sess), "o tmux do claude interativo foi embora"
    assert orq.account_of(label) == acc.name, orq.agents()
    orq.shot("converter-convertido")
    orq.page.wait_for_timeout(1500)
    # o agente novo lembra o que o claude do terminal sabia
    reply = orq.say("Qual é a palavra-chave que pedi para guardar? Responda só com ela.")
    assert SECRET in reply, reply
    orq.shot("converter-lembra")
    orq.page.wait_for_timeout(1500)
    assert not orq.problems(), orq.problems()
