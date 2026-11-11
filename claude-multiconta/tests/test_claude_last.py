"""claude-last --projects and --pick in a temporary HOME: conversations from the page (claude -p) count,
levels (professor) do not, scripts/cron stay hidden."""

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
    d = cfg / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", cwd)  # how Claude Code encodes the folder
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    first = {"type": "user", "cwd": cwd, "entrypoint": entrypoint, "message": {"role": "user", "content": prompt}}
    if peer:  # task that arrived from another session: Claude Code writes isMeta and the text in origin.body
        first.update(isMeta=True, origin={"kind": "peer", "name": "prof", "body": prompt})
        first["message"]["content"] = "Another Claude session sent a message:\n<cross-session-message>" + prompt
    lines = [
        first,
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
    ]
    f.write_text("\n".join(json.dumps(x, separators=(",", ":")) for x in lines) + "\n")  # compact, the way Claude Code writes it
    os.utime(f, (mtime, mtime))
    return f


def run(env, *args):
    r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30)
    return r.returncode, r.stdout, r.stderr


@pytest.fixture
def proj():
    """Project folder outside /tmp: claude-last hides /tmp projects on purpose, and in CI pytest's
    tmp_path lives there."""
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
    # 1) conversation of the page's agent (claude -p), recorded in orq's state.json
    session(cfg, str(proj), "sid-base", "sdk-cli", "task from the page", 1000, peer=True)
    # 2) the professor's conversation, newer, in the same folder: it is a level, it must not win
    session(cfg, str(proj), "sid-prof", "sdk-cli", "I am the professor", 2000)
    # 3) claude -p from a cron in another folder: stays hidden
    session(cfg, str(cron), "sid-cron", "sdk-cli", "the cron ran", 3000)
    for sid, extra in (("sid-base", {}), ("sid-prof", {"parent": "proj@laptop", "level": "professor"})):
        d = state / sid
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({"label": "x", "sid": sid, "kind": "sdk", **extra}))
    env = dict(os.environ, HOME=str(home), CLAUDE_MULTICONTA_CONF=str(conf), ORQ_STATE_DIR=str(state))

    code, out, err = run(env, "--projects")
    assert code == 0, err
    items = json.loads(out)
    assert [(p["cwd"], p["sid"]) for p in items] == [(str(proj), "sid-base")]
    assert items[0]["title"] == "task from the page" and items[0]["account"] == "gmail"

    # --pick in the folder: resumes the agent's conversation, not the professor's
    code, out, err = run(env, "--pick", str(proj))
    assert code == 0 and out.split() == ["gmail", "sid-base"], (out, err)

    # with no state.json at all, claude -p is treated as a script again: the list is empty
    env2 = dict(env, ORQ_STATE_DIR=str(tmp_path / "empty"))
    assert json.loads(run(env2, "--projects")[1]) == []
