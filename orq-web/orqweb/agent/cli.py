"""orq-agent: supervisor de um agente do Claude Code sem terminal, para a página do orq.

Uso:
  orq-agent spawn  --dir PASTA --account CONTA --label NOME [--mode resume|new]
                   [--role-file ARQ|-] [--parent RÓTULO] [--level NÍVEL]
                   [--permission-mode MODO] [--set CONJUNTO] [--pipeline RECEITA]
        garante o supervisor rodando numa sessão tmux "orqs-<nome>" desta máquina (sem tmux: nohup).
        Um nível de cima (professor etc.) sobe com o papel (--role-file; "-" lê do stdin e guarda em
        ~/.cache/orq/<nome>/role.txt), o rótulo do agente logo abaixo (--parent) e o nome do nível.
        Sem --parent/--level, --role-file é só um prompt extra do agente (os conjuntos da página usam).
        --permission-mode troca o modo do accounts.json só para este agente; --set marca de que
        conjunto ele veio; --pipeline, de que receita veio um nível. Tudo fica no state.json e vale de
        novo num --restart.
        --restart fecha o agente que já roda com esse rótulo e sobe de novo (ex.: para ganhar nome).
  orq-agent start  --dir PASTA --account CONTA --label NOME [--mode resume|new] [--role-file ARQ] …
        roda o supervisor em primeiro plano (é o que o spawn abre dentro do tmux). Todo agente sobe
        com `claude -n <nome saneado>`, para ser endereçável pelo SendMessage de outra sessão
  orq-agent attach NOME [--since N]
        liga stdin/stdout ao socket do agente: primeiro os eventos a partir de N, depois ao vivo;
        cada linha JSON do stdin vai para o claude. É o que o orq-web usa, local ou por ssh.
  orq-agent stop NOME          encerra o claude e o supervisor (a conversa fica salva)
  orq-agent list               agentes desta máquina com estado
  orq-agent link LOCAL REMOTO@MÁQUINA     vínculo: LOCAL (desta máquina) e REMOTO passam a se ver no
        ListAgents e a se falar pelo SendMessage, com o socket de cada um esticado por ssh até o outro
        e o registro de sessões espelhado (orqweb/agent/link.py). Fica numa sessão tmux "orql-<a>--<b>".
  orq-agent unlink LOCAL REMOTO@MÁQUINA   desfaz (apaga os espelhos nas duas máquinas, fecha o túnel)
  orq-agent links              vínculos desta máquina em JSON
  orq-agent link-run LOCAL REMOTO         o processo do vínculo em primeiro plano (é o que o link abre)
  orq-agent peer NOME [--probe PID | --mirror --sock S --from L | --unmirror PID --sock S]
        lado remoto do vínculo: imprime o cartão de sessão do agente NOME (com --probe, diz também se o
        socket encaminhado do pid dado já está de pé aqui), ou grava/apaga o espelho de um agente de
        outra máquina na pasta sessions/ dele. Erro vem como JSON {"err": …} (código 0: é resposta)
  orq-agent ls PASTA           arquivos da pasta em JSON [{name, size, mtime}], do mais novo ao mais antigo
  orq-agent cat ARQ [--range INI-FIM]
        manda uma linha JSON {size, start, length} e os bytes do arquivo (ou da faixa, fim inclusivo).
        ls e cat são como o orq-web lista e baixa a `_outbox/` do projeto, por ssh quando remota.
  (spawn/start com --mode resume recusam pasta onde um claude interativo está aberto; --force ignora)
  orq-agent convert NOME [--account CONTA]
        fecha um claude interativo que roda num tmux desta máquina (/exit, Ctrl-C, SIGTERM) e
        reabre a mesma conversa como agente da página; imprime JSON {ok, label, how, msg}

Estado em ~/.cache/orq/<nome>/: agent.sock, events.jsonl, state.json.

Todo agente da página sobe com uma nota no system prompt (OUTBOX_NOTE) dizendo que arquivos para o
usuário vão em `_outbox/` do projeto e que os enviados por ele chegam em `_inbox/`.
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
    "Esta sessão roda pela página do orq, sem terminal. Para compartilhar um arquivo com o usuário "
    "(PDF, imagem, vídeo, planilha, relatório…), copie-o para a pasta _outbox/ na raiz do projeto: a "
    "página lista o que está lá e oferece para baixar ou ver. Arquivos que o usuário envia pela página "
    "chegam em _inbox/ na raiz do projeto."
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
            print(f"não consegui ler o papel em {a.role_file}: {e}", file=sys.stderr)
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
        # reabrindo (troca de conta, ganhar nome): mantém papel, parent, nível, modo de permissão e
        # conjunto que o agente já tinha
        from .state import StateFile

        old = StateFile(config.state_dir(a.label) / "state.json").read()
        parent, level = old.get("parent"), old.get("level")
        permission, set_id = permission or old.get("permission"), set_id or old.get("set")
        pipeline = pipeline or old.get("pipeline")
        rf = config.state_dir(a.label) / "role.txt"
        if rf.exists():
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
                permission=permission,
                set_id=set_id,
                pipeline=pipeline,
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


def cmd_ls(a: argparse.Namespace) -> int:
    import json

    print(json.dumps(files.list_dir(Path(os.path.expanduser(a.dir))), ensure_ascii=False))
    return 0


def cmd_cat(a: argparse.Namespace) -> int:
    try:
        start, end = files.parse_range(a.range)
    except ValueError:
        print(f"faixa inválida: {a.range}", file=sys.stderr)
        return 2
    out = os.fdopen(sys.stdout.fileno(), "wb", closefd=False)
    try:
        return files.send(Path(os.path.expanduser(a.path)), out, start, end)
    except BrokenPipeError:  # quem pedia desistiu (a página fechou o download)
        return 1


def _agent_sessions_dir(label: str):
    """(pasta sessions/ da conta do agente, nome da sessão) ou (None, erro)."""
    try:
        return link.agent_sessions_dir(label), config.session_name(label)
    except RuntimeError as e:
        return None, str(e)


def cmd_peer(a: argparse.Namespace) -> int:
    """Lado remoto do vínculo. Sempre responde JSON numa linha; erro é {"err": …} com código 0, porque
    quem chama (o link-run, por ssh) lê a resposta, não o código."""
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
            return out({"err": f"espelho inválido: {e}"})
        return out({"ok": True, "paths": [str(p) for p in paths]})
    entry = link.wait_entry(name, [sessions])
    if entry is None:
        return out({"err": f"{a.label} não está rodando com nome {name}"})
    rec = entry.record()
    if a.probe:
        sock = Path(link.mirror_sock(entry.sock_dir, a.probe))
        rec["probe_alive"] = link.socket_alive(sock)
        if not rec["probe_alive"] and sock.exists():
            sock.unlink()  # sobra de um túnel que caiu: o -R do próximo recusa caminho que já existe
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
    return 0 if "desfeito" in msg else 1


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
        p.add_argument(
            "--permission-mode", help="modo de permissão deste agente (padrão: o do accounts.json)"
        )
        p.add_argument("--set", help="conjunto da página de que este agente veio (fica no state.json)")
        p.add_argument("--pipeline", help="receita de níveis de que este nível veio (fica no state.json)")
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
        p.add_argument("local", help="agente desta máquina")
        p.add_argument("remote", help="agente da outra máquina (rótulo com @máquina)")
        p.set_defaults(fn=fn)
    p = sub.add_parser("links")
    p.set_defaults(fn=cmd_links)
    p = sub.add_parser("peer")
    p.add_argument("label")
    p.add_argument("--mirror", action="store_true", help="grava o espelho lido do stdin")
    p.add_argument("--sock", help="caminho do socket encaminhado (com --mirror)")
    p.add_argument("--from", dest="origin", help="rótulo do agente de origem (com --mirror)")
    p.add_argument("--unmirror", type=int, metavar="PID", help="apaga o espelho desse pid (e --sock)")
    p.add_argument("--probe", type=int, metavar="PID", help="diz se o socket encaminhado desse pid vive")
    p.set_defaults(fn=cmd_peer)
    p = sub.add_parser("cat")
    p.add_argument("path")
    p.add_argument("--range", help="INI-FIM (fim inclusivo) ou INI-")
    p.set_defaults(fn=cmd_cat)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
