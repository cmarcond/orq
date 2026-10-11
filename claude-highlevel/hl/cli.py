"""`python -m hl run` — o pipeline no terminal, colorido, antes de existir interface."""
from __future__ import annotations

import argparse
import queue
import re
import signal
import sys
import textwrap
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from .events import Event
from .orchestrator import Run
from .bus import Bus
from . import pipelines

RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
RED, GREY = "\033[31m", "\033[90m"
WIDTH = 96
LIVE_KINDS = {"narration", "wire_in", "wire_out", "prompt", "activity", "activity_done", "thinking"}


def _c(text: str, color: str) -> str:
    return f"\033[{color}m{text}{RESET}"


def _wrap(text: str, indent: str = "  ") -> str:
    out = []
    for para in text.strip().split("\n"):
        out.append(textwrap.fill(para, WIDTH, initial_indent=indent,
                                 subsequent_indent=indent) if para.strip() else "")
    return "\n".join(out)


def _fingerprint(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:120]


class Console:
    def __init__(self, colors: dict[str, str], show: set[str]) -> None:
        self.colors = colors
        self.show = show
        # Uma mensagem entre níveis aparece nos dois transcripts: como wire_out no de
        # quem mandou e como wire_in no de quem recebeu. O corpo é o mesmo, então o
        # segundo virou eco. Guardamos o que já foi mostrado para colapsar o eco.
        self._sent: deque[str] = deque(maxlen=32)

    def status(self, who: str, message: str) -> None:
        print(f"{DIM}{datetime.now():%H:%M:%S}{RESET} {_c(who, self.colors.get(who, '37'))} {DIM}{message}{RESET}")

    def blocked(self, who: str, block) -> None:
        print(f"\n{RED}{BOLD}▌ {who} está BLOQUEADO · {block.hint}{RESET}")
        for number, label in block.options:
            print(f"{RED}▌{RESET}   {number}. {label}")
        print(f"{RED}▌{RESET} {DIM}responda no painel; na fase 1, com --auto-allow{RESET}\n")

    def event(self, event: Event) -> None:
        color = self.colors.get(event.session, "37")
        kind, data = event.kind, event.payload

        if kind == "narration":
            print(f"\n{_c('▌ ' + event.session, color)}")
            print(_wrap(data["text"]))
        elif kind == "wire_out":
            self._sent.append(_fingerprint(data["message"]))
            print(f"\n{_c('→ ' + event.session, color)} {DIM}manda para{RESET} "
                  f"{_c(data['to'], self.colors.get(data['to'], '35'))} {DIM}· {data['summary']}{RESET}")
            if "fios" in self.show:
                print(f"{DIM}{_wrap(data['message'][:600])}{RESET}")
        elif kind == "wire_in":
            sender = data["from"]
            head = _c("←", self.colors.get(sender, "35"))
            if _fingerprint(data["body"]) in self._sent:
                print(f"{head} {DIM}{event.session} recebeu a mensagem de {sender}{RESET}")
                return
            print(f"\n{head} {_c(sender, self.colors.get(sender, '35'))} "
                  f"{DIM}→ {event.session}{RESET}")
            print(f"{DIM}{_wrap(data['body'])}{RESET}")
        elif kind == "prompt":
            print(f"\n{BOLD}❯ {data['text'].strip()[:400]}{RESET}")
        elif kind == "activity" and "bastidores" in self.show:
            print(f"{GREY}  · {event.session} {data['tool']}: {data['brief']}{RESET}")
        elif kind == "activity_done" and "bastidores" in self.show:
            mark = "ok" if data["ok"] else "erro"
            print(f"{GREY}  · {mark}: {data['brief'][:100]}{RESET}")
        elif kind == "thinking" and "thinking" in self.show:
            print(f"{GREY}{_wrap(data['text'][:400], '  ~ ')}{RESET}")
        elif kind == "level":
            detail = f" · {data['detail']}" if data.get("detail") else ""
            print(f"{DIM}{datetime.now():%H:%M:%S}{RESET} "
                  f"{_c(data['name'], self.colors.get(data['name'], '37'))} "
                  f"{DIM}{data['state']}{detail}{RESET}")
        elif kind == "status" and "bastidores" in self.show:
            print(f"{GREY}  · {event.session}: {data}{RESET}")


def run_command(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).expanduser()
    tag = "-" + uuid.uuid4().hex[:4]
    receita = pipelines.pegar(args.pipeline)
    levels = receita.montar(model=args.model, mode=args.permission_mode, tag=tag)
    modes = pipelines.mode_mismatch(levels)
    if len(modes) > 1:
        print(f"{RED}aviso: níveis em modos de permissão diferentes ({', '.join(sorted(modes))}). "
              f"O Claude Code vai reter as mensagens entre eles esperando revisão.{RESET}")
    colors = {spec.name: spec.color for spec in levels}
    show = set(args.show.split(",")) if args.show else set()
    console = Console(colors, show)

    run_dir = Path(args.run_dir).expanduser() if args.run_dir else Path("runs") / f"r{datetime.now():%Y%m%d-%H%M%S}{tag}"
    run_dir.mkdir(parents=True, exist_ok=True)
    bus = Bus(run_dir / "events.ndjson")
    run = Run(id=run_dir.name, cwd=cwd, task=args.task, levels=levels,
              run_dir=run_dir, bus=bus,
              bootstrap=receita.texto_bootstrap(args.task, tag), pipeline=receita.id)
    stopping = False

    def bye(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, bye)

    print(f"{BOLD}HighLevel{RESET} {DIM}· {receita.titulo} · {cwd} · níveis: "
          f"{' → '.join(s.name for s in levels)} · run em {run_dir}{RESET}\n")
    feed, _ = bus.subscribe()
    run.start_async()

    last_event = time.time()
    announced: set[str] = set()
    deadline = time.time() + args.timeout
    try:
        while not stopping and time.time() < deadline:
            if run.status == "erro":
                print(f"{RED}falhou ao subir: {run.error}{RESET}", file=sys.stderr)
                return 1
            try:
                event = Event(**{k: v for k, v in feed.get(timeout=1.0).items()
                                 if k in ("kind", "payload", "ts", "level", "session", "seq")})
            except queue.Empty:
                for session, block in run.blocked():
                    signature = f"{session.spec.name}:{block.kind}:{block.options[0][1][:40]}"
                    if signature not in announced:
                        announced.add(signature)
                        console.blocked(session.spec.name, block)
                        if args.auto_allow:
                            session.send_keys(block.keys_to_allow)
                            console.status(session.spec.name, f"auto-allow: liberado ({block.kind})")
                if time.time() - last_event > args.idle:
                    console.status("run", f"silêncio de {args.idle:.0f}s — encerrando")
                    break
                continue
            # Registros de custo e estado chegam sozinhos e não são sinal de trabalho:
            # contá-los como atividade fazia o cronômetro de silêncio nunca vencer.
            if event.kind in LIVE_KINDS:
                last_event = time.time()
                announced.clear()
            console.event(event)
    finally:
        print(f"\n{DIM}encerrando níveis…{RESET}")
        run.stop()
    return 0


def pipelines_command(_args: argparse.Namespace) -> int:
    for receita in pipelines.catalogo().values():
        print(f"{BOLD}{receita.id}{RESET}  {DIM}{receita.titulo}{RESET}")
        print(f"  {receita.descricao}")
        print(f"  níveis: {' → '.join(n['nome'] for n in receita.niveis)}\n")
    return 0


def serve_command(args: argparse.Namespace) -> int:
    from .server import serve
    serve(args.host, args.port, Path(args.runs).expanduser())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hl", description="pipeline de Claude Codes em níveis")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="sobe o pipeline e transmite os eventos")
    run_p.add_argument("--cwd", required=True, help="diretório de trabalho do pipeline")
    run_p.add_argument("--task", required=True, help="a tarefa, em português mesmo")
    run_p.add_argument("--model", default=None)
    run_p.add_argument("--pipeline", default=None,
                       help="qual receita de pipelines/ usar (padrão: professor-executor)")
    run_p.add_argument("--permission-mode", default="auto",
                       help="modo de permissão de TODOS os níveis (auto, acceptEdits, bypassPermissions, manual)")
    run_p.add_argument("--show", default="fios",
                       help="painéis extras: fios,bastidores,thinking")
    run_p.add_argument("--idle", type=float, default=120, help="segundos de silêncio até encerrar")
    run_p.add_argument("--timeout", type=float, default=1800)
    run_p.add_argument("--ready-timeout", type=float, default=60)
    run_p.add_argument("--run-dir", default=None, help="onde guardar o PTY cru de cada nível")
    run_p.add_argument("--auto-allow", action="store_true",
                       help="responde sozinho o pedido de permissão com a opção em foco")
    run_p.set_defaults(func=run_command)

    lista_p = sub.add_parser("pipelines", help="mostra as receitas disponíveis")
    lista_p.set_defaults(func=pipelines_command)

    serve_p = sub.add_parser("serve", help="sobe o servidor local (REST + SSE)")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=7333)
    serve_p.add_argument("--runs", default="runs", help="onde guardar os runs")
    serve_p.set_defaults(func=serve_command)

    args = parser.parse_args(argv)
    # streaming: sem isso, redirecionar a saída para arquivo segura tudo no buffer
    sys.stdout.reconfigure(line_buffering=True)
    return args.func(args)
