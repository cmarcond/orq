"""Arquivos que você manda ao agente (📎 ou arrastar para o chat): vão para `_inbox/` na pasta do projeto, na
máquina do agente, e a mensagem sai com os caminhos. O primeiro teste é só a página (sem cota): soltar arquivo
sem aba aberta avisa. O segundo anexa um CSV pelo 📎 e arrasta uma nota, tira um da fila e põe de volta, envia, e
o claude real lê os dois de lá e responde a soma e a palavra-chave (um turno)."""

import base64
import subprocess

from orqqa import local_host_name

CSV = "item,valor\na,10\nb,20\nc,30\n"  # soma 60
NOTA = "palavra-chave: jabuticaba-317\n"
SECRET = "jabuticaba-317"

# Arrastar de verdade não existe no Chromium sem janela: solta um File num DragEvent, o mesmo que a página recebe.
DROP = """([name, b64]) => {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const dt = new DataTransfer();
  dt.items.add(new File([bytes], name, { type: 'text/plain' }));
  document.body.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
}"""


def drop(orq, name: str, text: str) -> None:
    orq.page.evaluate(DROP, [name, base64.b64encode(text.encode()).decode()])


def chips(orq):
    return orq.page.evaluate("[...document.querySelectorAll('#files .fchip')].map((e) => e.textContent.trim())")


def cat_on(host: str, folder: str, names) -> str:
    """Conteúdo dos arquivos em `folder` na máquina do agente, numa conexão só (ver ssh-sequencial-falha)."""
    script = f'cd {folder}/_inbox && ls -1 && for f in {" ".join(names)}; do printf "== %s\\n" "$f"; cat "$f"; done'
    cmd = ["sh", "-c", script] if host == local_host_name() else ["ssh", "-o", "BatchMode=yes", host, script]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-300:]
    return r.stdout


def test_soltar_sem_aba_avisa(orq):
    drop(orq, "nota.txt", NOTA)
    orq.wait_fn("() => document.getElementById('toast').textContent.includes('abra uma aba')")
    assert orq.page.is_hidden("#files")
    orq.shot("anexo-sem-aba")
    assert not orq.problems(), orq.problems()


def test_anexa_arrasta_e_claude_le(orq, agent, tmp_path):
    csv = tmp_path / "dados.csv"
    csv.write_text(CSV)
    folder = next(a["dir"] for a in orq.agents() if a["label"] == agent).rstrip("/")
    # 📎 abre o seletor de arquivos do navegador; o arquivo entra na fila como um chip com o tamanho
    with orq.page.expect_file_chooser() as fc:
        orq.page.click("#attach")
    fc.value.set_files(str(csv))
    orq.page.wait_for_selector("#files:not([hidden]) .fchip")
    assert chips(orq) == [f"📄 dados.csv {len(CSV)} B✕"], chips(orq)
    # arrastar para o chat põe outro na fila
    drop(orq, "nota.txt", NOTA)
    orq.wait_fn("() => document.querySelectorAll('#files .fchip').length === 2")
    assert chips(orq)[1].startswith("📄 nota.txt"), chips(orq)
    orq.shot("anexo-fila")
    orq.page.wait_for_timeout(1500)  # pausas: o vídeo é para uma pessoa conferir
    # ✕ tira da fila sem enviar nada; arrastar de novo põe de volta
    orq.page.click('#files .fchip:nth-child(2) b[data-i="1"]')
    assert chips(orq) == [f"📄 dados.csv {len(CSV)} B✕"]
    drop(orq, "nota.txt", NOTA)
    orq.wait_fn("() => document.querySelectorAll('#files .fchip').length === 2")
    assert orq.page.locator(".chat.on .me").count() == 0, "nada saiu ainda"
    # enviar: os dois sobem para a _inbox do projeto e a mensagem sai com os caminhos
    orq.say(
        "Leia os dois arquivos que enviei (os caminhos estão nesta mensagem). Responda numa linha só: a soma "
        "da coluna valor do CSV e a palavra-chave da nota, nada mais.",
        wait=False,
    )
    orq.wait_fn("() => [...document.querySelectorAll('.chat.on .me')].some((e) => e.innerText.includes('_inbox'))")
    me = orq.page.inner_text(".chat.on .me")
    assert "Arquivo(s) enviado(s) para a pasta _inbox do projeto:" in me, me
    assert f"- {folder}/_inbox/dados.csv" in me and f"- {folder}/_inbox/nota.txt" in me, me
    assert orq.page.is_hidden("#files"), "a fila esvaziou"
    orq.shot("anexo-enviado")
    # o claude real lê os arquivos de lá
    st = orq.answer_permissions(240)
    assert "erro" not in st, st
    reply = orq.last_ai()
    assert "60" in reply and SECRET in reply, reply
    orq.shot("anexo-claude-leu")
    orq.page.wait_for_timeout(2000)
    # e os bytes chegaram inteiros à máquina do agente
    out = cat_on(agent.split("@")[1], folder, ["dados.csv", "nota.txt"])
    assert out == f"dados.csv\nnota.txt\n== dados.csv\n{CSV}== nota.txt\n{NOTA}", out
    assert not orq.problems(), orq.problems()
