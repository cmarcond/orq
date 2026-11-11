"""_outbox/: listing the folder and sending bytes (orq-agent ls/cat), end to end through the CLI."""

import io
import json
import os
import time

import pytest

from orqweb.agent import cli, files


def test_list_dir_newest_first_skips_hidden_and_folders(tmp_path):
    (tmp_path / "old.txt").write_bytes(b"a")
    os.utime(tmp_path / "old.txt", (1000, 1000))
    (tmp_path / "new.pdf").write_bytes(b"ab")
    os.utime(tmp_path / "new.pdf", (2000, 2000))
    (tmp_path / ".hidden").write_bytes(b"x")
    (tmp_path / "folder").mkdir()
    assert files.list_dir(tmp_path) == [
        {"name": "new.pdf", "size": 2, "mtime": 2000},
        {"name": "old.txt", "size": 1, "mtime": 1000},
    ]
    assert files.list_dir(tmp_path / "missing") == []


def test_open_range_and_send(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"0123456789")
    size, length, f = files.open_range(p)
    assert (size, length, f.read()) == (10, 10, b"0123456789")
    size, length, f = files.open_range(p, 2, 5)  # whoever reads respects `length`
    assert (size, length, f.read(length)) == (10, 4, b"2345")
    size, length, f = files.open_range(p, 7, None)
    assert (size, length, f.read(length)) == (10, 3, b"789")
    size, length, f = files.open_range(p, 8, 99)  # end beyond the file: cut
    assert (length, f.read(length)) == (2, b"89")
    with pytest.raises(ValueError):
        files.open_range(p, 10)
    out = io.BytesIO()
    assert files.send(p, out, 2, 5) == 0
    head, body = out.getvalue().split(b"\n", 1)
    assert json.loads(head) == {"size": 10, "start": 2, "length": 4} and body == b"2345"
    out = io.BytesIO()
    assert files.send(tmp_path / "x", out) == 1 and "err" in json.loads(out.getvalue())
    assert files.read_header(io.BytesIO(b"")) == {"err": "no response from the machine"}
    assert "err" in files.read_header(io.BytesIO(b"garbage\n"))
    assert files.parse_range("5-9") == (5, 9) and files.parse_range("5-") == (5, None)
    assert files.parse_range(None) == (0, None)


def test_cli_ls_and_cat(tmp_path, capfdbinary):
    (tmp_path / "r.md").write_bytes(b"# oi\n")
    assert cli.main(["ls", str(tmp_path)]) == 0
    out = capfdbinary.readouterr().out
    (item,) = json.loads(out)
    assert item["name"] == "r.md" and item["size"] == 5 and abs(item["mtime"] - time.time()) < 5
    assert cli.main(["cat", str(tmp_path / "r.md"), "--range", "2-3"]) == 0
    out = capfdbinary.readouterr().out
    assert out == b'{"size": 5, "start": 2, "length": 2}\noi'
    assert cli.main(["cat", str(tmp_path / "nada")]) == 1
    assert "err" in json.loads(capfdbinary.readouterr().out)
    assert cli.main(["cat", str(tmp_path / "r.md"), "--range", "x"]) == 2


def test_every_page_agent_learns_about_the_outbox(tmp_path, monkeypatch):
    """The system prompt carries the _outbox/ note, before the level's role when there is one."""
    seen = {}

    class Sup:
        def __init__(self, **kw):
            seen.update(kw)

        def run(self):
            pass

    monkeypatch.setattr(cli, "Supervisor", Sup)
    monkeypatch.setattr(cli.claude, "find_claude", lambda b: "/x/claude")
    monkeypatch.setattr(cli.claude, "pick_session", lambda *a: None)
    monkeypatch.setattr(cli, "_refuse_if_terminal_live", lambda *a: None)
    monkeypatch.setattr(
        cli.config, "load", lambda: cli.config.parse({"accounts": [{"name": "g", "config_dir": "~/.c"}]})
    )
    role = tmp_path / "role.txt"
    role.write_text("You are the teacher.")
    for args, want in (
        ([], cli.OUTBOX_NOTE),
        (["--role-file", str(role)], cli.OUTBOX_NOTE + "\n\nYou are the teacher."),
    ):
        assert (
            cli.main(["start", "--dir", str(tmp_path), "--account", "g", "--label", "p@laptop"] + args) == 0
        )
        cmd = seen["command"]
        assert cmd[cmd.index("--append-system-prompt") + 1] == want
    assert "_outbox/" in cli.OUTBOX_NOTE and "_inbox/" in cli.OUTBOX_NOTE
