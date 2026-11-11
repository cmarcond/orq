"""⇄ convert: an interactive `claude` running in a tmux on this machine becomes an agent of the page, in the
same conversation. The test starts a real claude in a tmux in the QA folder (as the alias `claude-<account>`
does), talks to it through tmux (it keeps a keyword), waits for the page to list it as a dashed tab,
converts through the panel's button and asks the new agent for the keyword: it has to remember. Two turns of
quota, on the chosen account (ORQ_QA_ACCOUNT; otherwise the first account that is not the default, so that
CLAUDE_CONFIG_DIR says the account)."""

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
    env.pop("TMUX", None)  # the default tmux server, even when running inside another tmux
    return subprocess.run(["tmux", *args], capture_output=True, text=True, env=env, check=check)


def has_session(sess: str) -> bool:
    return tmux("has-session", "-t", "=" + sess).returncode == 0


def pane(sess: str, fmt: str) -> str:
    r = tmux("display-message", "-p", "-t", sess, fmt)
    return r.stdout.strip() if r.returncode == 0 else ""


def wait_idle(sess: str, timeout: float = 120) -> None:
    """Waits for the tmux claude to be idle (pane title starts with ✳), accepting the folder trust
    dialog."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not has_session(sess):
            raise RuntimeError(f"the tmux session {sess} ended")
        title = pane(sess, "#{pane_title}")
        if title.startswith("✳"):
            return
        text = tmux("capture-pane", "-p", "-t", sess).stdout
        # the dialog comes with "No, exit" selected: goes down to "Yes"
        if re.search(r"trust this folder|confia", text, re.I):
            tmux("send-keys", "-t", sess, "Down")
            time.sleep(0.3)
            tmux("send-keys", "-t", sess, "Enter")
            time.sleep(2)
        time.sleep(1)
    raise RuntimeError(f"the claude in {sess} did not get ready: title {pane(sess, '#{pane_title}')!r}")


def type_in(sess: str, text: str) -> None:
    tmux("send-keys", "-t", sess, "-l", text)
    time.sleep(0.5)
    tmux("send-keys", "-t", sess, "Enter")


def last_assistant(config_dir: Path, folder: Path) -> str:
    """Last claude text in the most recent conversation of the folder, from the transcript that Claude Code
    writes."""
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
    raise RuntimeError(f"the tmux claude did not answer {contains!r}: {last_assistant(config_dir, folder)!r}")


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
        pytest.skip(
            f"the interactive claude starts on this machine ({local_host_name()}), and ORQ_QA_PROJECT is on "
            f"{host}"
        )
    folder = Path(os.path.expanduser(path))
    name = qa_name(path)
    label = f"{name}@{host}"
    acc = pick_account()
    sess = f"claude-{acc.name}-{name}"  # the name the alias claude-<account> gives the session
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
    # a real interactive claude, in tmux, as the alias does (CLAUDE_CONFIG_DIR says the account)
    tmux(
        "new-session",
        "-d",
        "-s",
        sess,
        "-c",
        str(folder),
        "-x",
        "140",
        "-y",
        "40",
        f"env CLAUDE_CONFIG_DIR={acc.config_dir} claude",
        check=True,
    )
    wait_idle(sess)
    type_in(sess, f"Keep the keyword {SECRET}. Answer only: stored.")
    wait_reply(acc.config_dir, folder, "stored")
    wait_idle(sess)
    # the page lists the terminal claude as a dashed tab (query to the machines every 15 s)
    orq.page.wait_for_selector(f'.tab.term[data-l="{label}"]', timeout=90000)
    orq.page.click(f'.tab.term[data-l="{label}"]')
    orq.page.wait_for_selector(".chat.on .perm.term")
    panel = orq.page.inner_text(".chat.on .perm.term")
    assert "Claude session in the terminal" in panel and sess in panel and str(folder) in panel, panel
    assert orq.page.is_hidden("#composer"), "one does not talk to the terminal through the page"
    orq.shot("converter-terminal")
    orq.page.wait_for_timeout(2000)  # pauses: the video is for a person to check
    # ⇄ convert: /exit in tmux and the same conversation reopened as an agent of the page
    orq.page.click(".chat.on .perm.term button.ok")
    orq.wait_fn("() => document.getElementById('toast').textContent.includes('converted')", timeout=120000)
    toast = orq.toast()
    assert "closed by exit" in toast, toast
    orq.page.wait_for_selector(f'.tab[data-l="{label}"]:not(.term)', timeout=60000)
    orq.wait_fn(
        "(l) => document.querySelector('.tab.on')?.dataset.l === l"
        " && document.getElementById('cstate').textContent.includes('waiting')",
        arg=label,
        timeout=90000,
    )
    assert not has_session(sess), "the tmux of the interactive claude is gone"
    assert orq.account_of(label) == acc.name, orq.agents()
    orq.shot("converter-convertido")
    orq.page.wait_for_timeout(1500)
    # the new agent remembers what the terminal claude knew
    reply = orq.say("What is the keyword I asked you to keep? Answer only with it.")
    assert SECRET in reply, reply
    orq.shot("converter-lembra")
    orq.page.wait_for_timeout(1500)
    assert not orq.problems(), orq.problems()
