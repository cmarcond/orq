"""The page comes up, shows the quota and lists projects. Spends no claude quota."""


def test_pagina_abre_sem_erros(orq):
    assert orq.page.title() == "orq"
    # the quota comes from usage-all, which may take a few seconds on the first query
    orq.wait_fn("() => document.getElementById('usage').textContent.trim().length > 0", timeout=60000)
    st = orq.state()
    assert st.get("accounts"), "no account in /api/state"
    assert not [p for p in orq.problems() if "pageerror" in p], orq.problems()


def test_picker_lista_projetos(orq):
    orq.page.click('.tab.plus[data-host="laptop"]')
    orq.page.wait_for_selector("#picker:not([hidden])")
    assert "Open project on laptop" in orq.page.inner_text("#ptitle")
    assert orq.page.input_value("#pnew") == "laptop:~/"
    # claude-last --projects on all machines; the remote ones may take up to ~20 s
    orq.page.wait_for_selector("#plist .p", timeout=40000)
    assert orq.page.locator("#plist .p").count() >= 1
    orq.shot("picker")
    orq.page.click("#pclose")
    assert orq.page.is_hidden("#picker")
