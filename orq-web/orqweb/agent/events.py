"""Eventos do agente: log numerado com replay, carimbo de origem, estado derivado e histórico antigo.

Tudo aqui é puro (sem processo, sem socket) para ser testável. As linhas são bytes JSON com `\\n`
no fim, no mesmo formato do stream-json do `claude -p`, mais dois tipos próprios:

- `{"type": "orq", "level": ..., "text": ...}`: aviso do supervisor;
- `{"type": "orq", "subtype": "permission_answered", ...}`: a página respondeu uma permissão.
"""

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPLAY_MAX = 400  # entradas do histórico antigo reenviadas ao retomar


def encode(obj: Dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


def orq_event(level: str, text: str, **extra: Any) -> bytes:
    e = {"type": "orq", "level": level, "text": text, "ts": time.time()}
    e.update(extra)
    return encode(e)


def text_of(message: Optional[Dict[str, Any]]) -> str:
    """Texto de uma mensagem user/assistant: string, ou os blocos `text` concatenados."""
    c = (message or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


class EventLog:
    """Linhas persistidas em memória e, opcionalmente, em disco. O índice é o número de sequência."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.lines: List[bytes] = []
        self.lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")

    def append(self, line: bytes) -> int:
        """Guarda e devolve a sequência (1-based) da linha."""
        with self.lock:
            self.lines.append(line)
            if self.path is not None:
                with open(self.path, "ab") as fh:
                    fh.write(line)
            return len(self.lines)

    def since(self, n: int) -> List[bytes]:
        with self.lock:
            return list(self.lines[max(0, n) :])

    def __len__(self) -> int:
        return len(self.lines)


class SentTexts:
    """Textos que a página mandou e ainda não voltaram no eco do claude (para o carimbo `page`)."""

    def __init__(self, keep: int = 50):
        self.keep = keep
        self._items: List[str] = []
        self._lock = threading.Lock()

    def add(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._items = (self._items + [text])[-self.keep :]

    def take(self, text: str) -> bool:
        """True se `text` era nosso; consome uma ocorrência."""
        with self._lock:
            if text in self._items:
                self._items.remove(text)
                return True
            return False


def stamp_origin(event: Dict[str, Any], sent: SentTexts) -> Dict[str, Any]:
    """Carimba `orq_origin` numa mensagem `user` vinda do claude.

    - `agent`: veio de outra sessão do Claude Code pelo SendMessage (origin.kind == "peer"; o texto
      limpo está em origin.body e quem mandou em origin.name);
    - `page`: eco de algo que a página mandou (o claude re-emite com --replay-user-messages);
    - `injected`: sem marca de humano (origin.kind) nem de cliente (promptSource): cron, loop, sistema;
    - sem carimbo: mensagem humana digitada no terminal, ou meta.
    """
    if event.get("type") != "user" or event.get("isMeta"):
        return event
    txt = text_of(event.get("message"))
    if not txt:
        return event
    if (event.get("origin") or {}).get("kind") == "peer":
        event["orq_origin"] = "agent"
    elif sent.take(txt):
        event["orq_origin"] = "page"
    elif (event.get("origin") or {}).get("kind") != "human" and event.get("promptSource") not in (
        "sdk",
        "typed",
    ):
        event["orq_origin"] = "injected"
    return event


def _short_input(inp: Dict[str, Any]) -> str:
    for k in ("command", "file_path", "pattern", "url", "query", "description"):
        v = inp.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def derive_state(event: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Estado do agente implicado por um evento do claude: (estado, mensagem curta), ou None se não muda."""
    t = event.get("type")
    if t == "control_request" and (event.get("request") or {}).get("subtype") == "can_use_tool":
        r = event["request"]
        short = _short_input(r.get("input") or {})
        return "permission", "{} {}".format(r.get("tool_name", ""), short[:60]).strip()
    if t == "result":
        if event.get("is_error"):
            return "error", str(event.get("result") or event.get("subtype") or "erro")[:120]
        return "waiting", ""
    if t == "assistant":
        return "working", ""
    return None


def derive_state_from_client(obj: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Estado implicado por algo que o cliente mandou: mensagem nova ou resposta de permissão."""
    if obj.get("type") in ("user", "control_response"):
        return "working", ""
    return None


def permission_answered(obj: Dict[str, Any]) -> Optional[bytes]:
    """Evento próprio registrando a resposta da página a uma permissão (para o replay fechar o cartão)."""
    if obj.get("type") != "control_response":
        return None
    resp = obj.get("response") or {}
    return orq_event(
        "info",
        "",
        subtype="permission_answered",
        request_id=resp.get("request_id"),
        behavior=(resp.get("response") or {}).get("behavior"),
    )


def project_dir(config_dir: Path, cwd: Path) -> Path:
    """Pasta onde o Claude Code guarda as sessões de `cwd` nesta conta."""
    return config_dir / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))


def transcript_entries(path: Path, limit: int = REPLAY_MAX) -> List[Dict[str, Any]]:
    """Mensagens user/assistant de uma sessão gravada, sem sidechains, marcadas `replay`.

    Devolve só as últimas `limit`.
    """
    keep: List[Dict[str, Any]] = []
    try:
        with open(path, "rb") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") not in ("user", "assistant") or o.get("isSidechain"):
                    continue
                entry = {
                    "type": o["type"],
                    "message": o.get("message"),
                    "uuid": o.get("uuid"),
                    "timestamp": o.get("timestamp"),
                    "replay": True,
                }
                for k in ("isMeta", "origin", "promptSource"):
                    if k in o:
                        entry[k] = o[k]
                keep.append(entry)
    except OSError:
        return []
    return keep[-limit:]


def iter_json_lines(lines: Iterable[bytes]) -> Iterable[Dict[str, Any]]:
    for line in lines:
        try:
            yield json.loads(line)
        except ValueError:
            continue
