from orqweb.server import assign as A


def u(sevenday, pace, fivehour=0):
    return {"5h": fivehour, "7d": sevenday, "7d_pace": pace}


def test_exhausted():
    assert A.exhausted(None) and A.exhausted({}) and A.exhausted({"err": "x"})
    assert A.exhausted(u(95, 10)) and A.exhausted(u(10, 10, fivehour=96))
    assert not A.exhausted(u(94, 10))


def test_slack_sign():
    assert A.slack(u(30, 50)) == -20  # below the pace = slack
    assert A.slack(u(60, 50)) == 10


def test_assign_prefers_account_with_more_slack():
    usage = {"a": u(50, 50), "b": u(10, 50)}
    (p,) = A.assign([{"cwd": "/x", "account": None}], usage, ["a", "b"])
    assert p["acc"] == "b" and p["why"].startswith("most slack")


def test_assign_stays_on_conversation_account_when_close():
    usage = {"a": u(50, 50), "b": u(45, 50)}  # b has 5 more points of slack, less than STAY
    (p,) = A.assign([{"cwd": "/x", "account": "a"}], usage, ["a", "b"])
    assert p["acc"] == "a" and p["why"] == "the conversation's"


def test_assign_moves_when_conversation_account_is_exhausted():
    usage = {"a": u(97, 50), "b": u(10, 50)}
    (p,) = A.assign([{"cwd": "/x", "account": "a"}], usage, ["a", "b"])
    assert p["acc"] == "b" and p["why"] == "a out of quota"


def test_running_agents_weigh_against_account():
    usage = {"a": u(10, 50), "b": u(20, 50)}
    (p,) = A.assign([{"cwd": "/x", "account": "a"}], usage, ["a", "b"], running=["a", "a"])
    assert p["acc"] == "b" and "already has 2 agent(s)" in p["why"]


def test_spreads_two_projects_over_two_accounts():
    usage = {"a": u(10, 50), "b": u(12, 50)}
    ps = A.assign([{"cwd": "/x"}, {"cwd": "/y"}], usage, ["a", "b"])
    assert {p["acc"] for p in ps} == {"a", "b"}


def test_no_healthy_account():
    usage = {"a": u(99, 50)}
    (p,) = A.assign([{"cwd": "/x", "account": "a"}], usage, ["a"])
    assert p["acc"] == "a" and p["why"] == "no account with slack"


def test_preassigned_is_kept_and_counts():
    usage = {"a": u(10, 50), "b": u(10, 50)}
    ps = A.assign([{"cwd": "/x", "acc": "a"}, {"cwd": "/y"}], usage, ["a", "b"])
    assert ps[0]["acc"] == "a" and ps[1]["acc"] == "b"


def test_label_for():
    assert A.label_for("/home/c/acervo/", "node0", set()) == "acervo@node0"
    assert A.label_for("/home/c/acervo", "node0", {"acervo@node0"}) == "acervo-2@node0"
    assert A.label_for("/", "laptop", set()) == "home@laptop"
