"""A página em si: a aba 📊 uso e o painel da conta a partir do banco do usage-hist, a versão para celular
(/m), o sino 🔔 e os atalhos de teclado. Quase nada gasta cota: só
test_titulo_e_aviso_quando_o_agente_termina manda um turno curto ao claude real (precisa de um agente para
a página ter alguém que "terminou"); os atalhos abrem dois agentes mas não mandam nada. Precisa do
histórico de uso (usage_db_host com banco) para o uso e o painel."""

PHONE = {"width": 390, "height": 844}  # iPhone 14

# Guarda cada Notification criada em window.__notas: o Chromium sem janela não mostra nada, e a página só
# cria a notificação quando `Notification.permission` é granted.
STUB_NOTIFY = """() => {
  window.__notas = [];
  window.Notification = class {
    static get permission() { return 'granted'; }
    static requestPermission() { return Promise.resolve('granted'); }
    constructor(title, opts) { window.__notas.push([title, (opts || {}).body || '']); }
    close() {}
  };
}"""


def wait_usage(orq):
    """A cota (com os botões 📊 ⊞ 🔔) vem do usage-all, que pode demorar uns segundos na primeira consulta."""
    orq.wait_fn("() => document.getElementById('uso') !== null", timeout=60000)


# ---------------------------------------------------------------- 📊 uso e painel da conta


def test_aba_uso_por_janela(orq):
    wait_usage(orq)
    orq.page.click("#uso")
    assert orq.page.is_visible("#view")
    assert orq.page.inner_text("#view .vname").startswith("Uso")
    orq.page.wait_for_selector("#view .usage-table", timeout=60000)
    assert orq.page.inner_text("#view .ptabs button.on") == "5 h"
    assert orq.page.locator("#view .usage-table tr.acc").count() >= 1, "um bloco por conta"
    assert "coleta das" in orq.page.inner_text("#view .vbody")
    orq.shot("uso-5h")
    orq.page.wait_for_timeout(1500)  # pausas: o vídeo é para uma pessoa conferir
    for hours, txt in ((24, "24 h"), (168, "7 dias")):
        orq.page.click(f'#view .ptabs button[data-h="{hours}"]')
        orq.page.wait_for_selector("#view .usage-table", timeout=60000)  # a tabela é refeita a cada janela
        assert orq.page.inner_text("#view .ptabs button.on") == txt
        orq.shot(f"uso-{txt.replace(' ', '')}")
        orq.page.wait_for_timeout(1500)
    orq.page.keyboard.press("Escape")  # Esc fecha a janela
    assert orq.page.is_hidden("#view") and orq.page.inner_html("#view .vbody") == ""
    assert not orq.problems(), orq.problems()


def test_painel_da_conta_pelo_cartao(orq):
    wait_usage(orq)
    orq.wait_fn("() => document.querySelector('#usage .ucard.clickable') !== null", timeout=60000)
    cards = orq.page.locator("#usage .ucard.clickable")
    # o histórico já está em cada cartão: as barras das 24 h e o resumo hoje · ritmo · previsão
    assert cards.first.locator(".hrow svg").count() == 1 and cards.first.locator(".hsum").count() == 1
    # selo de alerta: fica no cartão de cada conta com alerta no histórico (e no TOTAL quando há ⚠)
    items = (orq.state().get("history") or {}).get("alert_items") or []
    for acc in {i.get("account") for i in items if i.get("account")}:
        assert orq.page.locator(f'#usage .ucard[data-acc="{acc}"] .selo').count() == 1, acc
    if any(i.get("level") == "warn" for i in items):
        assert orq.page.locator("#usage .ucard.total .selo").count() == 1
    orq.shot("cota-com-historico")
    orq.page.wait_for_timeout(1500)
    name = cards.first.get_attribute("data-acc")
    cards.first.click()
    orq.page.wait_for_selector("#view .apanel .kpis .kpi", timeout=60000)
    assert orq.page.inner_text("#view .vname").startswith("Painel ·")
    assert orq.page.locator("#view .kpis .kpi").count() == 5, "semana, 5 h, hoje, ritmo, previsão"
    assert orq.page.locator("#view .apanel svg").count() >= 3, "semana até o reset, 24 h, 7 dias"
    assert orq.page.is_visible('#view .ptable[data-p="24"]')
    assert orq.page.is_hidden('#view .ptable[data-p="7"]')
    orq.shot(f"painel-{name}")
    orq.page.wait_for_timeout(2000)
    orq.page.click('#view .ptabs button[data-p="7"]')  # o que mais gastou: 24 h ↔ 7 dias
    assert orq.page.is_hidden('#view .ptable[data-p="24"]')
    assert orq.page.is_visible('#view .ptable[data-p="7"]')
    orq.page.wait_for_timeout(1500)
    orq.page.click("#view .vx")  # ✕ fecha
    assert orq.page.is_hidden("#view")
    assert not orq.problems(), orq.problems()


# ---------------------------------------------------------------- celular (/m)


def test_celular(orq):
    orq.page.set_viewport_size(PHONE)
    orq.page.goto(orq.url("/m"))
    orq.page.wait_for_selector("#tabs .tab.plus", timeout=20000)
    assert orq.page.title() == "orq · celular"
    orq.wait_fn("() => document.querySelector('#usage .ucard.total') !== null", timeout=60000)
    # abas numa linha só; a cota é uma faixa com o TOTAL e as contas abrem no toque
    assert orq.page.evaluate("getComputedStyle(document.getElementById('tabs')).flexDirection") == "row"
    accounts = orq.page.locator("#usage .ucard[data-acc]")
    assert accounts.count() >= 1 and accounts.first.is_hidden()
    orq.page.click("#usage .ucard.total")
    assert accounts.first.is_visible()
    assert orq.page.evaluate("document.getElementById('usage').classList.contains('open')")
    orq.shot("celular-contas")
    orq.page.wait_for_timeout(1500)
    orq.page.click("#usage .ucard.total")
    assert accounts.first.is_hidden()
    assert orq.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "sem rolagem"
    # + abre o seletor na largura da tela e lista os projetos
    orq.page.click('.tab.plus[data-host="laptop"]')
    orq.page.wait_for_selector("#picker:not([hidden])")
    w = orq.page.evaluate("document.querySelector('#picker .pbox').getBoundingClientRect().width")
    assert PHONE["width"] * 0.9 <= w <= PHONE["width"], w
    orq.page.wait_for_selector("#plist .p", timeout=40000)  # as máquinas remotas podem levar uns 20 s
    orq.shot("celular-picker")
    orq.page.wait_for_timeout(1500)
    orq.page.click("#pclose")
    # a aba uso cabe na tela estreita: ficam máquina, pasta, parte, ≈pts e ●
    orq.page.click("#uso")
    orq.page.wait_for_selector("#view .usage-table", timeout=60000)
    assert orq.page.is_hidden("#view .usage-table th:nth-child(3)"), "a coluna tipo some no celular"
    assert orq.page.is_visible("#view .usage-table th:nth-child(2)")
    orq.shot("celular-uso")
    orq.page.wait_for_timeout(2000)
    orq.page.keyboard.press("Escape")
    assert orq.page.is_hidden("#view")
    assert not orq.problems(), orq.problems()


# ---------------------------------------------------------------- 🔔 notificações


def test_sino_pede_permissao_e_testa(orq):
    ctx = orq.page.context
    ctx.grant_permissions(["notifications"], origin=orq.base)
    orq.page.reload()
    wait_usage(orq)
    orq.page.evaluate(STUB_NOTIFY)
    orq.page.click("#bell")  # já permitido: manda a notificação de teste
    orq.wait_fn("() => document.getElementById('toast').textContent.includes('notificação de teste enviada')")
    notas = orq.page.evaluate("window.__notas")
    assert notas and notas[0][0] == "orq: teste", notas
    orq.shot("sino-teste")
    orq.page.wait_for_timeout(1500)
    # sem permissão, o navegador recusa; a página avisa que ficam o bipe e a contagem no título
    ctx.clear_permissions()
    orq.page.reload()
    wait_usage(orq)
    orq.page.click("#bell")
    orq.wait_fn("() => document.getElementById('toast').textContent.includes('recusadas')")
    orq.shot("sino-recusado")
    assert not orq.problems(), orq.problems()


def test_titulo_e_aviso_quando_o_agente_termina(orq, agent):
    """Gasta um turno curto: com a página fora de vista, o fim do turno vira notificação e `(1) orq` no
    título."""
    assert orq.page.title() == "orq"
    orq.page.evaluate(STUB_NOTIFY)
    # a página "fora de vista", como quando você vai a outra aba do navegador
    orq.page.evaluate(
        "() => Object.defineProperty(document, 'hidden', {configurable: true, get: () => true})"
    )
    orq.say("Responda apenas com a palavra pong, em minúsculas, sem pontuação.", wait=False)
    orq.wait_fn("() => document.title === '(1) orq'", timeout=180000)
    orq.wait_fn("() => window.__notas.some((n) => n[0].includes('terminou'))")
    notas = orq.page.evaluate("window.__notas")
    assert notas[-1][0] == f"{agent.split('@')[0]}: terminou", notas
    orq.shot("titulo-espera-voce")
    orq.page.wait_for_timeout(1500)
    # de volta à página: a contagem some
    orq.page.evaluate(
        "() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});"
        " document.dispatchEvent(new Event('visibilitychange')); }"
    )
    orq.wait_fn("() => document.title === 'orq'")
    orq.wait_idle(60)
    assert "pong" in orq.last_ai().lower(), orq.last_ai()
    assert not orq.problems(), orq.problems()


# ---------------------------------------------------------------- atalhos de teclado


def test_atalhos_de_teclado(orq, agent, agent2):
    """⌘1–⌘9 (Ctrl no Linux) trocam de aba, Shift+Enter quebra linha sem enviar, Esc fecha a janela do painel.
    Abre dois agentes mas não manda nada a eles."""
    order = orq.labels()
    assert agent in order and agent2 in order, order
    kbd = orq.page.evaluate("[...document.querySelectorAll('.tab[data-l] .kbd')].map((e) => e.textContent)")
    assert kbd[:2] == ["⌘1", "⌘2"], kbd
    n1, n2 = order.index(agent) + 1, order.index(agent2) + 1
    assert orq.current() == agent
    orq.page.evaluate("document.activeElement && document.activeElement.blur()")
    orq.page.keyboard.press(f"Control+{n2}")
    assert orq.current() == agent2, orq.current()
    orq.page.wait_for_timeout(1000)
    orq.page.keyboard.press(f"Control+{n1}")
    assert orq.current() == agent, orq.current()
    orq.shot("atalho-troca-aba")
    # Shift+Enter quebra linha sem enviar
    orq.page.click("#input")
    orq.page.keyboard.type("primeira linha")
    orq.page.keyboard.press("Shift+Enter")
    orq.page.keyboard.type("segunda linha")
    assert orq.page.input_value("#input") == "primeira linha\nsegunda linha"
    assert orq.page.locator(".chat.on .me").count() == 0, "nada foi enviado"
    orq.shot("atalho-shift-enter")
    orq.page.wait_for_timeout(1500)
    orq.page.fill("#input", "")
    # Esc fecha a janela do painel (uso)
    wait_usage(orq)
    orq.page.click("#uso")  # o foco sai do campo de texto (lá o Esc interrompe o turno)
    orq.page.wait_for_selector("#view:not([hidden])")
    orq.page.keyboard.press("Escape")
    assert orq.page.is_hidden("#view")
    assert not orq.problems(), orq.problems()
