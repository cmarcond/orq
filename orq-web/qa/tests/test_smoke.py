"""A página sobe, mostra a cota e lista projetos. Não gasta cota de claude."""


def test_pagina_abre_sem_erros(orq):
    assert orq.page.title() == "orq"
    # a cota vem do usage-all, que pode demorar uns segundos na primeira consulta
    orq.wait_fn("() => document.getElementById('usage').textContent.trim().length > 0", timeout=60000)
    st = orq.state()
    assert st.get("accounts"), "nenhuma conta no /api/state"
    assert not [p for p in orq.problems() if "pageerror" in p], orq.problems()


def test_picker_lista_projetos(orq):
    orq.page.click('.tab.plus[data-host="laptop"]')
    orq.page.wait_for_selector("#picker:not([hidden])")
    assert "Abrir projeto em laptop" in orq.page.inner_text("#ptitle")
    assert orq.page.input_value("#pnew") == "laptop:~/"
    # claude-last --projects em todas as máquinas; as remotas podem levar até ~20 s
    orq.page.wait_for_selector("#plist .p", timeout=40000)
    assert orq.page.locator("#plist .p").count() >= 1
    orq.shot("picker")
    orq.page.click("#pclose")
    assert orq.page.is_hidden("#picker")
