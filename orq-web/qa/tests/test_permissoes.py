"""Permissões e perguntas pela página, com o claude real. `test_pergunta_do_agente`: o `AskUserQuestion` vira
formulário no chat; o QA responde escolhendo uma opção, depois pelo "Outro…", e por fim cancela (três turnos
curtos, no modo normal do accounts.json). `test_modo_manual_negar_e_permitir`: um conjunto de um agente só com
`permissao: manual` (tudo pergunta); o claude pede para gravar um arquivo, o QA nega (nada é gravado), pede de
novo e o QA permite (dois turnos)."""

import os
import time
from pathlib import Path

import pytest
from conftest import PROJECT
from orqqa import local_host_name

FRUTAS = ("maçã", "banana", "uva")


def ask_card(orq):
    """O formulário pendente: {pergunta, opções}."""
    return orq.page.evaluate(
        """() => {
      const p = document.querySelector('.chat.on .perm.ask:not(.done)');
      if (!p) return null;
      return { pergunta: p.querySelector('.qt').textContent,
               opcoes: [...p.querySelectorAll('.opts .opt b')].map((b) => b.textContent) };
    }"""
    )


def wait_ask(orq, timeout: float = 180):
    orq.page.wait_for_selector(".chat.on .perm.ask:not(.done)", timeout=timeout * 1000)
    return ask_card(orq)


def test_pergunta_do_agente(orq, agent):
    # 1) uma pergunta com opções: o QA escolhe uma
    orq.say(
        "Use a ferramenta AskUserQuestion para me perguntar qual fruta eu prefiro, com exatamente estas três "
        f"opções: {', '.join(FRUTAS)} (uma só resposta). Depois responda apenas: você escolheu <fruta>.",
        wait=False,
    )
    card = wait_ask(orq)
    assert "fruta" in card["pergunta"].lower(), card
    assert all(any(f in o.lower() for o in card["opcoes"]) for f in FRUTAS), card
    assert "pergunta" in orq.cstate() or "permiss" in orq.cstate(), orq.cstate()
    orq.shot("pergunta-formulario")
    orq.page.wait_for_timeout(2000)  # pausas: o vídeo é para uma pessoa conferir
    orq.page.click('.chat.on .perm.ask:not(.done) .opt:has(b:text-is("uva")) input')
    orq.page.click(".chat.on .perm.ask:not(.done) .btns .ok")
    orq.page.wait_for_selector(".chat.on .perm.ask.done")
    assert "respondido" in orq.page.inner_text(".chat.on .perm.ask.done .btns")
    st = orq.wait_idle(120)
    assert "erro" not in st, st
    assert "uva" in orq.last_ai().lower(), orq.last_ai()
    orq.shot("pergunta-respondida")
    # 2) "Outro…": texto livre em vez das opções
    orq.say(
        "Use o AskUserQuestion de novo: pergunte qual cor eu prefiro, com as opções azul e verde. "
        "Depois responda apenas: você escolheu <cor>.",
        wait=False,
    )
    card = wait_ask(orq)
    assert "cor" in card["pergunta"].lower(), card
    orq.page.fill(".chat.on .perm.ask:not(.done) .otxt", "roxo")  # o foco no campo marca o "Outro…"
    orq.page.wait_for_timeout(1500)
    orq.shot("pergunta-outro")
    orq.page.click(".chat.on .perm.ask:not(.done) .btns .ok")
    st = orq.wait_idle(120)
    assert "erro" not in st, st
    assert "roxo" in orq.last_ai().lower(), orq.last_ai()
    # 3) cancelar: o claude fica sem resposta e diz isso
    orq.say(
        "Use o AskUserQuestion mais uma vez: pergunte se eu quero continuar, com as opções sim e não. "
        "Se a pergunta for cancelada ou negada, responda apenas: pergunta cancelada.",
        wait=False,
    )
    wait_ask(orq)
    orq.page.click(".chat.on .perm.ask:not(.done) .btns .no")
    orq.wait_fn(
        "() => [...document.querySelectorAll('.chat.on .perm.ask.done .btns')]"
        ".some((e) => e.textContent.includes('cancelado'))"
    )
    st = orq.wait_idle(120)
    assert "erro" not in st, st
    assert "cancelad" in orq.last_ai().lower(), orq.last_ai()
    orq.shot("pergunta-cancelada")
    assert not orq.problems(), orq.problems()


def deny_all(orq, timeout: float = 180) -> str:
    """Espera o turno acabar negando tudo que o claude pedir no caminho. Devolve o estado final."""
    end = time.time() + timeout
    while True:
        st = orq.wait_idle(max(5.0, end - time.time()))
        if "permiss" not in st:
            return st
        orq.permission(False)
        orq.page.wait_for_timeout(500)


def test_modo_manual_negar_e_permitir(orq):
    host, _, path = PROJECT.partition(":")
    if host != local_host_name():
        pytest.skip(
            f"o teste confere o arquivo nesta máquina ({local_host_name()}), e ORQ_QA_PROJECT está em {host}"
        )
    name = path.rstrip("/").split("/")[-1]
    label = f"{name}@{host}"
    folder = Path(os.path.expanduser(path))
    sid = "qa-manual"
    orq.purge(PROJECT)
    orq.api(f"/api/conjuntos/{sid}/delete", {})
    try:
        _manual(orq, host, path, label, folder, sid)
    finally:
        orq.api(f"/api/conjuntos/{sid}/delete", {})
        orq.purge(PROJECT)


def _manual(orq, host, path, label, folder, sid):
    assert orq.save_set(
        {
            "nome": "QA manual",
            "descricao": "um agente que pergunta tudo",
            "permissao": "manual",
            "agentes": [{"maquina": host, "pasta": path, "modo": "new"}],
        }
    ) == sid
    job = orq.open_set(sid)
    assert job["erros"] == {} and job["feitos"] == [label], job
    orq.show(label)
    orq.wait_fn("() => document.getElementById('cstate').textContent.includes('esperando')", timeout=60000)
    assert next(a for a in orq.agents() if a["label"] == label).get("permission") == "manual", orq.agents()
    # 1) negar: o claude pede para gravar, a página nega, nada é gravado
    orq.say(
        "Crie o arquivo oi.txt nesta pasta com o texto 'oi'. Se a permissão for negada, não tente de outro "
        "jeito: responda apenas 'permissão negada'.",
        wait=False,
    )
    orq.page.wait_for_selector(".chat.on .perm:not(.term):not(.ask):not(.done)", timeout=180000)
    card = orq.page.inner_text(".chat.on .perm:not(.term):not(.ask):not(.done)")
    assert card.startswith("Permissão:") and "oi.txt" in card, card
    assert "permiss" in orq.cstate(), orq.cstate()
    orq.shot("manual-pede")
    orq.page.wait_for_timeout(2000)
    orq.permission(False)
    orq.page.wait_for_selector(".chat.on .perm.done")
    assert "negado" in orq.page.inner_text(".chat.on .perm.done .btns")
    st = deny_all(orq)
    assert "erro" not in st, st
    assert not (folder / "oi.txt").exists(), "negado: nada gravado"
    assert "negad" in orq.last_ai().lower(), orq.last_ai()
    orq.shot("manual-negado")
    orq.page.wait_for_timeout(1500)
    # 2) permitir: o mesmo pedido, agora aceito
    orq.say("Agora tente de novo criar oi.txt com o texto 'oi'. Responda apenas 'criado'.", wait=False)
    orq.page.wait_for_selector(".chat.on .perm:not(.term):not(.ask):not(.done)", timeout=180000)
    orq.shot("manual-pede-de-novo")
    orq.page.wait_for_timeout(1500)
    st = orq.answer_permissions(180)
    assert "erro" not in st, st
    assert orq.page.locator(".chat.on .perm.done").count() >= 2
    last = orq.page.evaluate("[...document.querySelectorAll('.chat.on .perm.done .btns')].pop().textContent")
    assert "permitido" in last, last
    assert (folder / "oi.txt").read_text().strip() == "oi"
    orq.shot("manual-permitido")
    orq.page.wait_for_timeout(1500)
    assert not orq.problems(), orq.problems()
