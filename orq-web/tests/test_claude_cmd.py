from orqweb import config
from orqweb.agent import claude


def test_build_command_basic():
    cmd = claude.build_command("/x/claude", "auto")
    assert cmd[:2] == ["/x/claude", "-p"] and "--permission-mode" in cmd and cmd[-1] == "auto"
    assert "-n" not in cmd and "--append-system-prompt" not in cmd


def test_build_command_with_resume_name_and_role():
    cmd = claude.build_command("/x/claude", "auto", "sid-1", name="kaggle-server0", system_prompt="Você é…")
    assert cmd[cmd.index("--resume") + 1] == "sid-1"
    assert cmd[cmd.index("-n") + 1] == "kaggle-server0"
    assert cmd[cmd.index("--append-system-prompt") + 1] == "Você é…"


def test_session_name_has_no_at():
    assert config.session_name("kaggle@server0") == "kaggle-server0"
    assert config.session_name("kaggle~professor@server0") == "kaggle-professor-server0"
    assert "@" not in config.session_name("a@b@c")


def test_build_command_permission_mode_is_positional_arg():
    cmd = claude.build_command("/x/claude", "acceptEdits")
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
