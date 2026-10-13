"""Arquivos que o agente compartilha (_outbox/ do projeto): lista, prévia, download e aviso.

O primeiro teste grava na pasta por fora (não gasta cota): isola a página. O segundo pede ao claude real
que gere um arquivo e o compartilhe, sem dizer o nome da pasta: prova que a nota do system prompt basta.
"""

import os
import time
from pathlib import Path

from conftest import PROJECT

OUTBOX = Path(os.path.expanduser(PROJECT.partition(":")[2])) / "_outbox"


def png_bars(w: int = 320, h: int = 200) -> bytes:
    """PNG com barras coloridas e uma diagonal: dá para ver no vídeo que a prévia de imagem funcionou."""
    import struct
    import zlib

    cols = [(220, 60, 50), (240, 170, 40), (60, 160, 90), (50, 110, 200), (130, 70, 180), (40, 40, 40)]
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            r, g, b = cols[x * len(cols) // w]
            if abs(y - x * h // w) < 4:
                r, g, b = 255, 255, 255
            row += bytes((r, g, b))
        rows.append(bytes(row))

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )


def pdf_one_page(text: str = "QA do orq") -> bytes:
    """PDF mínimo de uma página com um texto grande."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 36 Tf 72 720 Td ({text}) Tj ET".encode()
    objs[3] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


PNG = png_bars()
PDF = pdf_one_page()


def clear_outbox():
    if OUTBOX.exists():
        for f in OUTBOX.iterdir():
            if f.is_file():
                f.unlink()


def test_lista_previa_download_e_aviso(orq, agent):
    clear_outbox()
    orq.open_files()
    orq.page.click("#outbox .rf")  # a página já tinha lido a pasta antes da limpeza: relê
    orq.page.wait_for_function("() => document.querySelectorAll('#outbox .frow').length === 0")
    assert orq.files() == [] and "Nada compartilhado" in orq.page.inner_text("#outbox")
    OUTBOX.mkdir(exist_ok=True)
    (OUTBOX / "relatório.md").write_text("# Relatório\n\nTudo **certo**.\n")
    time.sleep(1.1)
    (OUTBOX / "relatorio.pdf").write_bytes(PDF)
    time.sleep(1.1)
    (OUTBOX / "grafico.png").write_bytes(PNG)
    orq.page.click("#outbox .rf")  # ↻: relê agora (senão, no fim da rodada ou em 30 s)
    orq.page.wait_for_function("() => document.querySelectorAll('#outbox .frow').length === 3")
    assert orq.files() == ["grafico.png", "relatorio.pdf", "relatório.md"], orq.files()
    assert "arquivos novos" in orq.toast() or "novo arquivo" in orq.toast(), orq.toast()
    assert orq.page.inner_text("#out").strip().endswith("3"), "contagem no 📂"
    orq.page.wait_for_timeout(1500)  # pausas: o vídeo é para uma pessoa conferir
    assert orq.preview("relatório.md") == "DIV"
    assert "Tudo" in orq.page.inner_text("#view .vbody")
    assert orq.page.locator("#view .vbody strong").count() == 1, "markdown renderizado"
    orq.shot("arquivos-previa-md")
    orq.page.wait_for_timeout(2500)
    orq.close_preview()
    assert orq.preview("grafico.png") == "IMG"
    orq.page.wait_for_function("() => document.querySelector('#view img').naturalWidth === 320")
    orq.shot("arquivos-previa-png")
    orq.page.wait_for_timeout(2500)
    orq.close_preview()
    assert (
        orq.preview("relatorio.pdf") == "IFRAME"
    )  # o visualizador é do navegador (no headless pode não desenhar)
    orq.page.wait_for_timeout(3000)
    orq.shot("arquivos-previa-pdf")
    orq.close_preview()
    assert orq.download("grafico.png") == PNG
    assert orq.download("relatorio.pdf") == PDF
    assert orq.download("relat%C3%B3rio.md").startswith(b"# Relat")
    assert not orq.problems(), orq.problems()


def test_claude_gera_e_compartilha(orq, agent):
    clear_outbox()
    orq.say(
        "Crie um arquivo chamado resumo.md com exatamente uma linha de texto: 'QA do orq'. "
        "Depois compartilhe esse arquivo comigo pela página. Responda só 'pronto' quando terminar.",
        wait=False,
    )
    st = orq.answer_permissions()
    assert "erro" not in st, st
    orq.wait_file("resumo.md")
    orq.shot("arquivos-claude")
    assert (OUTBOX / "resumo.md").read_text().strip() == "QA do orq"
    assert orq.download("resumo.md").strip() == b"QA do orq"
    assert not orq.problems(), orq.problems()
