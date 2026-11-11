"""Sets (⊞): two real agents opened at once, each with its own prompt, with a lead teacher that receives
the initial task and coordinates the two; then the page refuses to open again (already open), the snapshot of
the tabs becomes a new set, and everything is undone. Spends quota: about 5 turns."""

import json
from pathlib import Path

from conftest import PROJECT, PROJECT2


def cleanup(orq):
    """Undoes the teacher, closes the agents and deletes the QA folders (Orq.purge), and deletes the sets."""
    for spec in (PROJECT, PROJECT2):
        orq.purge(spec)
    for sid in ("qa-conjunto", "qa-foto"):
        orq.api(f"/api/conjuntos/{sid}/delete", {})


def test_conjunto_com_professor(orq):
    host, _, path = PROJECT.partition(":")
    host2, _, path2 = PROJECT2.partition(":")
    name, name2 = path.rstrip("/").split("/")[-1], path2.rstrip("/").split("/")[-1]
    label, label2 = f"{name}@{host}", f"{name2}@{host2}"
    cleanup(orq)
    try:
        _run(orq, host, path, host2, path2, name, name2, label, label2)
    finally:
        cleanup(orq)


def _run(orq, host, path, host2, path2, name, name2, label, label2):
    data = {
        "nome": "QA conjunto",
        "descricao": "two executors and a teacher, through the QA",
        "permissao": "auto",
        "agentes": [
            {
                "maquina": host,
                "pasta": path,
                "modo": "new",
                "prompt": "You are the agent ALFA. Start every reply of yours with the word ALFA.",
            },
            {
                "maquina": host2,
                "pasta": path2,
                "modo": "new",
                "prompt": "You are the agent BETA. Start every reply of yours with the word BETA.",
                "tarefa": "Say in one line what your working folder is.",
            },
        ],
        "lider": {
            "tarefa": "Ask each of your executors, through SendMessage, what its working folder is, and "
            "when both have answered tell me the two paths, one per line.",
        },
    }
    sid = orq.save_set(data)
    assert sid == "qa-conjunto"
    rows = orq.open_sets()
    row = next(r for r in rows if r["id"] == sid)
    assert "2 agents" in row["text"] and "teacher" in row["text"], row
    orq.shot("conjuntos-lista")

    job = orq.open_set(sid)
    assert job["erros"] == {}, job
    assert sorted(job["feitos"]) == sorted([label, label2]) and job["top"] == f"{name}~professor@{host}"
    orq.shot("conjunto-aberto")
    # the tab of the first one talks to the teacher; the bar says which set it came from
    orq.show(label)
    orq.wait_fn("() => /professor/.test(document.getElementById('cstate').textContent)", timeout=60000)
    assert sid in orq.cstate(), orq.cstate()
    assert orq.levels() == ["professor", "executor"], orq.levels()
    # the teacher received the initial task and delegated to both through SendMessage
    st = orq.answer_permissions(300)
    assert "error" not in st, st
    orq.wait_fn(
        "() => [...document.querySelectorAll('#side .wire.in, #side .wire.delivered')].length >= 2",
        timeout=240000,
    )
    orq.wait_idle(180)
    txt = orq.last_ai()
    assert name in txt and name2 in txt, txt
    orq.shot("conjunto-professor-respondeu")
    # BETA started with its prompt and received its own task
    orq.show(label2)
    orq.wait_idle(120)
    assert "BETA" in orq.last_ai(), orq.last_ai()
    assert any(
        a["label"] == label2 and a.get("role", "").startswith("You are the agent BETA") for a in orq.agents()
    )
    # open again: everything is already open
    j = orq.api(f"/api/conjuntos/{sid}/open", {})
    assert sorted(j.get("abertos", [])) == sorted([label, label2]), j
    # the snapshot of the tabs becomes a set with both and the teacher
    foto = orq.snapshot_set("QA foto")
    assert foto["lider"] == {"receita": "professor-executores"}, foto
    assert [a["pasta"] for a in foto["agentes"]][:2] and foto["agentes"][0]["prompt"].startswith(
        "You are the agent ALFA"
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


def test_enviar_json_pelo_botao(orq, tmp_path):
    """📄 upload JSON: the button opens the browser's file picker; the JSON becomes a saved set (the name
    comes from the file when it is missing), ⬇ downloads the same JSON, invalid JSON and an invalid set only
    warn, and ✕ twice deletes. Nothing is opened: spends no quota."""
    sid = "qa-json-botao"
    orq.api(f"/api/conjuntos/{sid}/delete", {})
    try:
        _json_botao(orq, tmp_path, sid)
    finally:
        orq.api(f"/api/conjuntos/{sid}/delete", {})


def _upload(orq, path):
    with orq.page.expect_file_chooser() as fc:
        orq.page.click("#setup")
    fc.value.set_files(str(path))


def _json_botao(orq, tmp_path, sid):
    host = PROJECT.partition(":")[0]
    data = {
        "descricao": "uploaded through the 📄 button in the QA",
        "permissao": "auto",
        "agentes": [
            {"maquina": host, "pasta": PROJECT.partition(":")[2], "modo": "new", "prompt": "You are ALFA."},
            {"maquina": host, "pasta": PROJECT2.partition(":")[2], "modo": "new", "tarefa": "Say hi."},
        ],
    }
    ok = tmp_path / f"{sid}.json"  # without `nome`: the name comes from the file
    ok.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    rows = orq.open_sets()
    assert not any(r["id"] == sid for r in rows)
    orq.shot("json-lista-antes")
    orq.page.wait_for_timeout(1500)  # pauses: the video is for a person to check
    _upload(orq, ok)
    orq.wait_fn("(s) => document.getElementById('toast').textContent.includes(`set ${s} saved`)", arg=sid)
    orq.page.wait_for_selector(f'#plist .setp[data-id="{sid}"]', timeout=20000)
    row = orq.page.inner_text(f'#plist .setp[data-id="{sid}"]')
    assert sid in row and "2 agents" in row and "independent" in row and "auto" in row, row
    assert "uploaded through the" in row
    orq.shot("json-salvo")
    orq.page.wait_for_timeout(1500)
    saved = orq.api(f"/api/conjuntos/{sid}")["conjunto"]
    assert saved["nome"] == sid, saved
    assert [a["pasta"] for a in saved["agentes"]] == [a["pasta"] for a in data["agentes"]]
    # ⬇ downloads the saved JSON
    with orq.page.expect_download() as dl:
        orq.page.click(f'#plist .setp[data-id="{sid}"] [data-dl]')
    got = json.loads(Path(dl.value.path()).read_text())
    assert dl.value.suggested_filename == f"{sid}.json"
    assert got["nome"] == sid and len(got["agentes"]) == 2, got
    # broken JSON and invalid set: only a notice, the list does not change
    bad = tmp_path / "quebrado.json"
    bad.write_text("{ this is not json")
    _upload(orq, bad)
    orq.wait_fn("() => document.getElementById('toast').textContent.includes('invalid JSON')")
    orq.shot("json-invalido")
    orq.page.wait_for_timeout(1500)
    wrong = tmp_path / "qa-json-ruim.json"
    wrong.write_text(json.dumps({"agentes": "not a list", "permissao": "everything"}))
    _upload(orq, wrong)
    orq.wait_fn("() => /agentes.*list|permissao/.test(document.getElementById('toast').textContent)")
    assert "agentes" in orq.toast() and "permissao" in orq.toast(), orq.toast()
    orq.shot("json-conjunto-invalido")
    orq.page.wait_for_timeout(1500)
    assert not any(r["id"] == "qa-json-ruim" for r in orq.open_sets())
    # ✕ twice deletes
    orq.delete_set(sid)
    assert not any(r["id"] == sid for r in orq.open_sets())
    orq.page.click("#pclose")
    orq.shot("json-apagado")
    # the 400 of the invalid set is expected; any other problem counts
    rest = [p for p in orq.problems() if not ("[http 400]" in p and p.endswith("/api/conjuntos"))]
    assert not rest, orq.problems()
