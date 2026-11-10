"""Os atalhos gerados pelo install.sh (aliases.sh), num bash de verdade e numa pasta pessoal temporária: o `claude`
puro abre a melhor conta (usage-all --melhor) e o `claude-resume` fica na conta da conversa se ela estiver boa.
usage-all, claude-last e as contas são falsos: só registram como foram chamados."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="precisa de bash")


@pytest.fixture
def home(tmp_path):
    """Instala numa pasta pessoal temporária (sem baixar nada) e devolve (home, stubs)."""
    h = tmp_path / "home"
    h.mkdir()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "claude").write_text("#!/bin/sh\necho 2.9.9 \\(Claude Code\\)\n")  # o install.sh pergunta a versão
    (stubs / "claude").chmod(0o755)
    env = dict(os.environ, HOME=str(h), PATH=f"{stubs}:/usr/bin:/bin")
    env.pop("CLAUDE_CONFIG_DIR", None)
    r = subprocess.run(["sh", str(HERE / "install.sh"), "--no-install"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    return h, stubs


def run(home, stubs, script, best="gmail", pick="work3 sess-1", picked_as=None):
    """Roda `script` num bash com o aliases.sh carregado e os programas falsos; devolve as linhas do log."""
    h = home
    log = h / "log.txt"
    stubs = h / ".local/bin"  # o aliases.sh põe ~/.local/bin na frente do PATH: os falsos ficam lá, por cima
    log.unlink(missing_ok=True)  # cada chamada com o seu registro
    (stubs / "usage-all").write_text(f'#!/bin/sh\necho "usage-all $*" >> "{log}"\n[ -n "{best}" ] && echo {best}\n')
    as_out = picked_as or ""
    (stubs / "claude-last").write_text(
        "#!/bin/sh\n"
        f'echo "claude-last $*" >> "{log}"\n'
        'case "$*" in *--as=*) a=$(echo "$*" | sed "s/.*--as=\\([^ ]*\\).*/\\1/"); echo "$a sess-1";; '
        f'*) echo "{pick}";; esac\n'
    )
    for f in ("usage-all", "claude-last"):
        (stubs / f).chmod(0o755)
    accounts = ["work", "gmail", "work2", "work3"]
    fakes = "\n".join(f'claude-{a}() {{ echo "ABRIU {a} $*" >> "{log}"; }}' for a in accounts)
    env = dict(os.environ, HOME=str(h), PATH=f"{stubs}:/usr/bin:/bin")
    env.pop("CLAUDE_CONTA", None)
    body = f'. "{h}/.config/claude-multiconta/aliases.sh" >/dev/null 2>&1\n{fakes}\n{script}\n'
    r = subprocess.run(["bash", "-c", body], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return log.read_text().splitlines() if log.exists() else [], r.stdout


def test_plain_claude_opens_the_best_account_silently(home):
    lines, out = run(*home, "claude --model opus")
    assert lines == ["usage-all --melhor", "ABRIU gmail --model opus"] and out == ""


def test_plain_claude_respects_claude_conta_and_falls_back_to_default(home):
    lines, _ = run(*home, "CLAUDE_CONTA=work2 claude")
    assert lines == ["ABRIU work2 "]  # forçada: nem pergunta
    lines, _ = run(*home, "claude", best="")  # sem cota legível
    assert lines[-1] == "ABRIU work " and lines[0] == "usage-all --melhor"


def test_resume_stays_or_moves_by_usage(home):
    # a conversa é da work3; o usage-all diz que ela está boa: fica nela
    lines, out = run(*home, "claude-resume", best="work3", pick="work3 sess-1")
    assert lines == ["claude-last --pick 1", "usage-all --melhor --fica work3", "claude-last --pick --as=work3 1", "ABRIU work3 --resume sess-1"]
    assert out == ""
    # não está: vai para a melhor (o claude-last --as copia a conversa)
    lines, _ = run(*home, "claude-resume 2 --model sonnet", best="gmail", pick="work3 sess-1")
    assert lines[1] == "usage-all --melhor --fica work3" and lines[2] == "claude-last --pick --as=gmail 2"
    assert lines[-1] == "ABRIU gmail --resume sess-1 --model sonnet"


def test_resume_with_as_does_not_ask(home):
    lines, _ = run(*home, "claude-resume --as work2")
    assert lines == ["claude-last --pick --as=work2 1", "ABRIU work2 --resume sess-1"]
