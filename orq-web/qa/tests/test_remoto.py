"""O caminho remoto: tudo que o servidor faz por ssh quando o agente está noutra máquina.
`ORQ_QA_PROJECT_REMOTE` (padrão `laptop:~/Projects/orq-qa`) é uma pasta descartável numa máquina que não é a do
servidor. Três testes com claude real: o agente remoto compartilha um arquivo (`orq-agent ls`/`cat` por ssh,
prévia e download pela página); um agente daqui se vincula ao remoto (túnel do `orq-agent link`) e pergunta a pasta
dele pelo SendMessage; e um conjunto abre um agente em cada máquina, cada um com a própria tarefa. Uns 5 turnos."""

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
    """Agente real na máquina remota; encerrado e apagado lá no fim (purge por ssh)."""
    if RHOST == local_host_name():
        pytest.skip(f"ORQ_QA_PROJECT_REMOTE ({REMOTE}) está na máquina do servidor; o teste é do caminho remoto")
    yield from qa_agent(orq, REMOTE)


def remote_dir(orq, label: str) -> str:
    """Pasta do agente como a página a conhece; sem o `~`, porque até a máquina confirmar o agente a lista traz
    a pasta provisória tal como foi pedida (`~/Projects/…`), e o claude responde o caminho expandido."""
    return next(a["dir"] for a in orq.agents() if a["label"] == label).rstrip("/").lstrip("~")


def test_agente_remoto_compartilha_por_ssh(orq, remote):
    assert remote == f"{RNAME}@{RHOST}", remote
    entry = next(a for a in orq.agents() if a["label"] == remote)
    assert entry.get("host") == RHOST and entry.get("dir"), entry
    assert "esperando" in orq.cstate(), orq.cstate()
    orq.shot("remoto-aberto")
    orq.say(
        "Crie um arquivo chamado resumo.md com exatamente uma linha de texto: 'QA remoto'. "
        "Depois compartilhe esse arquivo comigo pela página. Responda só 'pronto' quando terminar.",
        wait=False,
    )
    st = orq.answer_permissions()
    assert "erro" not in st, st
    # a lista vem de `orq-agent ls` por ssh; a prévia e o download, de `orq-agent cat`
    orq.wait_file("resumo.md")
    orq.shot("remoto-outbox")
    orq.page.wait_for_timeout(1500)  # pausas: o vídeo é para uma pessoa conferir
    assert orq.preview("resumo.md") == "DIV"
    assert "QA remoto" in orq.page.inner_text("#view .vbody")
    orq.shot("remoto-previa")
    orq.page.wait_for_timeout(2000)
    orq.close_preview()
    assert orq.download("resumo.md").strip() == b"QA remoto"
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
    other = remote.replace("@", "-")  # nome de sessão do outro (rótulo saneado)
    orq.say(
        f"Pergunte ao agente {other}, pelo SendMessage, qual é a pasta de trabalho dele (peça que responda "
        "pelo SendMessage a você). Quando a resposta chegar, me diga só o caminho que ele respondeu.",
        wait=False,
    )
    out = orq.wait_wire("executor", "", timeout=180)  # o pedido saiu pelo fio, pelo túnel
    assert other in out["head"], out
    # a resposta volta pelo túnel como mensagem de outra sessão (✓✓ no fio); o claude a relata num turno novo
    deadline = time.time() + 240
    while time.time() < deadline and not any(w["delivered"] for w in orq.wires()):
        orq.page.wait_for_timeout(2000)
    assert any(w["delivered"] for w in orq.wires()), orq.wires()
    rdir = remote_dir(orq, remote)
    deadline = time.time() + 240
    while time.time() < deadline and rdir not in orq.last_ai():
        if "permiss" in orq.cstate():
            orq.permission(True)
        orq.page.wait_for_timeout(1000)
    assert rdir in orq.last_ai(), (rdir, orq.last_ai())
    orq.shot("remoto-resposta")
    orq.page.wait_for_timeout(1500)
    assert not orq.problems(), orq.problems()
    orq.unlink(remote)
    assert orq.links() == []
    deadline = time.time() + 45  # a consulta às máquinas (a cada 15 s) confirma o túnel desfeito
    while time.time() < deadline and any(
        {k["local"], k["remote"]} == {agent, remote} and k.get("up") for k in orq.state()["links"]
    ):
        orq.page.wait_for_timeout(2000)
    assert not any({k["local"], k["remote"]} == {agent, remote} and k.get("up") for k in orq.state()["links"])


def test_conjunto_entre_maquinas(orq):
    if RHOST == local_host_name():
        pytest.skip(f"ORQ_QA_PROJECT_REMOTE ({REMOTE}) está na máquina do servidor; o teste é do caminho remoto")
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
    task = "Diga em uma linha só qual é a sua pasta de trabalho (o caminho completo), nada mais."
    assert orq.save_set(
        {
            "nome": "QA duas maquinas",
            "descricao": "um agente aqui e um na máquina remota, cada um com a própria tarefa",
            "permissao": "auto",
            "agentes": [
                {"maquina": host, "pasta": path, "modo": "new", "tarefa": task},
                {"maquina": RHOST, "pasta": RPATH, "modo": "new", "tarefa": task},
            ],
        }
    ) == sid
    job = orq.open_set(sid)
    assert job["erros"] == {} and sorted(job["feitos"]) == sorted([label, rlabel]), job
    orq.shot("remoto-conjunto")
    for lab in (label, rlabel):
        orq.show(lab)
        want = remote_dir(orq, lab)
        # a tarefa foi injetada pelo servidor: espera a resposta com o caminho, permitindo o que pedir
        deadline = time.time() + 240
        while time.time() < deadline:
            if "permiss" in orq.cstate():
                orq.permission(True)
            if want in orq.last_ai():
                break
            orq.page.wait_for_timeout(1000)
        assert want in orq.last_ai(), (lab, want, orq.last_ai())
        orq.shot(f"remoto-conjunto-{lab.split('@')[1]}")
        orq.page.wait_for_timeout(1500)
    assert not orq.problems(), orq.problems()
