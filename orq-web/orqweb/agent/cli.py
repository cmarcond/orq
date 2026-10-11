"""orq-agent: supervisor de um agente do Claude Code sem terminal, para a página do orq.

Uso:
  orq-agent spawn  --dir PASTA --account CONTA --label NOME [--mode resume|new]
                   [--role-file ARQ|-] [--parent RÓTULO] [--level NÍVEL]
        garante o supervisor rodando numa sessão tmux "orqs-<nome>" desta máquina (sem tmux: nohup).
        Um nível de cima (professor etc.) sobe com o papel (--role-file; "-" lê do stdin e guarda em
        ~/.cache/orq/<nome>/role.txt), o rótulo do agente logo abaixo (--parent) e o nome do nível.
        --restart fecha o agente que já roda com esse rótulo e sobe de novo (ex.: para ganhar nome).
  orq-agent start  --dir PASTA --account CONTA --label NOME [--mode resume|new] [--role-file ARQ] …
        roda o supervisor em primeiro plano (é o que o spawn abre dentro do tmux). Todo agente sobe
        com `claude -n <nome saneado>`, para ser endereçável pelo SendMessage de outra sessão
  orq-agent attach NOME [--since N]
        liga stdin/stdout ao socket do agente: primeiro os eventos a partir de N, depois ao vivo;
        cada linha JSON do stdin vai para o claude. É o que o orq-web usa, local ou por ssh.
  orq-agent stop NOME          encerra o claude e o supervisor (a conversa fica salva)
  orq-agent list               agentes desta máquina com estado
  (spawn/start com --mode resume recusam pasta onde um claude interativo está aberto; --force ignora)
  orq-agent convert NOME [--account CONTA]
        fecha um claude interativo que roda num tmux desta máquina (/exit, Ctrl-C, SIGTERM) e
        reabre a mesma conversa como agente da página; imprime JSON {ok, label, how, msg}

Estado em ~/.cache/orq/<nome>/: agent.sock, events.jsonl, state.json.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

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
            print(f"não consegui ler o papel em {a.role_file}: {e}", file=sys.stderr)
            return 1
    sup = Supervisor(
        label=a.label,
        folder=folder,
        account=acc.name,
        command=claude.build_command(
            claude.find_claude(config.bin_dir()),
            conf.permission_mode,
            sid,
            name=config.session_name(a.label),
            system_prompt=role,
        ),
        env=acc.env(),
        state_dir=config.state_dir(a.label),
        sid=sid,
        transcript=transcript,
        name=config.session_name(a.label),
        parent=a.parent,
        level=a.level,
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
    if a.restart and not (role_file or parent or level):
        # reabrindo um nível (troca de conta): mantém papel, parent e nome do nível que ele já tinha
        from .state import StateFile

        old = StateFile(config.state_dir(a.label) / "state.json").read()
        parent, level = old.get("parent"), old.get("level")
        rf = config.state_dir(a.label) / "role.txt"
        if (parent or level) and rf.exists():
            role_file = str(rf)
    if role_file == "-":  # o papel vem pelo stdin (é como o servidor manda por ssh) e fica no estado
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
            )
        )
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
    """Retomar uma pasta onde um claude interativo está aberto põe dois processos na mesma conversa."""
    if mode != "resume":
        return None
    live = convert.live_terminal_in_folder(_status(), os.path.expanduser(folder))
    if not live:
        return None
    names = ", ".join(a.get("tmux", "?") for a in live)
    return f"há um claude aberto no terminal nesta pasta (tmux {names}). Converta-o ou feche-o antes."


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
        p.add_argument(
            "--force", action="store_true", help="retoma mesmo com um claude interativo aberto na pasta"
        )
        p.add_argument(
            "--role-file", help="papel do nível (--append-system-prompt); no spawn, '-' lê do stdin"
        )
        p.add_argument("--parent", help="nível de cima: rótulo do agente logo abaixo")
        p.add_argument("--level", help="nome do nível na receita (professor, arquiteto…)")
        p.add_argument(
            "--restart", action="store_true", help="fecha o agente que estiver rodando e sobe de novo"
        )
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
