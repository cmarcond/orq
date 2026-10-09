"""Conversão de um claude interativo em tmux para agente da página, com um tmux simulado."""

import json
import subprocess
from pathlib import Path

from orqweb.agent import convert as C


class FakeTmux(C.Tmux):
    """Simula um painel: `script` diz como o claude reage a cada tecla; o estado muda quando 'fecha'."""

    def __init__(self, exits_on=("Enter",), has_shell_after=False):
        super().__init__(run=lambda *a, **k: None)
        self.exits_on = exits_on
        self.has_shell_after = has_shell_after
        self.alive = True  # claude rodando no painel
        self.session = True
        self.sent = []
        self.killed = False

    def has_session(self, sess):
        return self.session

    def pane_command(self, sess):
        return "claude" if self.alive else "bash"

    def pane_pid(self, sess):
        return 4242

    def send(self, sess, *keys, literal=False):
        self.sent += list(keys)
        if any(k in self.exits_on for k in keys):
            self.alive = False
            if not self.has_shell_after:
                self.session = False

    def kill_session(self, sess):
        self.killed = True
        self.session = False


def nosleep(_):
    pass


def test_close_with_exit():
    t = FakeTmux(exits_on=("Enter",))
    assert C.close_claude(t, "s", sleep=nosleep) == "exit"
    assert t.sent[:3] == ["Escape", "/exit", "Enter"]


def test_close_falls_back_to_ctrl_c():
    t = FakeTmux(exits_on=("C-c",))
    assert C.close_claude(t, "s", wait=1, sleep=nosleep) == "ctrl-c"
    assert t.sent.count("C-c") == 2


def test_close_falls_back_to_sigterm_using_claude_pids():
    t = FakeTmux(exits_on=())
    killed = []
    ps = subprocess.CompletedProcess([], 0, "4242 1 bash\n5000 4242 claude\n6000 5000 node\n7 1 other\n", "")

    def kill(pid, sig):
        killed.append(pid)
        t.alive = False
        t.session = False

    how = C.close_claude(t, "s", wait=1, sleep=nosleep, kill=kill, run=lambda *a, **k: ps)
    assert how == "sigterm" and killed == [5000]


def test_close_reports_failure():
    t = FakeTmux(exits_on=())
    ps = subprocess.CompletedProcess([], 0, "4242 1 bash\n", "")
    assert (
        C.close_claude(t, "s", wait=0.5, sleep=nosleep, kill=lambda *a: None, run=lambda *a, **k: ps)
        == "failed"
    )


def test_already_gone_is_exit():
    t = FakeTmux()
    t.alive = False
    assert C.close_claude(t, "s", sleep=nosleep) == "exit"
    assert t.sent == []


def test_is_claude_recognizes_linux_and_macos_names():
    assert C.is_claude("claude") and C.is_claude("/home/c/.local/bin/claude")
    assert C.is_claude("2.1.296") and C.is_claude("/Users/c/.local/share/claude/versions/2.1.296")
    assert not C.is_claude("bash") and not C.is_claude("python3") and not C.is_claude("")


def test_close_recognizes_macos_binary_name():
    t = FakeTmux(exits_on=("Enter",))
    t.pane_command = lambda sess: "2.1.296" if t.alive else "zsh"
    assert C.close_claude(t, "s", sleep=nosleep) == "exit"
    assert "Enter" in t.sent, "não pode achar que já saiu só porque o nome não é 'claude'"


def test_terminal_sessions_filter():
    status = [
        {"label": "a", "tmux": "claude-gmail-a", "state": "waiting"},
        {"label": "b@h", "kind": "sdk", "state": "waiting"},
        {"label": "c", "tmux": "x", "state": "closed"},
        {"label": "d", "state": "menu"},  # sem tmux: painel da grade, fora
    ]
    assert [s["label"] for s in C.terminal_sessions(status)] == ["a"]


def test_account_for_prefers_known_then_projects(tmp_path):
    projects = json.dumps([{"cwd": str(tmp_path / "p"), "account": "gmail"}])
    run = lambda *a, **k: subprocess.CompletedProcess([], 0, projects, "")  # noqa: E731
    assert C.account_for(str(tmp_path / "p"), "work", Path("/b"), run) == "work"
    assert C.account_for(str(tmp_path / "p"), "?", Path("/b"), run) == "gmail"
    assert C.account_for(str(tmp_path / "outra"), None, Path("/b"), run) is None


def test_convert_happy_path_kills_leftover_shell_and_spawns():
    t = FakeTmux(exits_on=("Enter",), has_shell_after=True)
    calls = []

    def spawn(label, folder, acc, mode):
        calls.append((label, folder, acc, mode))
        return "aberto na sessão tmux orqs-kaggle"

    entry = {
        "label": "kaggle",
        "tmux": "claude-gmail-kaggle",
        "dir": "/home/c/kaggle",
        "account": "gmail",
        "state": "waiting",
    }
    res = C.convert(entry, "server0", spawn, tmux=t, bin_dir=Path("/b"), sleep=nosleep)
    assert res["ok"] and res["label"] == "kaggle@server0" and res["how"] == "exit"
    assert t.killed, "o shell que sobrou no tmux antigo é fechado"
    assert calls == [("kaggle@server0", "/home/c/kaggle", "gmail", "resume")]
    assert "conta gmail" in res["msg"]


def test_convert_refuses_without_account_and_on_failed_close():
    t = FakeTmux(exits_on=("Enter",))
    entry = {"label": "x", "tmux": "s", "dir": "/nada", "account": "?"}
    res = C.convert(entry, "h", lambda *a: "", tmux=t, bin_dir=Path("/b"), sleep=nosleep)
    assert not res["ok"] and "conta" in res["msg"] and t.sent == [], "não fecha nada se não sabe a conta"

    t2 = FakeTmux(exits_on=())
    ps = subprocess.CompletedProcess([], 0, "", "")
    entry2 = dict(entry, account="gmail")
    res = C.convert(
        entry2,
        "h",
        lambda *a: "",
        tmux=t2,
        bin_dir=Path("/b"),
        sleep=nosleep,
        wait=0.5,
        kill=lambda *a: None,
        run=lambda *a, **k: ps,
    )
    assert not res["ok"] and res["how"] == "failed"


def test_convert_reports_spawn_error():
    t = FakeTmux(exits_on=("Enter",))

    def spawn(*a):
        raise RuntimeError("socket não apareceu")

    entry = {"label": "x", "tmux": "s", "dir": "/p", "account": "gmail"}
    res = C.convert(entry, "h", spawn, tmux=t, bin_dir=Path("/b"), sleep=nosleep)
    assert not res["ok"] and "socket" in res["msg"] and res["how"] == "exit"
