"""Automatic account switch with the real claude, forced from underneath.

It is not possible to exhaust the quota on demand, so the QA writes `state: quota` (with the message to resend
in `retry`) in the agent's state.json, exactly what the supervisor does on seeing the `rate_limit_event`. From
there on everything is real: the server picks the account with the most slack, reopens the agent on it in the
same conversation (`claude-last --as` copies the conversation) and resends the message; the new claude has to
remember what the old one knew. Takes about 2 minutes and spends a little quota on two accounts."""

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
    reply = orq.say(f"Keep the keyword {SECRET}. Answer only: stored.")
    assert "stored" in reply.lower(), reply
    orq.shot("troca-antes")

    orq.page.evaluate(WATCH_TOASTS)
    orq.force_quota(agent, retry="What is the keyword I asked you to keep? Answer only with it.")
    new = orq.wait_account_change(agent, old)
    assert new != old
    assert any(
        m.get("label") == agent and m.get("from") == old and m.get("to") == new for m in orq.migrations()
    ), orq.migrations()

    # the pending message was resent on the new account, and the new claude remembers the old one's
    # conversation
    orq.wait_fn(
        "(s) => [...document.querySelectorAll('.chat.on .ai')].some(e => e.innerText.includes(s))",
        arg=SECRET,
        timeout=240000,
    )
    orq.wait_fn("() => document.getElementById('cstate').textContent.includes('waiting')", timeout=60000)
    orq.shot("troca-depois")
    assert SECRET in orq.last_ai(), orq.last_ai()
    toasts = orq.page.evaluate("window.__toasts")
    assert any("out of quota" in t for t in toasts), toasts
    assert orq.account_of(agent) == new
    assert not orq.problems(), orq.problems()
