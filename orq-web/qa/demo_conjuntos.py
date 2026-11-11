#!/usr/bin/env python3
"""Recordable demonstration of the sets (⊞) with real work: it is not a test, it is a script.

A set with two executors (PROGRAMMER and DOCUMENTER, each in its own folder and with its own
prompt) and a lead teacher that receives the task and coordinates: the programmer writes a Python library
with pytest tests and runs the tests; the teacher passes the API on to the documenter, which writes the
README; at the end the teacher summarises. The script opens the page, shows the JSON and the list, opens the
set, alternates between the tabs while they work (approving permissions on screen, if any), shows the
delivered files in the page's preview and the snapshot of the open tabs. Captions explain each step: with
DEMO_CAP_DIR, the script writes the text to <dir>/t.txt and <dir>/s.txt and ffmpeg draws it on the browser bar
(drawtext with reload, filter in qa/demo_legenda.ffmpeg); without it, in a box inside the page.

Usage (on server0, on a virtual display, recording with ffmpeg):
  DISPLAY=:77 ORQ_QA_HEADED=1 ORQ_LOCAL_HOST=server0 ORQ_EXTRA_HOSTS=laptop \\
    DEMO_FFMPEG_PID=<pid> DEMO_CAP_DIR=<dir> .venv/bin/python qa/demo_conjuntos.py
With DEMO_FFMPEG_PID, the recording is stopped (SIGINT) before the cleanup. Demo folders, throwaway and
deleted before and after (with the state and the conversations): server0:~/Projects/orq-demo-prog and
orq-demo-docs.
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orqqa import Orq, server_down, server_up  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

HOST = "server0"
PROG, DOCS = "~/Projects/orq-demo-prog", "~/Projects/orq-demo-docs"
L_PROG, L_DOCS = f"orq-demo-prog@{HOST}", f"orq-demo-docs@{HOST}"
L_PROF = f"orq-demo-prog~professor@{HOST}"
SID = "demo-time-conversor"
HOME = Path.home()
FILES = [
    HOME / "Projects/orq-demo-prog/conversor.py",
    HOME / "Projects/orq-demo-prog/test_conversor.py",
    HOME / "Projects/orq-demo-docs/README.md",
]

TAREFA = """Team task: a small Python library called `conversor`, which converts temperatures between
Celsius, Fahrenheit and Kelvin.

1. The programmer creates conversor.py with the functions c_para_f, f_para_c, c_para_k, k_para_c and
   converter(valor, de, para), which accepts the scales "C", "F" and "K" and raises ValueError for an
   unknown scale and for a temperature below absolute zero; and test_conversor.py with at least 8 pytest
   tests. It runs `python3 -m pytest -q`, writes the output to testes.txt and copies conversor.py and
   testes.txt to _outbox/.
2. When the tests pass, pass on to the documenter the signatures and the behaviour, including the errors.
   It writes README.md in its folder, with usage and examples, and copies README.md to _outbox/.
3. At the end, tell me in a few lines the result of the tests and what each one delivered.

Do not ask me anything: decide whatever is open."""

CONJUNTO = {
    "nome": SID,
    "descricao": "programmer + documenter, with a teacher: temperature library with tests and README",
    "permissao": "auto",
    "agentes": [
        {
            "maquina": HOST,
            "pasta": PROG,
            "modo": "new",
            "prompt": "You are the PROGRAMMER of a small team. You write clear Python code, with pytest "
            "tests, only in your folder, and run the tests before saying you are finished. You work on your "
            "own, without asking for confirmation, and report to the teacher through SendMessage only the "
            "milestones.",
        },
        {
            "maquina": HOST,
            "pasta": DOCS,
            "modo": "new",
            "prompt": "You are the DOCUMENTER of a small team. You write documentation in Markdown, short, "
            "with correct examples for the API you receive, only in your folder. You do not write production "
            "code. You work on your own and report to the teacher through SendMessage only the milestones.",
        },
    ],
    "lider": {"receita": "professor-executores", "tarefa": TAREFA},
}

CAPTION_JS = """([t, sub]) => {
  let c = document.getElementById('demo-cap');
  if (!c) {
    c = document.createElement('div');
    c.id = 'demo-cap';
    c.style.cssText = 'pointer-events:none;position:fixed;left:12px;top:8px;z-index:99;'
      + 'max-width:760px;padding:10px 16px;'
      + 'border-radius:10px;background:rgba(20,20,30,.88);color:#fff;font:600 17px/1.35 system-ui;'
      + 'box-shadow:0 4px 16px rgba(0,0,0,.3)';
    document.body.appendChild(c);
  }
  const s2 = 'font-weight:400;font-size:14px;opacity:.85;margin-top:3px';
  c.innerHTML = t ? `<div>${t}</div>` + (sub ? `<div style="${s2}">${sub}</div>` : '') : '';
  c.style.display = t ? 'block' : 'none';
}"""

JSON_JS = """(txt) => {
  let p = document.getElementById('demo-json');
  if (!txt) { if (p) p.remove(); return; }
  p = document.createElement('pre');
  p.id = 'demo-json';
  p.style.cssText = 'pointer-events:none;position:fixed;left:50%;top:90px;transform:translateX(-50%);'
    + 'z-index:98;width:900px;'
    + 'max-height:740px;overflow:auto;margin:0;padding:16px 18px;border-radius:12px;background:#1e1e24;'
    + 'color:#e8e8ea;font:12.5px/1.45 ui-monospace,monospace;white-space:pre-wrap;'
    + 'box-shadow:0 8px 30px rgba(0,0,0,.4)';
  p.textContent = txt;
  document.body.appendChild(p);
}"""


CAP_DIR = Path(os.environ["DEMO_CAP_DIR"]) if os.environ.get("DEMO_CAP_DIR") else None


def plain(text: str) -> str:
    """Without emoji: ffmpeg's drawtext does not draw colour emoji (it becomes a square)."""
    return "".join(ch for ch in text if ord(ch) < 0x2190 or ch in "·").strip()


class Demo:
    def __init__(self, o: Orq):
        self.o, self.p = o, o.page
        self.view = None

    def cap(self, t: str, sub: str = "") -> None:
        """Caption. With DEMO_CAP_DIR, it goes to the files t.txt and s.txt that ffmpeg draws over the
        video (drawtext with reload), on the browser bar; without it, in a box inside the page."""
        print(f"[{time.strftime('%H:%M:%S')}] {t}", flush=True)
        if not CAP_DIR:
            self.p.evaluate(CAPTION_JS, [t, sub])
            return
        for name, text in (("t.txt", t), ("s.txt", sub)):
            tmp = CAP_DIR / f".{name}.tmp"
            tmp.write_text(plain(text), encoding="utf-8")
            os.replace(str(tmp), str(CAP_DIR / name))  # atomic: ffmpeg never reads a half-written file

    def pause(self, s: float) -> None:
        self.p.wait_for_timeout(int(s * 1000))

    def agents(self) -> dict:
        return {a["label"]: a for a in self.o.state().get("agents", []) if a.get("state") != "closed"}

    # ---- what to show
    def show_prof(self) -> None:
        self.o.show(L_PROG)  # the first agent's tab talks to the teacher (top of the chain)
        self.view = "prof"

    def show_prog(self) -> None:
        self.o.show(L_PROG)
        self.p.click(f'#levels .lcard[data-l="{L_PROG}"]')  # the executor below, through the level's card
        self.view = "prog"

    def show_docs(self) -> None:
        self.o.show(L_DOCS)
        self.view = "docs"

    def approve_pending(self) -> bool:
        """If some agent of the demo asks permission, opens its chat and clicks Allow (on screen)."""
        ag = self.agents()
        for lab, show, who in (
            (L_PROF, self.show_prof, "teacher"),
            (L_PROG, self.show_prog, "PROGRAMMER"),
            (L_DOCS, self.show_docs, "DOCUMENTER"),
        ):
            if ag.get(lab, {}).get("state") != "permission":
                continue
            show()
            self.cap(f"The {who} asked permission: approving on the page itself", ag[lab].get("msg", "")[:70])
            try:
                self.p.wait_for_selector(".chat.on .perm:not(.done) .btns button[data-ok='1']", timeout=15000)
                self.pause(1.5)
                self.o.permission(True)
            except Exception as e:  # noqa: BLE001 - it may have been resolved by itself
                print("  permission not found in the chat:", str(e)[:100])
            self.pause(2)
            return True
        return False

    def done(self) -> bool:
        if not all(f.exists() for f in FILES):
            return False
        ag = self.agents()
        return all(ag.get(lab, {}).get("state") == "waiting" for lab in (L_PROF, L_PROG, L_DOCS))


def main() -> int:
    url, started = server_up()
    ff = int(os.environ.get("DEMO_FFMPEG_PID") or 0)
    try:
        with sync_playwright() as pw:
            o = Orq.launch(pw, headed=True, video=False)
            d = Demo(o)
            cleanup(o)  # starts from scratch: no agents, folders or conversations from a previous demo
            try:
                run(d, o)
            finally:
                if ff:
                    try:
                        os.kill(ff, signal.SIGINT)  # closes the video before the cleanup
                    except OSError:
                        pass
                    time.sleep(3)
                cleanup(o)
                o.close()
    finally:
        # the QA's orq-web always exits when it was the demo that started it, even if the demo fails:
        # otherwise it stays up with `attach` bridges stuck to agents already closed, and the next demo
        # reuses it without shutting it down
        if started:
            server_down()
    return 0


def run(d: Demo, o: Orq) -> None:
    o.api(f"/api/conjuntos/{SID}/delete", {})
    assert o.save_set(CONJUNTO) == SID
    d.pause(1)
    d.cap(
        "orq · sets",
        "several agents at once, each with its own prompt, with a lead teacher",
    )
    d.pause(4)

    d.cap(
        "1. The set is a JSON",
        "two executors in their own folders, with their own prompts, and a lead with the task",
    )
    d.p.evaluate(JSON_JS, json.dumps(CONJUNTO, ensure_ascii=False, indent=2))
    d.pause(12)
    d.p.evaluate(JSON_JS, "")

    d.cap(
        "2. The sets button: the list of the saved ones",
        "a click opens; it also downloads the JSON, deletes, takes a ready-made one and snapshots the tabs",
    )
    o.open_sets()
    d.pause(6)

    d.cap(
        "3. Opening the set",
        "the two executors start, are linked, the teacher starts and receives the task",
    )
    t0 = time.time()
    job = o.open_set(SID, timeout=300)
    print(
        "  opened in",
        round(time.time() - t0),
        "s:",
        job["feitos"],
        "teacher:",
        job["top"],
        "errors:",
        job["erros"],
    )
    d.show_prof()
    d.cap(
        "4. The teacher receives the task and distributes the work",
        "it never writes code: it delegates through SendMessage",
    )
    d.pause(15)

    # work: alternates between the three, approving permissions if any, until the files exist and all of
    # them stop
    views = [
        (
            d.show_prog,
            "The PROGRAMMER writes conversor.py and the tests, and runs pytest",
            "folder ~/Projects/orq-demo-prog",
        ),
        (
            d.show_prof,
            "The teacher follows the milestones and passes the API on",
            "the wires on the right are the messages between them",
        ),
        (
            d.show_docs,
            "The DOCUMENTER writes the README with the API it received",
            "folder ~/Projects/orq-demo-docs, another tab",
        ),
        (
            d.show_prof,
            "The teacher follows the milestones and passes the API on",
            "the wires on the right are the messages between them",
        ),
    ]
    end = time.time() + 20 * 60
    i = 0
    while time.time() < end:
        show, t, sub = views[i % len(views)]
        show()
        d.cap(t, sub)
        for _ in range(10):  # ~20 s on this view
            if d.approve_pending():
                show()
                d.cap(t, sub)
            d.pause(2)
        i += 1
        if d.done():
            break
    else:
        raise SystemExit("the team did not finish in 20 min")
    print("  work finished in", round(time.time() - t0), "s")

    d.show_prof()
    d.cap("5. The teacher summarises what each one delivered")
    d.pause(10)

    d.show_prog()
    d.cap(
        "6. What the PROGRAMMER delivered, in the _outbox/ folder",
        "the page lists the files and shows the preview",
    )
    o.open_files()
    d.pause(3)
    for name in ("testes.txt", "conversor.py"):
        if name in o.files():
            o.preview(name)
            d.pause(7)
            o.close_preview()
    d.show_docs()
    d.cap("7. The README the DOCUMENTER wrote", "Markdown rendered in the page's preview")
    o.open_files()
    d.pause(2)
    if "README.md" in o.files():
        o.preview("README.md")
        d.pause(10)
        o.close_preview()

    d.cap(
        "8. The open tabs become a new set",
        "the snapshot generates the same JSON, ready to reopen everything another day",
    )
    o.open_sets()
    d.p.click("#setsnap")
    d.p.wait_for_selector("#picker:not([hidden]) #snapjson", timeout=20000)
    d.pause(10)
    d.p.click("#pclose")
    d.cap("End", "sets: open several agents at once, with or without a teacher")
    d.pause(4)
    d.cap("")


def cleanup(o: Orq) -> None:
    """Undoes the teacher, closes the agents, deletes the demo folders with their state and conversations
    (Orq.purge) and deletes the set."""
    print("cleaning: agents, folders and set of the demo")
    for spec in (f"{HOST}:{PROG}", f"{HOST}:{DOCS}"):
        try:
            gone = o.purge(spec)
            print(f"  {spec}: clean" + (f" (closed {', '.join(gone)})" if gone else ""))
        except Exception as e:  # noqa: BLE001 - cleans what it can; the rest shows up in the log
            print("  could not clean", spec, e)
    o.api(f"/api/conjuntos/{SID}/delete", {})


if __name__ == "__main__":
    sys.exit(main())
