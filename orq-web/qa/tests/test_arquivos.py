"""Arquivos que o agente compartilha (_outbox/ do projeto): lista, prévia, download e aviso.

O primeiro teste grava na pasta por fora (não gasta cota): isola a página. O segundo pede ao claude real
que gere um arquivo e o compartilhe, sem dizer o nome da pasta: prova que a nota do system prompt basta.
"""

import os
import time
from pathlib import Path

from conftest import PROJECT

OUTBOX = Path(os.path.expanduser(PROJECT.partition(":")[2])) / "_outbox"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc00000"
    "0301010018dd8db00000000049454e44ae426082"
)  # 1×1 px


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
    (OUTBOX / "grafico.png").write_bytes(PNG)
    orq.page.click("#outbox .rf")  # ↻: relê agora (senão, no fim da rodada ou em 30 s)
    orq.page.wait_for_function("() => document.querySelectorAll('#outbox .frow').length === 2")
    assert orq.files() == ["grafico.png", "relatório.md"], orq.files()
    assert "novo arquivo grafico.png" in orq.toast() or "arquivos novos" in orq.toast(), orq.toast()
    assert orq.page.inner_text("#out").strip().endswith("2"), "contagem no 📂"
    assert orq.preview("relatório.md") == "DIV"
    assert "Tudo" in orq.page.inner_text("#view .vbody")
    assert orq.page.locator("#view .vbody strong").count() == 1, "markdown renderizado"
    orq.shot("arquivos-previa-md")
    orq.close_preview()
    assert orq.preview("grafico.png") == "IMG"
    orq.page.wait_for_function("() => document.querySelector('#view img').naturalWidth === 1")
    orq.shot("arquivos-previa-png")
    orq.close_preview()
    assert orq.download("grafico.png") == PNG
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
