"""Portões de boot e detecção de bloqueio, lidos da tela do terminal.

Uma sessão recém-aberta pode parar num diálogo antes de escrever qualquer coisa no
transcript: confiança no diretório, aceite do modo bypass. E, no meio do trabalho, um
pedido de permissão trava a sessão sem deixar rastro no JSONL — o `tool_use` já foi
gravado, o `tool_result` nunca chega. Nos dois casos, quem sabe é o PTY.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# CSI, OSC, designação de charset, teclas de modo — tudo que não é texto.
_ANSI = re.compile(
    rb"\x1b\[[0-9;?<>=]*[a-zA-Z]"      # inclui os modos privados: \x1b[>4m, \x1b[<u
    rb"|\x1b[78]"                       # salvar e restaurar cursor
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    rb"|\x1b[()][AB0]"
    rb"|\x1b[>=<]"
    rb"|[\r\x0f\x0e]"
)

ENTER = b"\r"
DOWN = b"\x1b[B"
ESC = b"\x1b"


def screen_text(raw: bytes) -> str:
    """Bytes crus do PTY viram uma linha de texto comparável."""
    return " ".join(_ANSI.sub(b" ", raw).decode("utf-8", "replace").split())


def compact(text: str) -> str:
    """Sem espaço nenhum.

    A TUI reposiciona o cursor no meio das palavras, então a mesma frase aparece ora
    como "esc to interrupt", ora como "esctointerrupt", dependendo de onde caíram os
    escapes. Comparar sem espaço torna a detecção imune a isso.
    """
    return re.sub(r"\s+", "", text)


# Movimento de cursor ocupa espaço na tela; cor e limpeza, não.
_MOVE = re.compile(rb"\x1b\[[0-9;]*[CDGHf]")


def plain_lines(raw: bytes) -> list[str]:
    """O PTY sem ANSI, legível por uma pessoa.

    Diferente de `screen_text`, que achata tudo numa linha só para comparação. Aqui há uma
    sutileza: a TUI costuma escrever os espaços entre palavras como *movimento de cursor*,
    não como espaço literal. Apagar todo escape gruda as palavras ("esctointerrupt");
    trocar todo escape por espaço quebra palavras no meio, onde só houve troca de cor.
    Então movimento vira espaço, e o resto some.
    """
    texto = _ANSI.sub(b"", _MOVE.sub(b" ", raw)).decode("utf-8", "replace")
    linhas = [linha.rstrip() for linha in texto.replace("\r", "\n").split("\n")]
    return [linha for linha in linhas if linha.strip()]


WORKING_MARK = "esctointerrupt"     # rodapé da TUI enquanto há turno em andamento

# O ❯ sozinho NÃO identifica a caixa de entrada: é também o cursor de seleção dos
# diálogos ("❯ No, exit"). O que separa os dois é o que vem logo depois — a caixa
# desenha um espaço não-quebrável de padding, o cursor do diálogo emenda direto no
# texto da opção. Cuidado: `screen_text` normaliza espaços e come o nbsp, então esta
# comparação só funciona nos bytes crus.
PROMPT_BOX = "\u276f\u00a0"

# Todo modal da TUI anuncia como se responde a ele. Nenhum rodapé de sessão ociosa ou
# trabalhando ("esc to interrupt") usa essas frases.
DIALOG_MARKS = ("Enter to confirm", "Esc to cancel")


def prompt_box_visible(raw: bytes) -> bool:
    """A caixa de entrada está desenhada e nenhum diálogo por cima dela.

    Dois sinais, porque sozinho nenhum basta: o nbsp distingue a caixa do cursor de
    seleção, e a ausência dos rodapés de modal cobre o diálogo que a gente ainda não
    conhece — que é justamente o caso perigoso, o que nenhum `Gate` pega.
    """
    texto = _ANSI.sub(b"", raw).decode("utf-8", "replace")   # sem normalizar espaços
    if PROMPT_BOX not in texto:
        return False
    plano = compact(screen_text(raw))
    return not any(compact(marca) in plano for marca in DIALOG_MARKS)


@dataclass(frozen=True)
class Gate:
    name: str
    needles: tuple[str, ...]
    keys: bytes
    describe: str

    def matches(self, text: str) -> bool:
        flat = compact(text)
        return any(compact(n) in flat for n in self.needles)


# A ordem importa: o aceite do bypass aparece antes de tudo.
BOOT_GATES: tuple[Gate, ...] = (
    Gate(
        name="bypass",
        needles=("Yes, I accept",),
        keys=DOWN + ENTER,          # a opção 1 é "No, exit"
        describe="aviso do modo bypass permissions",
    ),
    Gate(
        name="trust",
        # O texto já mudou de redação entre versões; as antigas ficam para trás como
        # compatibilidade. O foco abre em "No, exit" — como no bypass —, então
        # responder com ENTER puro *sai* do Claude em vez de confiar no diretório.
        needles=(
            "Yes, I trust this folder",
            "Is this a project you created or one you trust",
            "trust the files in this folder",
            "Do you trust the files",
        ),
        keys=DOWN + ENTER,
        describe="diálogo de confiança do diretório",
    ),
)

_PERMISSION_NEEDLES = (
    "Do you want to proceed?",
    "This command requires approval",
    "Do you want to make this edit",
)
_HELD_NEEDLES = (
    "Held message from another session",
    "Deliver this message to Claude",
)
_OPTION = re.compile(r"(?:❯\s*)?([1-9])\.\s+([^0-9❯]{2,70}?)(?=\s+[1-9]\.\s|\s+Esc|\s*$)")


@dataclass(frozen=True)
class Block:
    """Uma sessão parada esperando decisão humana."""
    kind: str                       # "permission" | "held-message"
    options: list[tuple[str, str]]
    hint: str                       # o que fazer para liberar
    keys_to_allow: bytes            # teclas que liberam, sabendo qual opção está em foco


def find_boot_gate(text: str, answered: set[str]) -> Gate | None:
    for gate in BOOT_GATES:
        if gate.name not in answered and gate.matches(text):
            return gate
    return None


def find_block(text: str) -> Block | None:
    """O que está travando a sessão agora, lido da tela."""
    flat = compact(text)
    if any(compact(n) in flat for n in _HELD_NEEDLES):
        # Mensagem de peer retida porque as sessões estão em classes de permissão
        # diferentes. O foco começa em "Deny", então liberar exige descer uma linha.
        return Block(
            kind="held-message",
            options=[("1", "Deny — drop it and tell the sender it was declined"),
                     ("2", "Deliver this message to Claude")],
            hint="mensagem de peer retida: classes de permissão diferentes entre os níveis",
            keys_to_allow=DOWN + ENTER,
        )
    if any(compact(n) in flat for n in _PERMISSION_NEEDLES):
        tail = text[max(0, text.rfind("Do you want")):]
        options = [(n, label.strip(" ·")) for n, label in _OPTION.findall(tail)]
        return Block(
            kind="permission",
            options=options or [("1", "Yes")],
            hint="pedido de permissão de ferramenta",
            keys_to_allow=ENTER,
        )
    return None
