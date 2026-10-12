"""Troca automática de conta com o claude real, forçada por baixo.

Não dá para esgotar a cota sob demanda, então o QA grava `state: quota` (com a mensagem a reenviar em
`retry`) no state.json do agente, exatamente o que o supervisor faz ao ver o `rate_limit_event`. Daí em
diante é tudo de verdade: o servidor escolhe a conta com mais folga, reabre o agente nela na mesma
conversa (`claude-last --as` copia a conversa) e reenvia a mensagem; o claude novo tem de lembrar o que
o antigo sabia. Leva uns 2 minutos e gasta um pouco de cota em duas contas."""

SECRET = "abacaxi-742"
WATCH_TOASTS = """() => {
  window.__toasts = [];
  const t = document.getElementById('toast');
  new MutationObserver(() => { if (t.textContent) window.__toasts.push(t.textContent); })
    .observe(t, { childList: true, characterData: true, subtree: true });
}"""


def test_troca_de_conta_forcada_continua_a_conversa(orq, agent):
    old = orq.account_of(agent)
    assert old, orq.agents()
    reply = orq.say(f"Guarde a palavra-chave {SECRET}. Responda apenas: guardado.")
    assert "guardado" in reply.lower(), reply
    orq.shot("troca-antes")

    orq.page.evaluate(WATCH_TOASTS)
    orq.force_quota(agent, retry="Qual é a palavra-chave que pedi para guardar? Responda só com ela.")
    new = orq.wait_account_change(agent, old)
    assert new != old
    assert any(
        m.get("label") == agent and m.get("from") == old and m.get("to") == new for m in orq.migrations()
    ), orq.migrations()

    # a mensagem pendente foi reenviada na conta nova, e o claude novo lembra a conversa do antigo
    orq.page.wait_for_function(
        "(s) => [...document.querySelectorAll('.chat.on .ai')].some(e => e.innerText.includes(s))",
        arg=SECRET,
        timeout=240000,
    )
    orq.page.wait_for_function(
        "() => document.getElementById('cstate').textContent.includes('esperando')", timeout=60000
    )
    orq.shot("troca-depois")
    assert SECRET in orq.last_ai(), orq.last_ai()
    toasts = orq.page.evaluate("window.__toasts")
    assert any("sem cota" in t for t in toasts), toasts
    assert orq.account_of(agent) == new
    assert not orq.problems(), orq.problems()
