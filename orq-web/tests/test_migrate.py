"""Automatic account switch when the quota runs out (Migrator), with fake spawn and delivery."""

import threading

from orqweb import config
from orqweb.server import state as S
from orqweb.server.migrate import Migrator


class Ext:
    def __init__(self, agents):
        self._agents = agents

    def all_agents(self, hosts):
        return [dict(a) for a in self._agents], {}

    def usage(self, host=None):
        return {
            "gmail": {"5h": 99, "7d": 50, "7d_pace": 50},  # the agent's account: out of quota
            "work": {"5h": 10, "7d": 30, "7d_pace": 50},  # slack
            "work2": {"5h": 96, "7d": 10, "7d_pace": 50},  # also out of quota
        }


def make(agents, enabled=True):
    conf = config.parse(
        {"accounts": [{"name": n, "config_dir": f"~/.c-{n}"} for n in ("gmail", "work", "work2")]}
    )
    state = S.StateCache(conf, Ext(agents))
    state.refresh_usage()
    calls, sent = [], []
    done = threading.Event()

    def spawn(host, label, cwd, account, mode, **kw):
        calls.append((host, label, cwd, account, mode, kw))
        return "went wrong" if cwd == "/falha" else None

    def deliver(label, obj):
        sent.append((label, obj))
        done.set()
        return None

    m = Migrator(conf, state, spawn, deliver, log=lambda *_: None, enabled=enabled)
    state.on_agents = m.check
    return state, m, calls, sent, done


QUOTA = {
    "label": "p@server0",
    "host": "server0",
    "dir": "/p",
    "account": "gmail",
    "state": "quota",
    "kind": "sdk",
    "retry": "continue daqui",
    "pid": 5,
}


def test_migrates_to_account_with_slack_and_resends_last_message():
    state, m, calls, sent, done = make([QUOTA])
    state.refresh_agents()  # the hook triggers the migration in a thread
    assert done.wait(5)
    assert calls == [("server0", "p@server0", "/p", "work", "resume", {"restart": True})]
    assert sent == [("p@server0", {"type": "user", "message": {"role": "user", "content": "continue daqui"}})]
    a = next(x for x in state.snapshot()["agents"] if x["label"] == "p@server0")
    assert a["account"] == "work" and a["state"] == "starting" and a["migrated_from"] == "gmail"
    assert m.history[-1]["from"] == "gmail" and m.history[-1]["to"] == "work"
    # the same query again does not migrate twice
    assert m.check([QUOTA]) == []


def test_no_account_with_slack_does_nothing():
    state, m, calls, sent, done = make([QUOTA])
    state.usage = {
        "gmail": {"5h": 99, "7d": 50, "7d_pace": 50},
        "work": {"5h": 99, "7d": 50, "7d_pace": 50},
    }
    assert m.migrate(dict(QUOTA)) is None and calls == [] and sent == []


def test_spawn_failure_keeps_agent_and_reports():
    state, m, calls, sent, done = make([dict(QUOTA, dir="/falha")])
    assert m.migrate(dict(QUOTA, dir="/falha")) is None
    assert sent == [] and m.history == []
    # the provisional entry does not stay: the next query to the machines says what is left
    assert "p@server0" not in state.provisional
    assert all(a["label"] != "p@server0" for a in state.snapshot()["agents"])


def test_tab_survives_while_reopening_the_agent():
    """Reopening takes seconds; in the meantime the page must keep seeing the agent (on the new
    account, 'opening…'), otherwise the tab disappears and the page jumps to another one."""
    state, m, calls, sent, done = make([QUOTA])
    seen = {}

    def spawn(host, label, cwd, account, mode, **kw):
        a = next(x for x in state.snapshot()["agents"] if x["label"] == label)
        seen.update(account=a["account"], state=a["state"], migrated_from=a.get("migrated_from"))
        # a query to the machines during the reopen returns the old agent as closed
        state.ext._agents = [dict(QUOTA, state="closed")]
        state.refresh_agents()
        return None

    m.spawn = spawn
    assert m.migrate(dict(QUOTA)) == "work"
    assert seen == {"account": "work", "state": "starting", "migrated_from": "gmail"}
    a = next(x for x in state.snapshot()["agents"] if x["label"] == "p@server0")
    assert a["account"] == "work" and a["state"] == "starting"


def test_disabled_and_ignores_other_states():
    state, m, calls, sent, done = make([QUOTA], enabled=False)
    assert m.check([QUOTA]) == []
    m.enabled = True
    assert m.check([dict(QUOTA, state="waiting"), dict(QUOTA, kind="term")]) == []
    assert m.check([dict(QUOTA, retry="")]) == ["p@server0"]
