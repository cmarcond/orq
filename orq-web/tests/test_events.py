import json

from orqweb.agent import events as ev


def user(text, **extra):
    e = {"type": "user", "message": {"role": "user", "content": text}}
    e.update(extra)
    return e


class TestEventLog:
    def test_append_returns_sequence_and_since_replays(self, tmp_path):
        log = ev.EventLog(tmp_path / "events.jsonl")
        assert log.append(b'{"a":1}\n') == 1
        assert log.append(b'{"a":2}\n') == 2
        assert log.since(0) == [b'{"a":1}\n', b'{"a":2}\n']
        assert log.since(1) == [b'{"a":2}\n']
        assert log.since(5) == []
        assert log.since(-3) == log.since(0)

    def test_persists_to_disk(self, tmp_path):
        p = tmp_path / "events.jsonl"
        log = ev.EventLog(p)
        log.append(b'{"a":1}\n')
        log.append(b'{"a":2}\n')
        assert p.read_bytes().count(b"\n") == 2

    def test_memory_only(self):
        log = ev.EventLog()
        log.append(b"x\n")
        assert len(log) == 1


class TestStampOrigin:
    def test_page_echo_is_stamped_page_and_consumed(self):
        sent = ev.SentTexts()
        sent.add("oi")
        e = ev.stamp_origin(user("oi"), sent)
        assert e["orq_origin"] == "page"
        # segunda ocorrência igual não é mais nossa
        assert ev.stamp_origin(user("oi"), sent).get("orq_origin") == "injected"

    def test_unmarked_message_is_injected(self):
        assert ev.stamp_origin(user("aviso do cron"), ev.SentTexts())["orq_origin"] == "injected"

    def test_human_typed_is_not_stamped(self):
        e = ev.stamp_origin(user("digitei", origin={"kind": "human"}, promptSource="typed"), ev.SentTexts())
        assert "orq_origin" not in e

    def test_sdk_source_is_not_stamped(self):
        e = ev.stamp_origin(user("pela página", promptSource="sdk"), ev.SentTexts())
        assert "orq_origin" not in e

    def test_tool_result_and_meta_untouched(self):
        tr = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]},
        }
        assert "orq_origin" not in ev.stamp_origin(tr, ev.SentTexts())
        assert "orq_origin" not in ev.stamp_origin(user("meta", isMeta=True), ev.SentTexts())

    def test_peer_message_is_stamped_agent_even_if_text_was_sent(self):
        sent = ev.SentTexts()
        sent.add("PONG")
        e = ev.stamp_origin(
            user(
                "<cross-session-message>PONG</cross-session-message>",
                origin={"kind": "peer", "name": "exe", "body": "PONG"},
            ),
            sent,
        )
        assert e["orq_origin"] == "agent" and e["origin"]["name"] == "exe"

    def test_text_blocks_count_as_text(self):
        e = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "bloco"}]}}
        assert ev.stamp_origin(e, ev.SentTexts())["orq_origin"] == "injected"


class TestDeriveState:
    def test_permission_request(self):
        e = {
            "type": "control_request",
            "request_id": "r1",
            "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls -la"}},
        }
        assert ev.derive_state(e) == ("permission", "Bash ls -la")

    def test_result_ok_and_error(self):
        assert ev.derive_state({"type": "result", "subtype": "success"}) == ("waiting", "")
        st, msg = ev.derive_state({"type": "result", "is_error": True, "result": "deu erro"})
        assert st == "error" and msg == "deu erro"

    def test_assistant_is_working_and_others_do_not_change(self):
        assert ev.derive_state({"type": "assistant"}) == ("working", "")
        assert ev.derive_state({"type": "stream_event"}) is None
        assert ev.derive_state({"type": "system", "subtype": "init"}) is None

    def test_quota_exhausted(self):
        rl = {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour"},
        }
        assert ev.quota_exhausted(rl) and ev.derive_state(rl) == ("quota", "cota esgotada (five_hour)")
        assert not ev.quota_exhausted({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}})
        err = {"type": "result", "is_error": True, "result": "You've hit your usage limit · resets 3pm"}
        assert ev.derive_state(err)[0] == "quota"
        other = {"type": "result", "is_error": True, "result": "tool failed"}
        assert ev.derive_state(other) == ("error", "tool failed")

    def test_client_side(self):
        assert ev.derive_state_from_client({"type": "user"}) == ("working", "")
        assert ev.derive_state_from_client({"type": "control_response"}) == ("working", "")
        assert ev.derive_state_from_client({"orq": "quit"}) is None


class TestPermissionAnswered:
    def test_builds_event(self):
        obj = {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": "r1", "response": {"behavior": "deny"}},
        }
        e = json.loads(ev.permission_answered(obj))
        assert e["type"] == "orq" and e["subtype"] == "permission_answered"
        assert e["request_id"] == "r1" and e["behavior"] == "deny"

    def test_ignores_other_messages(self):
        assert ev.permission_answered({"type": "user"}) is None


class TestTranscript:
    def test_project_dir_sanitizes_path(self, tmp_path):
        d = ev.project_dir(tmp_path, tmp_path / "Projects" / "x y")
        assert d.parent == tmp_path / "projects"
        assert d.name == re_sub(str(tmp_path / "Projects" / "x y"))

    def test_entries_filter_sidechain_and_other_types_and_cap(self, tmp_path):
        p = tmp_path / "s.jsonl"
        lines = [
            {
                "type": "user",
                "message": {"role": "user", "content": "oi"},
                "uuid": "1",
                "origin": {"kind": "human"},
            },
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "olá"}]},
            },
            {"type": "assistant", "isSidechain": True, "message": {"content": []}},
            {"type": "queue-operation"},
            {"type": "user", "message": {"role": "user", "content": "x"}, "isMeta": True},
        ]
        p.write_text("\n".join(json.dumps(x) for x in lines) + "\nlixo não json\n")
        out = ev.transcript_entries(p)
        assert [o["type"] for o in out] == ["user", "assistant", "user"]
        assert all(o["replay"] for o in out)
        assert out[0]["origin"] == {"kind": "human"} and out[2]["isMeta"] is True
        assert ev.transcript_entries(p, limit=1)[0]["isMeta"] is True

    def test_missing_file(self, tmp_path):
        assert ev.transcript_entries(tmp_path / "nao-existe.jsonl") == []


def re_sub(s):
    import re

    return re.sub(r"[^a-zA-Z0-9]", "-", s)


def test_text_of():
    assert ev.text_of({"content": "a"}) == "a"
    assert (
        ev.text_of(
            {"content": [{"type": "text", "text": "a"}, {"type": "tool_use"}, {"type": "text", "text": "b"}]}
        )
        == "a\nb"
    )
    assert ev.text_of(None) == ""
