"""The page itself: the 📊 usage tab and the account panel from the usage-hist database, the phone version
(/m), the 🔔 bell and the keyboard shortcuts. Almost nothing spends quota: only
test_titulo_e_aviso_quando_o_agente_termina sends a short turn to the real claude (it needs an agent for
the page to have someone that "finished"); the shortcuts open two agents but send nothing. Needs the
usage history (usage_db_host with a database) for the usage and the panel."""

PHONE = {"width": 390, "height": 844}  # iPhone 14

# Keeps each Notification created in window.__notas: headless Chromium shows nothing, and the page only
# creates the notification when `Notification.permission` is granted.
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
    """The quota (with the 📊 ⊞ 🔔 buttons) comes from usage-all, which may take a few seconds on the first
    query."""
    orq.wait_fn("() => document.getElementById('uso') !== null", timeout=60000)


# ---------------------------------------------------------------- 📊 usage and account panel


def test_aba_uso_por_janela(orq):
    wait_usage(orq)
    orq.page.click("#uso")
    assert orq.page.is_visible("#view")
    assert orq.page.inner_text("#view .vname").startswith("Usage")
    orq.page.wait_for_selector("#view .usage-table", timeout=60000)
    assert orq.page.inner_text("#view .ptabs button.on") == "5 h"
    assert orq.page.locator("#view .usage-table tr.acc").count() >= 1, "one block per account"
    assert "collected at" in orq.page.inner_text("#view .vbody")
    orq.shot("uso-5h")
    orq.page.wait_for_timeout(1500)  # pauses: the video is for a person to check
    for hours, txt in ((24, "24 h"), (168, "7 days")):
        orq.page.click(f'#view .ptabs button[data-h="{hours}"]')
        orq.page.wait_for_selector("#view .usage-table", timeout=60000)  # the table is redone per window
        assert orq.page.inner_text("#view .ptabs button.on") == txt
        orq.shot(f"uso-{txt.replace(' ', '')}")
        orq.page.wait_for_timeout(1500)
    orq.page.keyboard.press("Escape")  # Esc closes the window
    assert orq.page.is_hidden("#view") and orq.page.inner_html("#view .vbody") == ""
    assert not orq.problems(), orq.problems()


def test_painel_da_conta_pelo_cartao(orq):
    wait_usage(orq)
    orq.wait_fn("() => document.querySelector('#usage .ucard.clickable') !== null", timeout=60000)
    cards = orq.page.locator("#usage .ucard.clickable")
    # the history is already on each card: the 24 h bars and the summary today · pace · forecast
    assert cards.first.locator(".hrow svg").count() == 1 and cards.first.locator(".hsum").count() == 1
    # alert badge: stays on the card of each account with an alert in the history (and on the TOTAL with ⚠)
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
    assert orq.page.inner_text("#view .vname").startswith("Panel ·")
    assert orq.page.locator("#view .kpis .kpi").count() == 5, "week, 5 h, today, pace, forecast"
    assert orq.page.locator("#view .apanel svg").count() >= 3, "week up to the reset, 24 h, 7 days"
    assert orq.page.is_visible('#view .ptable[data-p="24"]')
    assert orq.page.is_hidden('#view .ptable[data-p="7"]')
    orq.shot(f"painel-{name}")
    orq.page.wait_for_timeout(2000)
    orq.page.click('#view .ptabs button[data-p="7"]')  # what spent the most: 24 h ↔ 7 days
    assert orq.page.is_hidden('#view .ptable[data-p="24"]')
    assert orq.page.is_visible('#view .ptable[data-p="7"]')
    orq.page.wait_for_timeout(1500)
    orq.page.click("#view .vx")  # ✕ closes
    assert orq.page.is_hidden("#view")
    assert not orq.problems(), orq.problems()


# ---------------------------------------------------------------- phone (/m)


def test_celular(orq):
    orq.page.set_viewport_size(PHONE)
    orq.page.goto(orq.url("/m"))
    orq.page.wait_for_selector("#tabs .tab.plus", timeout=20000)
    assert orq.page.title() == "orq · phone"
    orq.wait_fn("() => document.querySelector('#usage .ucard.total') !== null", timeout=60000)
    # tabs on a single row; the quota is a strip with the TOTAL and the accounts open on touch
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
    assert orq.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "no scrolling"
    # + opens the picker at the width of the screen and lists the projects
    orq.page.click('.tab.plus[data-host="laptop"]')
    orq.page.wait_for_selector("#picker:not([hidden])")
    w = orq.page.evaluate("document.querySelector('#picker .pbox').getBoundingClientRect().width")
    assert PHONE["width"] * 0.9 <= w <= PHONE["width"], w
    orq.page.wait_for_selector("#plist .p", timeout=40000)  # the remote machines may take about 20 s
    orq.shot("celular-picker")
    orq.page.wait_for_timeout(1500)
    orq.page.click("#pclose")
    # the usage tab fits the narrow screen: machine, folder, share, ≈pts and ● remain
    orq.page.click("#uso")
    orq.page.wait_for_selector("#view .usage-table", timeout=60000)
    assert orq.page.is_hidden("#view .usage-table th:nth-child(3)"), "the type column disappears on the phone"
    assert orq.page.is_visible("#view .usage-table th:nth-child(2)")
    orq.shot("celular-uso")
    orq.page.wait_for_timeout(2000)
    orq.page.keyboard.press("Escape")
    assert orq.page.is_hidden("#view")
    assert not orq.problems(), orq.problems()


# ---------------------------------------------------------------- 🔔 notifications


def test_sino_pede_permissao_e_testa(orq):
    ctx = orq.page.context
    ctx.grant_permissions(["notifications"], origin=orq.base)
    orq.page.reload()
    wait_usage(orq)
    orq.page.evaluate(STUB_NOTIFY)
    orq.page.click("#bell")  # already allowed: sends the test notification
    orq.wait_fn("() => document.getElementById('toast').textContent.includes('test notification sent')")
    notas = orq.page.evaluate("window.__notas")
    assert notas and notas[0][0] == "orq: test", notas
    orq.shot("sino-teste")
    orq.page.wait_for_timeout(1500)
    # without permission, the browser refuses; the page warns that the beep and the count in the title remain
    ctx.clear_permissions()
    orq.page.reload()
    wait_usage(orq)
    orq.page.click("#bell")
    orq.wait_fn("() => document.getElementById('toast').textContent.includes('notifications refused')")
    orq.shot("sino-recusado")
    assert not orq.problems(), orq.problems()


def test_titulo_e_aviso_quando_o_agente_termina(orq, agent):
    """Spends a short turn: with the page out of sight, the end of the turn becomes a notification and
    `(1) orq` in the title."""
    assert orq.page.title() == "orq"
    orq.page.evaluate(STUB_NOTIFY)
    # the page "out of sight", as when you go to another browser tab
    orq.page.evaluate(
        "() => Object.defineProperty(document, 'hidden', {configurable: true, get: () => true})"
    )
    orq.say("Answer only with the word pong, in lowercase, without punctuation.", wait=False)
    orq.wait_fn("() => document.title === '(1) orq'", timeout=180000)
    orq.wait_fn("() => window.__notas.some((n) => n[0].includes('finished'))")
    notas = orq.page.evaluate("window.__notas")
    assert notas[-1][0] == f"{agent.split('@')[0]}: finished", notas
    orq.shot("titulo-espera-voce")
    orq.page.wait_for_timeout(1500)
    # back on the page: the count disappears
    orq.page.evaluate(
        "() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});"
        " document.dispatchEvent(new Event('visibilitychange')); }"
    )
    orq.wait_fn("() => document.title === 'orq'")
    orq.wait_idle(60)
    assert "pong" in orq.last_ai().lower(), orq.last_ai()
    assert not orq.problems(), orq.problems()


# ---------------------------------------------------------------- keyboard shortcuts


def test_atalhos_de_teclado(orq, agent, agent2):
    """⌘1–⌘9 (Ctrl on Linux) switch tabs, Shift+Enter breaks the line without sending, Esc closes the panel
    window. Opens two agents but sends nothing to them."""
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
    # Shift+Enter breaks the line without sending
    orq.page.click("#input")
    orq.page.keyboard.type("first line")
    orq.page.keyboard.press("Shift+Enter")
    orq.page.keyboard.type("second line")
    assert orq.page.input_value("#input") == "first line\nsecond line"
    assert orq.page.locator(".chat.on .me").count() == 0, "nothing was sent"
    orq.shot("atalho-shift-enter")
    orq.page.wait_for_timeout(1500)
    orq.page.fill("#input", "")
    # Esc closes the panel window (usage)
    wait_usage(orq)
    orq.page.click("#uso")  # the focus leaves the text field (there Esc interrupts the turn)
    orq.page.wait_for_selector("#view:not([hidden])")
    orq.page.keyboard.press("Escape")
    assert orq.page.is_hidden("#view")
    assert not orq.problems(), orq.problems()
