#!/usr/bin/env python3
"""Demonstração gravável dos conjuntos (⊞) com trabalho de verdade: não é teste, é roteiro.

Um conjunto com dois executores (PROGRAMADOR e DOCUMENTADOR, cada um na própria pasta e com o próprio
prompt) e um professor líder que recebe a tarefa e coordena: o programador escreve uma biblioteca Python
com testes pytest e roda os testes; o professor repassa a API ao documentador, que escreve o README; no fim
o professor resume. O roteiro abre a página, mostra o JSON e a lista, abre o conjunto, alterna entre as abas
enquanto eles trabalham (aprovando permissões na tela, se houver), mostra os arquivos entregues na prévia da
página e a fotografia das abas abertas. Legendas na própria página explicam cada passo.

Uso (no server0, num display virtual, gravando com ffmpeg):
  DISPLAY=:77 ORQ_QA_HEADED=1 ORQ_LOCAL_HOST=server0 ORQ_EXTRA_HOSTS=laptop \\
    DEMO_FFMPEG_PID=<pid> .venv/bin/python qa/demo_conjuntos.py
Com DEMO_FFMPEG_PID, a gravação é encerrada (SIGINT) antes da limpeza. Pastas da demo, descartáveis:
server0:~/Projects/orq-demo-prog e ~/Projects/orq-demo-docs.
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

TAREFA = """Tarefa do time: uma biblioteca Python pequena chamada `conversor`, que converte temperaturas entre
Celsius, Fahrenheit e Kelvin.

1. O programador cria conversor.py com as funções c_para_f, f_para_c, c_para_k, k_para_c e
   converter(valor, de, para), que aceita as escalas "C", "F" e "K" e levanta ValueError para escala
   desconhecida e para temperatura abaixo do zero absoluto; e test_conversor.py com pelo menos 8 testes
   pytest. Ele roda `python3 -m pytest -q`, grava a saída em testes.txt e copia conversor.py e testes.txt
   para _outbox/.
2. Quando os testes passarem, repasse ao documentador as assinaturas e o comportamento, inclusive os erros.
   Ele escreve README.md na pasta dele, com uso e exemplos, e copia o README.md para _outbox/.
3. No fim, me diga em poucas linhas o resultado dos testes e o que cada um entregou.

Não me pergunte nada: decida o que estiver em aberto."""

CONJUNTO = {
    "nome": SID,
    "descricao": "programador + documentador, com professor: biblioteca de temperaturas com testes e README",
    "permissao": "auto",
    "agentes": [
        {
            "maquina": HOST,
            "pasta": PROG,
            "modo": "new",
            "prompt": "Você é o PROGRAMADOR de um time pequeno. Escreve código Python claro, com testes "
            "pytest, só na sua pasta, e roda os testes antes de dizer que terminou. Trabalha sozinho, sem "
            "pedir confirmação, e reporta ao professor pelo SendMessage só os marcos.",
        },
        {
            "maquina": HOST,
            "pasta": DOCS,
            "modo": "new",
            "prompt": "Você é o DOCUMENTADOR de um time pequeno. Escreve documentação em Markdown, curta, "
            "com exemplos corretos para a API que receber, só na sua pasta. Não escreve código de "
            "produção. Trabalha sozinho e reporta ao professor pelo SendMessage só os marcos.",
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


class Demo:
    def __init__(self, o: Orq):
        self.o, self.p = o, o.page
        self.view = None

    def cap(self, t: str, sub: str = "") -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {t}", flush=True)
        self.p.evaluate(CAPTION_JS, [t, sub])

    def pause(self, s: float) -> None:
        self.p.wait_for_timeout(int(s * 1000))

    def agents(self) -> dict:
        return {a["label"]: a for a in self.o.state().get("agents", []) if a.get("state") != "closed"}

    # ---- o que mostrar
    def show_prof(self) -> None:
        self.o.show(L_PROG)  # a aba do primeiro agente fala com o professor (topo da cadeia)
        self.view = "prof"

    def show_prog(self) -> None:
        self.o.show(L_PROG)
        self.p.click(f'#levels .lcard[data-l="{L_PROG}"]')  # o executor de baixo, pelo cartão do nível
        self.view = "prog"

    def show_docs(self) -> None:
        self.o.show(L_DOCS)
        self.view = "docs"

    def approve_pending(self) -> bool:
        """Se algum agente da demo pede permissão, abre o chat dele e clica Permitir (na tela)."""
        ag = self.agents()
        for lab, show, who in (
            (L_PROF, self.show_prof, "professor"),
            (L_PROG, self.show_prog, "PROGRAMADOR"),
            (L_DOCS, self.show_docs, "DOCUMENTADOR"),
        ):
            if ag.get(lab, {}).get("state") != "permission":
                continue
            show()
            self.cap(f"O {who} pediu permissão: aprovando na própria página", ag[lab].get("msg", "")[:90])
            try:
                self.p.wait_for_selector(".chat.on .perm:not(.done) .btns button[data-ok='1']", timeout=15000)
                self.pause(1.5)
                self.o.permission(True)
            except Exception as e:  # noqa: BLE001 - pode ter sido resolvida sozinha
                print("  permissão não achada no chat:", str(e)[:100])
            self.pause(2)
            return True
        return False

    def done(self) -> bool:
        if not all(f.exists() for f in FILES):
            return False
        ag = self.agents()
        return all(ag.get(lab, {}).get("state") == "waiting" for lab in (L_PROF, L_PROG, L_DOCS))


def main() -> int:
    for f in FILES:
        if f.exists():
            raise SystemExit(f"{f} já existe: a demo começa com pastas vazias")
    url, started = server_up()
    ff = int(os.environ.get("DEMO_FFMPEG_PID") or 0)
    try:
        with sync_playwright() as pw:
            o = Orq.launch(pw, headed=True, video=False)
            d = Demo(o)
            try:
                run(d, o)
            finally:
                if ff:
                    try:
                        os.kill(ff, signal.SIGINT)  # fecha o vídeo antes da limpeza
                    except OSError:
                        pass
                    time.sleep(3)
                cleanup(o)
                o.close()
    finally:
        # o orq-web do QA sai sempre que foi a demo que o subiu, mesmo se ela falhar: senão ele fica de pé
        # com pontes `attach` presas a agentes já encerrados, e a próxima demo o reaproveita sem desligar
        if started:
            server_down()
    return 0


def run(d: Demo, o: Orq) -> None:
    o.api(f"/api/conjuntos/{SID}/delete", {})
    assert o.save_set(CONJUNTO) == SID
    d.pause(1)
    d.cap(
        "orq · conjuntos (⊞)",
        "vários agentes de uma vez, cada um com o próprio prompt, com um professor líder",
    )
    d.pause(4)

    d.cap(
        "1. O conjunto é um JSON",
        "dois executores em pastas próprias, com prompts próprios, e um líder com a tarefa",
    )
    d.p.evaluate(JSON_JS, json.dumps(CONJUNTO, ensure_ascii=False, indent=2))
    d.pause(12)
    d.p.evaluate(JSON_JS, "")

    d.cap(
        "2. ⊞ conjuntos: a lista dos salvos",
        "clique abre; ⬇ baixa o JSON; ✕ apaga; 📄 envia um pronto; 📷 fotografa as abas",
    )
    o.open_sets()
    d.pause(6)

    d.cap(
        "3. Abrindo o conjunto",
        "os dois executores sobem, são vinculados, o professor sobe e recebe a tarefa",
    )
    t0 = time.time()
    job = o.open_set(SID, timeout=300)
    print(
        "  aberto em",
        round(time.time() - t0),
        "s:",
        job["feitos"],
        "professor:",
        job["top"],
        "erros:",
        job["erros"],
    )
    d.show_prof()
    d.cap(
        "4. O professor recebe a tarefa e distribui o trabalho",
        "ele nunca escreve código: delega pelo SendMessage",
    )
    d.pause(15)

    # trabalho: alterna entre os três, aprovando permissões se houver, até os arquivos existirem e todos
    # pararem
    views = [
        (
            d.show_prog,
            "O PROGRAMADOR escreve conversor.py e os testes, e roda o pytest",
            "pasta ~/Projects/orq-demo-prog",
        ),
        (
            d.show_prof,
            "O professor acompanha os marcos e repassa a API",
            "os fios à direita são as mensagens entre eles",
        ),
        (
            d.show_docs,
            "O DOCUMENTADOR escreve o README com a API que recebeu",
            "pasta ~/Projects/orq-demo-docs, outra aba",
        ),
        (
            d.show_prof,
            "O professor acompanha os marcos e repassa a API",
            "os fios à direita são as mensagens entre eles",
        ),
    ]
    end = time.time() + 20 * 60
    i = 0
    while time.time() < end:
        show, t, sub = views[i % len(views)]
        show()
        d.cap(t, sub)
        for _ in range(10):  # ~20 s nesta vista
            if d.approve_pending():
                show()
                d.cap(t, sub)
            d.pause(2)
        i += 1
        if d.done():
            break
    else:
        raise SystemExit("o time não terminou em 20 min")
    print("  trabalho terminado em", round(time.time() - t0), "s")

    d.show_prof()
    d.cap("5. O professor resume o que cada um entregou")
    d.pause(10)

    d.show_prog()
    d.cap("6. O que o PROGRAMADOR entregou, na 📂 _outbox/", "a página lista os arquivos e mostra a prévia")
    o.open_files()
    d.pause(3)
    for name in ("testes.txt", "conversor.py"):
        if name in o.files():
            o.preview(name)
            d.pause(7)
            o.close_preview()
    d.show_docs()
    d.cap("7. O README que o DOCUMENTADOR escreveu", "Markdown renderizado na prévia da página")
    o.open_files()
    d.pause(2)
    if "README.md" in o.files():
        o.preview("README.md")
        d.pause(10)
        o.close_preview()

    d.cap("8. 📷 As abas abertas viram um conjunto novo", "o mesmo JSON, pronto para reabrir tudo outro dia")
    o.open_sets()
    d.p.click("#setsnap")
    d.p.wait_for_selector("#picker:not([hidden]) #snapjson", timeout=20000)
    d.pause(10)
    d.p.click("#pclose")
    d.cap("Fim", "conjuntos: abrir vários agentes de uma vez, com ou sem professor")
    d.pause(4)
    d.cap("")


def cleanup(o: Orq) -> None:
    print("limpando: desfaz o professor, encerra os agentes, apaga o conjunto")
    o.api(f"/api/agent/{L_PROG}/downgrade", {})
    for lab in (L_PROG, L_DOCS):
        try:
            o.ensure_stopped(lab)
        except Exception as e:  # noqa: BLE001
            print("  não consegui encerrar", lab, e)
    o.api(f"/api/conjuntos/{SID}/delete", {})


if __name__ == "__main__":
    sys.exit(main())
