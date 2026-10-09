"""orq-agent: supervisor de um agente do Claude Code sem terminal, para a página do orq.

Uso:
  orq-agent spawn  --dir PASTA --account CONTA --label NOME [--mode resume|new]
        garante o supervisor rodando numa sessão tmux "orqs-<nome>" desta máquina (sem tmux: nohup)
  orq-agent start  --dir PASTA --account CONTA --label NOME [--mode resume|new]
        roda o supervisor em primeiro plano (é o que o spawn abre dentro do tmux)
  orq-agent attach NOME [--since N]
        liga stdin/stdout ao socket do agente: primeiro os eventos a partir de N, depois ao vivo;
        cada linha JSON do stdin vai para o claude. É o que o orq-web usa, local ou por ssh.
  orq-agent stop NOME          encerra o claude e o supervisor (a conversa fica salva)
  orq-agent list               agentes desta máquina com estado
  orq-agent convert NOME [--account CONTA]
        fecha um claude interativo que roda num tmux desta máquina (/exit, Ctrl-C, SIGTERM) e
        reabre a mesma conversa como agente da página; imprime JSON {ok, label, how, msg}

Estado em ~/.cache/orq/<nome>/: agent.sock, events.jsonl, state.json.
"""

import argparse
import os
import sys
from pathlib import Path

from .. import config
from . import claude, client, convert, events
from .state import list_agents
from .supervisor import Supervisor


def cmd_start(a: argparse.Namespace) -> int:
    conf = config.load()
    try:
        acc = conf.account(a.account)
    except KeyError as e:
        print(e.args[0], file=sys.stderr)
        return 1
    folder = Path(os.path.expanduser(a.dir))
    sid = claude.pick_session(config.bin_dir(), acc.name, folder) if a.mode == "resume" else None
    transcript = events.project_dir(acc.config_dir, folder) / f"{sid}.jsonl" if sid else None
    sup = Supervisor(
        label=a.label,
        folder=folder,
        account=acc.name,
        command=claude.build_command(claude.find_claude(config.bin_dir()), conf.permission_mode, sid),
        env=acc.env(),
        state_dir=config.state_dir(a.label),
        sid=sid,
        transcript=transcript,
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
    try:
        print(client.spawn(a.label, a.dir, a.account, a.mode))
        print("pronto")
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_attach(a: argparse.Namespace) -> int:
    out = os.fdopen(sys.stdout.fileno(), "wb", 0)
    return client.attach(config.state_dir(a.label) / "agent.sock", a.since, sys.stdin.buffer, out)


def cmd_stop(a: argparse.Namespace) -> int:
    if client.stop(config.state_dir(a.label) / "agent.sock"):
        print("pedido de encerramento enviado")
        return 0
    print(f"agente {a.label} não está rodando", file=sys.stderr)
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
        print(json.dumps({"ok": False, "label": a.label, "how": "", "msg": f"orq --status-json falhou: {e}"}))
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
                    "msg": "não há claude interativo em tmux com esse nome",
                }
            )
        )
        return 1
    if a.account:
        entry["account"] = a.account
    res = convert.convert(entry, host, client.spawn)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["ok"] else 1


def cmd_list(_: argparse.Namespace) -> int:
    for st in list_agents(config.state_root()):
        print(
            f"{st.get('label', '?'):<32} {st.get('account', '?'):<10} {st.get('state', '?'):<10}",
            st.get("dir", ""),
        )
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
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
