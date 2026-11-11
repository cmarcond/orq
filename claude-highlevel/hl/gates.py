"""Boot gates and block detection, read from the terminal screen.

A freshly opened session can stop at a dialog before writing anything to the
transcript: directory trust, accepting bypass mode. And, in the middle of the work, a
permission request freezes the session without leaving a trace in the JSONL — the `tool_use` has
already been recorded, the `tool_result` never arrives. In both cases, the one that knows is the PTY.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# CSI, OSC, charset designation, mode keys — everything that is not text.
_ANSI = re.compile(
    rb"\x1b\[[0-9;?<>=]*[a-zA-Z]"      # includes the private modes: \x1b[>4m, \x1b[<u
    rb"|\x1b[78]"                       # save and restore cursor
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    rb"|\x1b[()][AB0]"
    rb"|\x1b[>=<]"
    rb"|[\r\x0f\x0e]"
)

ENTER = b"\r"
DOWN = b"\x1b[B"
ESC = b"\x1b"


def screen_text(raw: bytes) -> str:
    """Raw PTY bytes become one comparable line of text."""
    return " ".join(_ANSI.sub(b" ", raw).decode("utf-8", "replace").split())


def compact(text: str) -> str:
    """No whitespace at all.

    The TUI repositions the cursor in the middle of words, so the same phrase shows up
    sometimes as "esc to interrupt", sometimes as "esctointerrupt", depending on where the
    escapes landed. Comparing without whitespace makes the detection immune to that.
    """
    return re.sub(r"\s+", "", text)


# Cursor movement takes up space on the screen; colour and clearing do not.
_MOVE = re.compile(rb"\x1b\[[0-9;]*[CDGHf]")


def plain_lines(raw: bytes) -> list[str]:
    """The PTY without ANSI, readable by a person.

    Unlike `screen_text`, which flattens everything into a single line for comparison. There is a
    subtlety here: the TUI usually writes the spaces between words as *cursor movement*,
    not as a literal space. Deleting every escape glues the words together ("esctointerrupt");
    replacing every escape with a space breaks words in the middle, where there was only a colour change.
    So movement becomes a space, and the rest disappears.
    """
    texto = _ANSI.sub(b"", _MOVE.sub(b" ", raw)).decode("utf-8", "replace")
    linhas = [linha.rstrip() for linha in texto.replace("\r", "\n").split("\n")]
    return [linha for linha in linhas if linha.strip()]


WORKING_MARK = "esctointerrupt"     # TUI footer while a turn is in progress

# The ❯ alone does NOT identify the input box: it is also the selection cursor of the
# dialogs ("❯ No, exit"). What separates the two is what comes right after — the box
# draws a non-breaking space as padding, the dialog cursor runs straight into the
# option text. Careful: `screen_text` normalizes whitespace and eats the nbsp, so this
# comparison only works on the raw bytes.
PROMPT_BOX = "\u276f\u00a0"

# Every TUI modal announces how to answer it. No footer of an idle or working session
# ("esc to interrupt") uses these phrases.
DIALOG_MARKS = ("Enter to confirm", "Esc to cancel")


def prompt_box_visible(raw: bytes) -> bool:
    """The input box is drawn and there is no dialog on top of it.

    Two signals, because neither is enough alone: the nbsp tells the box from the selection
    cursor, and the absence of the modal footers covers the dialog we do not know about
    yet — which is precisely the dangerous case, the one no `Gate` catches.
    """
    texto = _ANSI.sub(b"", raw).decode("utf-8", "replace")   # without normalizing whitespace
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


# Order matters: the bypass acceptance shows up before everything else.
BOOT_GATES: tuple[Gate, ...] = (
    Gate(
        name="bypass",
        needles=("Yes, I accept",),
        keys=DOWN + ENTER,          # option 1 is "No, exit"
        describe="bypass permissions mode warning",
    ),
    Gate(
        name="trust",
        # The wording has already changed between versions; the old ones stay behind for
        # compatibility. The focus opens on "No, exit" — as in bypass —, so
        # answering with a bare ENTER *exits* Claude instead of trusting the directory.
        needles=(
            "Yes, I trust this folder",
            "Is this a project you created or one you trust",
            "trust the files in this folder",
            "Do you trust the files",
        ),
        keys=DOWN + ENTER,
        describe="directory trust dialog",
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
    """A session stopped waiting for a human decision."""
    kind: str                       # "permission" | "held-message"
    options: list[tuple[str, str]]
    hint: str                       # what to do to unblock it
    keys_to_allow: bytes            # keys that unblock it, knowing which option is focused


def find_boot_gate(text: str, answered: set[str]) -> Gate | None:
    for gate in BOOT_GATES:
        if gate.name not in answered and gate.matches(text):
            return gate
    return None


def find_block(text: str) -> Block | None:
    """What is blocking the session right now, read from the screen."""
    flat = compact(text)
    if any(compact(n) in flat for n in _HELD_NEEDLES):
        # Peer message held because the sessions are in different permission
        # classes. The focus starts on "Deny", so allowing requires moving down one line.
        return Block(
            kind="held-message",
            options=[("1", "Deny — drop it and tell the sender it was declined"),
                     ("2", "Deliver this message to Claude")],
            hint="peer message held: different permission classes between the levels",
            keys_to_allow=DOWN + ENTER,
        )
    if any(compact(n) in flat for n in _PERMISSION_NEEDLES):
        tail = text[max(0, text.rfind("Do you want")):]
        options = [(n, label.strip(" ·")) for n, label in _OPTION.findall(tail)]
        return Block(
            kind="permission",
            options=options or [("1", "Yes")],
            hint="tool permission request",
            keys_to_allow=ENTER,
        )
    return None
