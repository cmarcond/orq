"""The remote path: everything the server does over ssh when the agent is on another machine.
`ORQ_QA_PROJECT_REMOTE` (default `laptop:~/Projects/orq-qa`) is a throwaway folder on a machine that is not
the server's. Three tests with the real claude: the remote agent shares a file (`orq-agent ls`/`cat`
over ssh, preview and download through the page); an agent from here links to the remote one (tunnel of
`orq-agent link`) and asks for its folder through SendMessage; and a set opens one agent on each machine, each
with its own task. About 5 turns."""

import os
import time

import pytest
from conftest import PROJECT, qa_agent
from orqqa import local_host_name

REMOTE = os.environ.get("ORQ_QA_PROJECT_REMOTE", "laptop:~/Projects/orq-qa")
RHOST, _, RPATH = REMOTE.partition(":")
RNAME = RPATH.rstrip("/").split("/")[-1]


@pytest.fixture
def remote(orq):
    """Real agent on the remote machine; closed and deleted there at the end (purge over ssh)."""
    if RHOST == local_host_name():
        pytest.skip(
            f"ORQ_QA_PROJECT_REMOTE ({REMOTE}) is on the server's machine; the test is for the remote one"
        )
    yield from qa_agent(orq, REMOTE)


def remote_dir(orq, label: str) -> str:
    """Folder of the agent as the page knows it; without the `~`, because until the machine confirms the agent
    the list brings the provisional folder as it was requested (`~/Projects/…`), and claude answers the
    expanded path."""
    return next(a["dir"] for a in orq.agents() if a["label"] == label).rstrip("/").lstrip("~")


def test_agente_remoto_compartilha_por_ssh(orq, remote):
    assert remote == f"{RNAME}@{RHOST}", remote
    entry = next(a for a in orq.agents() if a["label"] == remote)
    assert entry.get("host") == RHOST and entry.get("dir"), entry
    assert "waiting" in orq.cstate(), orq.cstate()
    orq.shot("remoto-aberto")
    orq.say(
        "Create a file called resumo.md with exactly one line of text: 'remote QA'. "
        "Then share that file with me through the page. Answer only 'done' when you finish.",
        wait=False,
    )
    st = orq.answer_permissions()
    assert "error" not in st, st
    # the list comes from `orq-agent ls` over ssh; the preview and the download, from `orq-agent cat`
    orq.wait_file("resumo.md")
    orq.shot("remoto-outbox")
    orq.page.wait_for_timeout(1500)  # pauses: the video is for a person to check
    assert orq.preview("resumo.md") == "DIV"
    assert "remote QA" in orq.page.inner_text("#view .vbody")
    orq.shot("remoto-previa")
    orq.page.wait_for_timeout(2000)
    orq.close_preview()
    assert orq.download("resumo.md").strip() == b"remote QA"
    assert not orq.problems(), orq.problems()


def test_vinculo_entre_maquinas(orq, agent, remote):
    orq.show(agent)
    assert orq.links() == []
    linked = orq.link(remote)
    assert linked == [remote], linked
    lk = next(k for k in orq.state()["links"] if {k["local"], k["remote"]} == {agent, remote})
    assert lk.get("kind") == "ssh" and lk.get("up"), lk
    assert "ssh" in orq.page.inner_text(f'#links .lcard[data-l="{remote}"]')
    orq.shot("remoto-vinculo")
    orq.page.wait_for_timeout(1500)
    other = remote.replace("@", "-")  # session name of the other one (sanitised label)
    orq.say(
        f"Ask the agent {other}, through SendMessage, what its working folder is (ask it to reply "
        "through SendMessage to you). When the reply arrives, tell me only the path it answered.",
        wait=False,
    )
    out = orq.wait_wire("executor", "", timeout=180)  # the request left through the wire and the tunnel
    assert other in out["head"], out
    # the reply comes back through the tunnel as a message from another session (✓✓ on the wire); claude
    # reports it in a new turn
    deadline = time.time() + 240
    while time.time() < deadline and not any(w["delivered"] for w in orq.wires()):
        orq.page.wait_for_timeout(2000)
    assert any(w["delivered"] for w in orq.wires()), orq.wires()
    rdir = remote_dir(orq, remote)
    deadline = time.time() + 240
    while time.time() < deadline and rdir not in orq.last_ai():
        if "permission" in orq.cstate():
            orq.permission(True)
        orq.page.wait_for_timeout(1000)
    assert rdir in orq.last_ai(), (rdir, orq.last_ai())
    orq.shot("remoto-resposta")
    orq.page.wait_for_timeout(1500)
    assert not orq.problems(), orq.problems()
    orq.unlink(remote)
    assert orq.links() == []
    deadline = time.time() + 45  # the query to the machines (every 15 s) confirms the tunnel is gone
    while time.time() < deadline and any(
        {k["local"], k["remote"]} == {agent, remote} and k.get("up") for k in orq.state()["links"]
    ):
        orq.page.wait_for_timeout(2000)
    assert not any({k["local"], k["remote"]} == {agent, remote} and k.get("up") for k in orq.state()["links"])


def test_conjunto_entre_maquinas(orq):
    if RHOST == local_host_name():
        pytest.skip(
            f"ORQ_QA_PROJECT_REMOTE ({REMOTE}) is on the server's machine; the test is for the remote one"
        )
    host, _, path = PROJECT.partition(":")
    label, rlabel = f"{path.rstrip('/').split('/')[-1]}@{host}", f"{RNAME}@{RHOST}"
    sid = "qa-duas-maquinas"
    for spec in (PROJECT, REMOTE):
        orq.purge(spec)
    orq.api(f"/api/conjuntos/{sid}/delete", {})
    try:
        _conjunto(orq, host, path, label, rlabel, sid)
    finally:
        orq.api(f"/api/conjuntos/{sid}/delete", {})
        for spec in (PROJECT, REMOTE):
            orq.purge(spec)


def _conjunto(orq, host, path, label, rlabel, sid):
    task = "Say in a single line what your working folder is (the full path), nothing else."
    assert (
        orq.save_set(
            {
                "nome": "QA duas maquinas",
                "descricao": "one agent here and one on the remote machine, each with its own task",
                "permissao": "auto",
                "agentes": [
                    {"maquina": host, "pasta": path, "modo": "new", "tarefa": task},
                    {"maquina": RHOST, "pasta": RPATH, "modo": "new", "tarefa": task},
                ],
            }
        )
        == sid
    )
    job = orq.open_set(sid)
    assert job["erros"] == {} and sorted(job["feitos"]) == sorted([label, rlabel]), job
    orq.shot("remoto-conjunto")
    for lab in (label, rlabel):
        orq.show(lab)
        want = remote_dir(orq, lab)
        # the task was injected by the server: waits for the reply with the path, allowing whatever it asks
        deadline = time.time() + 240
        while time.time() < deadline:
            if "permission" in orq.cstate():
                orq.permission(True)
            if want in orq.last_ai():
                break
            orq.page.wait_for_timeout(1000)
        assert want in orq.last_ai(), (lab, want, orq.last_ai())
        orq.shot(f"remoto-conjunto-{lab.split('@')[1]}")
        orq.page.wait_for_timeout(1500)
    assert not orq.problems(), orq.problems()
