"""claude-last --projects e --pick num HOME temporário: conversas da página (claude -p) contam,
níveis (professor) não, scripts/cron continuam escondidos."""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = HERE / "bin" / "claude-last"


def session(cfg, cwd, sid, entrypoint, prompt, mtime, peer=False):
    d = cfg / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", cwd)  # como o Claude Code codifica a pasta
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    first = {"type": "user", "cwd": cwd, "entrypoint": entrypoint, "message": {"role": "user", "content": prompt}}
    if peer:  # tarefa que chegou de outra sessão: o Claude Code grava isMeta e o texto em origin.body
        first.update(isMeta=True, origin={"kind": "peer", "name": "prof", "body": prompt})
        first["message"]["content"] = "Another Claude session sent a message:\n<cross-session-message>" + prompt
    lines = [
        first,
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
    ]
    f.write_text("\n".join(json.dumps(x, separators=(",", ":")) for x in lines) + "\n")  # compacto, como o Claude Code grava
    os.utime(f, (mtime, mtime))
    return f


def run(env, *args):
    r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30)
    return r.returncode, r.stdout, r.stderr


@pytest.fixture
def proj():
    """Pasta de projeto fora de /tmp: o claude-last esconde de propósito projetos de /tmp, e no CI o
    tmp_path do pytest fica lá."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="orq-cl-", dir=HERE / "tests"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_projects_include_page_agents_but_not_levels_or_scripts(tmp_path, proj):
    home = tmp_path / "home"
    cfg = home / ".claude-g"
    cron = tmp_path / "cron"
    cron.mkdir()
    conf = tmp_path / "accounts.json"
    conf.write_text(json.dumps({"accounts": [{"name": "gmail", "config_dir": str(cfg)}]}))
    state = tmp_path / "orq"
    # 1) conversa do agente da página (claude -p), registrada no state.json do orq
    session(cfg, str(proj), "sid-base", "sdk-cli", "tarefa da página", 1000, peer=True)
    # 2) conversa do professor, mais nova, na mesma pasta: é nível, não deve ganhar
    session(cfg, str(proj), "sid-prof", "sdk-cli", "sou o professor", 2000)
    # 3) claude -p de um cron noutra pasta: continua escondido
    session(cfg, str(cron), "sid-cron", "sdk-cli", "rodou o cron", 3000)
    for sid, extra in (("sid-base", {}), ("sid-prof", {"parent": "proj@laptop", "level": "professor"})):
        d = state / sid
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({"label": "x", "sid": sid, "kind": "sdk", **extra}))
    env = dict(os.environ, HOME=str(home), CLAUDE_MULTICONTA_CONF=str(conf), ORQ_STATE_DIR=str(state))

    code, out, err = run(env, "--projects")
    assert code == 0, err
    items = json.loads(out)
    assert [(p["cwd"], p["sid"]) for p in items] == [(str(proj), "sid-base")]
    assert items[0]["title"] == "tarefa da página" and items[0]["account"] == "gmail"

    # --pick na pasta: retoma a conversa do agente, não a do professor
    code, out, err = run(env, "--pick", str(proj))
    assert code == 0 and out.split() == ["gmail", "sid-base"], (out, err)

    # sem state.json nenhum, o claude -p volta a ser tratado como script: a lista fica vazia
    env2 = dict(env, ORQ_STATE_DIR=str(tmp_path / "vazio"))
    assert json.loads(run(env2, "--projects")[1]) == []
