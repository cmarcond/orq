"""Conjuntos (⊞): dois agentes reais abertos de uma vez, cada um com prompt próprio, com um professor
líder que recebe a tarefa inicial e coordena os dois; depois a página recusa abrir de novo (já abertos),
a fotografia das abas vira um conjunto novo, e tudo é desfeito. Gasta cota: uns 5 turnos."""

import json

from conftest import PROJECT, PROJECT2


def cleanup(orq, labels):
    """Pela API: desfaz níveis (senão o professor fica órfão e gruda no próximo agente de mesmo rótulo),
    encerra os agentes e apaga os conjuntos do QA."""
    for lab in labels:
        orq.api(f"/api/agent/{lab}/downgrade", {})
    for lab in labels:
        orq.ensure_stopped(lab)
    for sid in ("qa-conjunto", "qa-foto"):
        orq.api(f"/api/conjuntos/{sid}/delete", {})


def test_conjunto_com_professor(orq):
    host, _, path = PROJECT.partition(":")
    host2, _, path2 = PROJECT2.partition(":")
    name, name2 = path.rstrip("/").split("/")[-1], path2.rstrip("/").split("/")[-1]
    label, label2 = f"{name}@{host}", f"{name2}@{host2}"
    cleanup(orq, (label, label2))
    try:
        _run(orq, host, path, host2, path2, name, name2, label, label2)
    finally:
        cleanup(orq, (label, label2))


def _run(orq, host, path, host2, path2, name, name2, label, label2):
    data = {
        "nome": "QA conjunto",
        "descricao": "dois executores e um professor, pelo QA",
        "permissao": "auto",
        "agentes": [
            {
                "maquina": host,
                "pasta": path,
                "modo": "new",
                "prompt": "Você é o agente ALFA. Comece toda resposta sua com a palavra ALFA.",
            },
            {
                "maquina": host2,
                "pasta": path2,
                "modo": "new",
                "prompt": "Você é o agente BETA. Comece toda resposta sua com a palavra BETA.",
                "tarefa": "Diga em uma linha qual é a sua pasta de trabalho.",
            },
        ],
        "lider": {
            "tarefa": "Pergunte a cada um dos seus executores, pelo SendMessage, qual é a pasta de trabalho "
            "dele, e quando os dois responderem me diga os dois caminhos, um por linha.",
        },
    }
    sid = orq.save_set(data)
    assert sid == "qa-conjunto"
    rows = orq.open_sets()
    row = next(r for r in rows if r["id"] == sid)
    assert "2 agentes" in row["text"] and "professor" in row["text"], row
    orq.shot("conjuntos-lista")

    job = orq.open_set(sid)
    assert job["erros"] == {}, job
    assert sorted(job["feitos"]) == sorted([label, label2]) and job["top"] == f"{name}~professor@{host}"
    orq.shot("conjunto-aberto")
    # a aba do primeiro fala com o professor; a barra diz de que conjunto ele veio
    orq.show(label)
    orq.wait_fn("() => /professor/.test(document.getElementById('cstate').textContent)", timeout=60000)
    assert sid in orq.cstate(), orq.cstate()
    assert orq.levels() == ["professor", "executor"], orq.levels()
    # o professor recebeu a tarefa inicial e delegou aos dois pelo SendMessage
    st = orq.answer_permissions(300)
    assert "erro" not in st, st
    orq.wait_fn(
        "() => [...document.querySelectorAll('#side .wire.in, #side .wire.delivered')].length >= 2",
        timeout=240000,
    )
    orq.wait_idle(180)
    txt = orq.last_ai()
    assert name in txt and name2 in txt, txt
    orq.shot("conjunto-professor-respondeu")
    # o BETA subiu com o prompt dele e recebeu a tarefa própria
    orq.show(label2)
    orq.wait_idle(120)
    assert "BETA" in orq.last_ai(), orq.last_ai()
    assert any(
        a["label"] == label2 and a.get("role", "").startswith("Você é o agente BETA") for a in orq.agents()
    )
    # abrir de novo: já está tudo aberto
    j = orq.api(f"/api/conjuntos/{sid}/open", {})
    assert sorted(j.get("abertos", [])) == sorted([label, label2]), j
    # a fotografia das abas vira um conjunto com os dois e o professor
    foto = orq.snapshot_set("QA foto")
    assert foto["lider"] == {"receita": "professor-executores"}, foto
    assert [a["pasta"] for a in foto["agentes"]][:2] and foto["agentes"][0]["prompt"].startswith(
        "Você é o agente ALFA"
    )
    assert foto["permissao"] == "auto", json.dumps(foto, ensure_ascii=False)
    saved = orq.api("/api/conjuntos/qa-foto")
    assert saved.get("conjunto", {}).get("nome") == "QA foto", saved
    assert not orq.problems(), orq.problems()
    orq.show(label)
    orq.downgrade()
    orq.stop(label)
    orq.stop(label2)
    orq.delete_set("qa-foto")
    orq.delete_set(sid)
    assert not any(r["id"] in (sid, "qa-foto") for r in orq.open_sets())
    orq.page.click("#pclose")
    orq.shot("conjunto-desfeito")
