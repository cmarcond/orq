"""Files you send to the agent (📎 or dragging onto the chat): they go to `_inbox/` in the project folder, on
the agent's machine, and the message goes out with the paths. The first test is only the page (no quota):
dropping a file with no tab open warns. The second attaches a CSV through 📎 and drags a note, removes one
from the queue and puts it back, sends,
and the real claude reads both from there and answers the sum and the keyword (one turn)."""

import base64
import subprocess

from orqqa import local_host_name

CSV = "item,value\na,10\nb,20\nc,30\n"  # sum 60
NOTA = "keyword: jabuticaba-317\n"
SECRET = "jabuticaba-317"

# A real drag does not exist in headless Chromium: drops a File in a DragEvent, the same thing the page
# receives.
DROP = """([name, b64]) => {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const dt = new DataTransfer();
  dt.items.add(new File([bytes], name, { type: 'text/plain' }));
  document.body.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
}"""


def drop(orq, name: str, text: str) -> None:
    orq.page.evaluate(DROP, [name, base64.b64encode(text.encode()).decode()])


def chips(orq):
    return orq.page.evaluate(
        "[...document.querySelectorAll('#files .fchip')].map((e) => e.textContent.trim())"
    )


def cat_on(host: str, folder: str, names) -> str:
    """Contents of the files in `folder` on the agent's machine, in a single connection (see
    ssh-sequencial-falha)."""
    script = (
        f"cd {folder}/_inbox && ls -1 && for f in {' '.join(names)}; do "
        'printf "== %s\\n" "$f"; cat "$f"; done'
    )
    cmd = ["sh", "-c", script] if host == local_host_name() else ["ssh", "-o", "BatchMode=yes", host, script]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-300:]
    return r.stdout


def test_soltar_sem_aba_avisa(orq):
    drop(orq, "nota.txt", NOTA)
    orq.wait_fn("() => document.getElementById('toast').textContent.includes('open a tab')")
    assert orq.page.is_hidden("#files")
    orq.shot("anexo-sem-aba")
    assert not orq.problems(), orq.problems()


def test_anexa_arrasta_e_claude_le(orq, agent, tmp_path):
    csv = tmp_path / "dados.csv"
    csv.write_text(CSV)
    folder = next(a["dir"] for a in orq.agents() if a["label"] == agent).rstrip("/")
    # 📎 opens the browser's file picker; the file enters the queue as a chip with the size
    with orq.page.expect_file_chooser() as fc:
        orq.page.click("#attach")
    fc.value.set_files(str(csv))
    orq.page.wait_for_selector("#files:not([hidden]) .fchip")
    assert chips(orq) == [f"📄 dados.csv {len(CSV)} B✕"], chips(orq)
    # dragging onto the chat puts another one in the queue
    drop(orq, "nota.txt", NOTA)
    orq.wait_fn("() => document.querySelectorAll('#files .fchip').length === 2")
    assert chips(orq)[1].startswith("📄 nota.txt"), chips(orq)
    orq.shot("anexo-fila")
    orq.page.wait_for_timeout(1500)  # pauses: the video is for a person to check
    # ✕ removes it from the queue without sending anything; dragging again puts it back
    orq.page.click('#files .fchip:nth-child(2) b[data-i="1"]')
    assert chips(orq) == [f"📄 dados.csv {len(CSV)} B✕"]
    drop(orq, "nota.txt", NOTA)
    orq.wait_fn("() => document.querySelectorAll('#files .fchip').length === 2")
    assert orq.page.locator(".chat.on .me").count() == 0, "nothing went out yet"
    # send: both go up to the project's _inbox and the message goes out with the paths
    orq.say(
        "Read the two files I sent (the paths are in this message). Answer in a single line: the sum of the "
        "value column of the CSV and the keyword in the note, nothing else.",
        wait=False,
    )
    orq.wait_fn(
        "() => [...document.querySelectorAll('.chat.on .me')].some((e) => e.innerText.includes('_inbox'))"
    )
    me = orq.page.inner_text(".chat.on .me")
    assert "File(s) uploaded to the project's _inbox folder:" in me, me
    assert f"- {folder}/_inbox/dados.csv" in me and f"- {folder}/_inbox/nota.txt" in me, me
    assert orq.page.is_hidden("#files"), "the queue emptied"
    orq.shot("anexo-enviado")
    # the real claude reads the files from there
    st = orq.answer_permissions(240)
    assert "error" not in st, st
    reply = orq.last_ai()
    assert "60" in reply and SECRET in reply, reply
    orq.shot("anexo-claude-leu")
    orq.page.wait_for_timeout(2000)
    # and the bytes arrived intact on the agent's machine
    out = cat_on(agent.split("@")[1], folder, ["dados.csv", "nota.txt"])
    assert out == f"dados.csv\nnota.txt\n== dados.csv\n{CSV}== nota.txt\n{NOTA}", out
    assert not orq.problems(), orq.problems()
