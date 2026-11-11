"""orq-agent: supervisor of one Claude Code agent without a terminal, for the orq page.

Usage:
  orq-agent spawn  --dir FOLDER --account ACCOUNT --label NAME [--mode resume|new]
                   [--role-file FILE|-] [--parent LABEL] [--level LEVEL]
                   [--permission-mode MODE] [--set SET] [--pipeline RECIPE]
        makes sure the supervisor is running in a tmux session "orqs-<name>" on this machine (without
        tmux: nohup). An upper level (teacher etc.) starts with the role (--role-file; "-" reads from
        stdin and stores it in ~/.cache/orq/<name>/role.txt), the label of the agent right below
        (--parent) and the name of the level.
        Without --parent/--level, --role-file is just an extra prompt for the agent (the page's sets use it).
        --permission-mode replaces the mode of accounts.json for this agent only; --set marks which
        set it came from; --pipeline, which recipe a level came from. All of it is kept in state.json and
        applies again on a --restart.
        --restart closes the agent already running with this label and starts it again (e.g. to get a name).
  orq-agent start  --dir FOLDER --account ACCOUNT --label NAME [--mode resume|new] [--role-file FILE] …
        runs the supervisor in the foreground (it is what spawn opens inside tmux). Every agent starts
        with `claude -n <sanitized name>`, so it can be addressed by SendMessage from another session
  orq-agent attach NAME [--since N]
        connects stdin/stdout to the agent's socket: first the events from N on, then live;
        each JSON line from stdin goes to claude. It is what orq-web uses, locally or over ssh.
  orq-agent stop NAME          shuts down claude and the supervisor (the conversation stays saved)
  orq-agent list               agents of this machine with their state
  orq-agent link LOCAL REMOTE@MACHINE     link: LOCAL (of this machine) and REMOTE start seeing each other
        in ListAgents and talking through SendMessage, with each one's socket stretched over ssh to the
        other and the session registry mirrored (orqweb/agent/link.py). It lives in a tmux session
        "orql-<a>--<b>".
  orq-agent unlink LOCAL REMOTE@MACHINE   undoes it (deletes the mirrors on both machines, closes the tunnel)
  orq-agent links              links of this machine as JSON
  orq-agent link-run LOCAL REMOTE         the link's process in the foreground (it is what link opens)
  orq-agent peer NAME [--probe PID | --mirror --sock S --from L | --unmirror PID --sock S]
        remote side of the link: prints the session card of agent NAME (with --probe, also says whether
        the forwarded socket of the given pid is already up here), or writes/deletes the mirror of an
        agent from another machine in its sessions/ folder. An error comes as JSON {"err": …} (exit
        code 0: it is a response)
  orq-agent ls FOLDER          files of the folder as JSON [{name, size, mtime}], newest to oldest
  orq-agent cat FILE [--range START-END]
        sends a JSON line {size, start, length} and the bytes of the file (or of the range, end inclusive).
        ls and cat are how orq-web lists and downloads the project's `_outbox/`, over ssh when remote.
  (spawn/start with --mode resume refuse a folder where an interactive claude is open; --force ignores it)
  orq-agent convert NAME [--account ACCOUNT]
        closes an interactive claude running in a tmux on this machine (/exit, Ctrl-C, SIGTERM) and
        reopens the same conversation as a page agent; prints JSON {ok, label, how, msg}

State in ~/.cache/orq/<name>/: agent.sock, events.jsonl, state.json.

Every page agent starts with a note in the system prompt (OUTBOX_NOTE) saying that files for the
user go in the project's `_outbox/` and that the ones the user sends arrive in `_inbox/`.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from .. import config
from . import claude, client, convert, events, files, link
from .state import list_agents
from .supervisor import Supervisor

OUTBOX_NOTE = (
    "This session runs through the orq page, without a terminal. To share a file with the user "
    "(PDF, image, video, spreadsheet, report…), copy it to the _outbox/ folder at the project root: the "
    "page lists what is there and offers to download or view it. Files the user sends through the page "
    "arrive in _inbox/ at the project root."
)


def cmd_start(a: argparse.Namespace) -> int:
    conf = config.load()
    try:
        acc = conf.account(a.account)
    except KeyError as e:
        print(e.args[0], file=sys.stderr)
        return 1
    folder = Path(os.path.expanduser(a.dir))
    if not a.force:
        err = _refuse_if_terminal_live(str(folder), a.mode)
        if err:
            print(err, file=sys.stderr)
            return 1
    sid = claude.pick_session(config.bin_dir(), acc.name, folder) if a.mode == "resume" else None
    transcript = events.project_dir(acc.config_dir, folder) / f"{sid}.jsonl" if sid else None
    role = None
    if a.role_file:
        try:
            role = Path(a.role_file).read_text().strip() or None
        except OSError as e:
            print(f"could not read the role in {a.role_file}: {e}", file=sys.stderr)
            return 1
    sup = Supervisor(
        label=a.label,
        folder=folder,
        account=acc.name,
        command=claude.build_command(
            claude.find_claude(config.bin_dir()),
            a.permission_mode or conf.permission_mode,
            sid,
            name=config.session_name(a.label),
            system_prompt=OUTBOX_NOTE + ("\n\n" + role if role else ""),
        ),
        env=acc.env(),
        state_dir=config.state_dir(a.label),
        sid=sid,
        transcript=transcript,
        name=config.session_name(a.label),
        parent=a.parent,
        level=a.level,
        permission=a.permission_mode or conf.permission_mode,
        role=role,
        set_id=a.set,
        pipeline=a.pipeline,
    )
    try:
        sup.run()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 0
    return 0


def cmd_spawn(a: argparse.Namespace) -> int:
    conf = config.load()
    try:
        conf.account(a.account)
    except KeyError as e:
        print(e.args[0], file=sys.stderr)
        return 1
    if not a.force:
        err = _refuse_if_terminal_live(a.dir, a.mode)
        if err:
            print(err, file=sys.stderr)
            return 1
    role_file, parent, level = a.role_file, a.parent, a.level
    permission, set_id, pipeline = a.permission_mode, a.set, a.pipeline
    if a.restart and not (role_file or parent or level):
        # reopening (account switch, getting a name): keeps the role, parent, level, permission mode and
        # set the agent already had
        from .state import StateFile

        old = StateFile(config.state_dir(a.label) / "state.json").read()
        parent, level = old.get("parent"), old.get("level")
        permission, set_id = permission or old.get("permission"), set_id or old.get("set")
        pipeline = pipeline or old.get("pipeline")
        rf = config.state_dir(a.label) / "role.txt"
        if rf.exists():
            role_file = str(rf)
    if role_file == "-":  # role comes via stdin (how the server sends it over ssh) and stays in the state
        text = sys.stdin.read()
        path = config.state_dir(a.label) / "role.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        role_file = str(path)
    try:
        print(
            client.spawn(
                a.label,
                a.dir,
                a.account,
                a.mode,
                role_file=role_file,
                parent=parent,
                level=level,
                restart=a.restart,
                permission=permission,
                set_id=set_id,
                pipeline=pipeline,
            )
        )
        print("ready")
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_attach(a: argparse.Namespace) -> int:
    out = os.fdopen(sys.stdout.fileno(), "wb", 0)
    return client.attach(config.state_dir(a.label) / "agent.sock", a.since, sys.stdin.buffer, out)


def cmd_stop(a: argparse.Namespace) -> int:
    if client.stop(config.state_dir(a.label) / "agent.sock"):
        print("shutdown request sent")
        return 0
    print(f"agent {a.label} is not running", file=sys.stderr)
    return 1


def cmd_convert(a: argparse.Namespace) -> int:
    import json
    import socket
    import subprocess

    host = a.label.split("@", 1)[1] if "@" in a.label else socket.gethostname().split(".")[0]
    try:
        r = subprocess.run(
            [str(config.bin_dir() / "orq"), "--status-json"], capture_output=True, text=True, timeout=30
        )
        status = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"ok": False, "label": a.label, "how": "", "msg": f"orq --status-json failed: {e}"}))
        return 1
    base = a.label.split("@")[0]
    entry = next((e for e in convert.terminal_sessions(status) if e.get("label") in (a.label, base)), None)
    if entry is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "label": a.label,
                    "how": "",
                    "msg": "there is no interactive claude in tmux with that name",
                }
            )
        )
        return 1
    if a.account:
        entry["account"] = a.account
    res = convert.convert(entry, host, client.spawn, others=convert.others_in_folder(status, entry))
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["ok"] else 1


def _status() -> list:
    import json
    import subprocess

    try:
        r = subprocess.run(
            [str(config.bin_dir() / "orq"), "--status-json"], capture_output=True, text=True, timeout=30
        )
        return json.loads(r.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def _refuse_if_terminal_live(folder: str, mode: str) -> Optional[str]:
    """Resuming a folder where an interactive claude is open puts two processes in the same conversation."""
    if mode != "resume":
        return None
    live = convert.live_terminal_in_folder(_status(), os.path.expanduser(folder))
    if not live:
        return None
    names = ", ".join(a.get("tmux", "?") for a in live)
    return (
        f"there is a claude open in the terminal in this folder (tmux {names}). Convert it or close it first."
    )


def cmd_list(_: argparse.Namespace) -> int:
    for st in list_agents(config.state_root()):
        print(
            f"{st.get('label', '?'):<32} {st.get('account', '?'):<10} {st.get('state', '?'):<10}",
            st.get("dir", ""),
        )
    return 0


def cmd_ls(a: argparse.Namespace) -> int:
    import json

    print(json.dumps(files.list_dir(Path(os.path.expanduser(a.dir))), ensure_ascii=False))
    return 0


def cmd_cat(a: argparse.Namespace) -> int:
    try:
        start, end = files.parse_range(a.range)
    except ValueError:
        print(f"invalid range: {a.range}", file=sys.stderr)
        return 2
    out = os.fdopen(sys.stdout.fileno(), "wb", closefd=False)
    try:
        return files.send(Path(os.path.expanduser(a.path)), out, start, end)
    except BrokenPipeError:  # whoever was asking gave up (the page closed the download)
        return 1


def _agent_sessions_dir(label: str):
    """(sessions/ folder of the agent's account, session name) or (None, error)."""
    try:
        return link.agent_sessions_dir(label), config.session_name(label)
    except RuntimeError as e:
        return None, str(e)


def cmd_peer(a: argparse.Namespace) -> int:
    """Remote side of the link. Always answers JSON on one line; an error is {"err": …} with exit code 0,
    because the caller (link-run, over ssh) reads the response, not the code."""
    import json

    def out(obj: dict) -> int:
        print(json.dumps(obj, ensure_ascii=False))
        return 0

    sessions, name = _agent_sessions_dir(a.label)
    if sessions is None:
        return out({"err": name})
    if a.unmirror:
        link.remove_mirror(sessions, a.unmirror, a.sock)
        return out({"ok": True})
    if a.mirror:
        try:
            record = json.loads(sys.stdin.read())
            paths = link.write_mirror(sessions, record, a.sock, a.origin or "?")
        except (ValueError, KeyError, OSError, RuntimeError) as e:
            return out({"err": f"invalid mirror: {e}"})
        return out({"ok": True, "paths": [str(p) for p in paths]})
    entry = link.wait_entry(name, [sessions])
    if entry is None:
        return out({"err": f"{a.label} is not running under the name {name}"})
    rec = entry.record()
    if a.probe:
        sock = Path(link.mirror_sock(entry.sock_dir, a.probe))
        rec["probe_alive"] = link.socket_alive(sock)
        if not rec["probe_alive"] and sock.exists():
            sock.unlink()  # leftover of a dropped tunnel: the next -R refuses a path that already exists
    return out(rec)


def cmd_link(a: argparse.Namespace) -> int:
    try:
        print(client.spawn_link(a.local, a.remote))
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_link_run(a: argparse.Namespace) -> int:
    return link.run_link(a.local, a.remote)


def cmd_unlink(a: argparse.Namespace) -> int:
    msg = client.unlink(a.local, a.remote)
    print(msg)
    return 0 if "undone" in msg else 1


def cmd_links(_: argparse.Namespace) -> int:
    import json

    print(json.dumps(link.list_links(), ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="orq-agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("spawn", cmd_spawn), ("start", cmd_start)):
        p = sub.add_parser(name)
        p.add_argument("--dir", required=True)
        p.add_argument("--account", required=True)
        p.add_argument("--label", required=True)
        p.add_argument("--mode", choices=("resume", "new"), default="resume")
        p.add_argument(
            "--force", action="store_true", help="resumes even with an interactive claude open in the folder"
        )
        p.add_argument(
            "--role-file", help="role of the level (--append-system-prompt); in spawn, '-' reads from stdin"
        )
        p.add_argument("--parent", help="upper level: label of the agent right below")
        p.add_argument("--level", help="name of the level in the recipe (professor, arquiteto…)")
        p.add_argument(
            "--restart", action="store_true", help="closes the agent that is running and starts it again"
        )
        p.add_argument(
            "--permission-mode", help="permission mode of this agent (default: the one in accounts.json)"
        )
        p.add_argument("--set", help="set of the page this agent came from (kept in state.json)")
        p.add_argument("--pipeline", help="recipe of levels this level came from (kept in state.json)")
        p.set_defaults(fn=fn)
    p = sub.add_parser("attach")
    p.add_argument("label")
    p.add_argument("--since", type=int, default=0)
    p.set_defaults(fn=cmd_attach)
    p = sub.add_parser("stop")
    p.add_argument("label")
    p.set_defaults(fn=cmd_stop)
    p = sub.add_parser("list")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("convert")
    p.add_argument("label")
    p.add_argument("--account")
    p.set_defaults(fn=cmd_convert)
    p = sub.add_parser("ls")
    p.add_argument("dir")
    p.set_defaults(fn=cmd_ls)
    for name, fn in (("link", cmd_link), ("link-run", cmd_link_run), ("unlink", cmd_unlink)):
        p = sub.add_parser(name)
        p.add_argument("local", help="agent of this machine")
        p.add_argument("remote", help="agent of the other machine (label with @machine)")
        p.set_defaults(fn=fn)
    p = sub.add_parser("links")
    p.set_defaults(fn=cmd_links)
    p = sub.add_parser("peer")
    p.add_argument("label")
    p.add_argument("--mirror", action="store_true", help="writes the mirror read from stdin")
    p.add_argument("--sock", help="path of the forwarded socket (with --mirror)")
    p.add_argument("--from", dest="origin", help="label of the source agent (with --mirror)")
    p.add_argument("--unmirror", type=int, metavar="PID", help="deletes the mirror of this pid (and --sock)")
    p.add_argument(
        "--probe", type=int, metavar="PID", help="says whether the forwarded socket of this pid is alive"
    )
    p.set_defaults(fn=cmd_peer)
    p = sub.add_parser("cat")
    p.add_argument("path")
    p.add_argument("--range", help="START-END (end inclusive) or START-")
    p.set_defaults(fn=cmd_cat)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
