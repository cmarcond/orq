"""JSONL cru → eventos com significado.

O transcript já separa fala do modelo, mensagem de peer e tool call, então o viewer
não precisa de nenhum regex sobre saída de terminal. Cada evento sabe para qual painel
da interface ele vai.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

# painéis: palco (a narração limpa), fios (a conversa entre níveis), bastidores (o resto)
LANE = {
    "narration": "palco",
    "prompt": "palco",
    "wire_out": "fios",
    "wire_in": "fios",
    "activity": "bastidores",
    "activity_done": "bastidores",
    "thinking": "bastidores",
    "status": "bastidores",
    "cost": "bastidores",
    "level": "niveis",      # estado do próprio nível: sobe do orquestrador, não do JSONL
}

_ARG_HINT = ("command", "file_path", "path", "pattern", "query", "url", "prompt")


@dataclass
class Event:
    kind: str
    payload: dict[str, Any]
    ts: str = ""
    level: int = 0
    session: str = ""
    seq: int = 0

    @property
    def lane(self) -> str:
        return LANE.get(self.kind, "bastidores")

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq, "ts": self.ts, "level": self.level,
            "session": self.session, "kind": self.kind,
            "lane": self.lane, "payload": self.payload,
        }


def _brief(name: str, args: dict[str, Any]) -> str:
    for key in _ARG_HINT:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\n", " ")[:120]
    return ""


def _blocks(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Os blocos de conteúdo da mensagem. `content` também pode ser string crua."""
    content = record.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def normalize(record: dict[str, Any]) -> Iterator[Event]:
    kind = record.get("type")
    ts = record.get("timestamp", "")

    if kind == "assistant":
        for block in _blocks(record):
            btype = block.get("type")
            if btype == "text":
                text = block.get("text", "")
                if text.strip():
                    yield Event("narration", {"text": text}, ts)
            elif btype == "thinking":
                yield Event("thinking", {"text": block.get("thinking", "")}, ts)
            elif btype == "tool_use":
                tool, args = block.get("name", "?"), block.get("input", {}) or {}
                if tool == "SendMessage":
                    yield Event("wire_out", {
                        "to": args.get("to", ""),
                        "summary": args.get("summary", ""),
                        "message": args.get("message", ""),
                    }, ts)
                else:
                    yield Event("activity", {
                        "tool": tool, "brief": _brief(tool, args),
                        "id": block.get("id", ""),
                    }, ts)
        return

    if kind == "user":
        origin = record.get("origin") or {}
        if origin.get("kind") == "peer":
            # origin.body já vem sem o texto de embrulho que o Claude recebe.
            yield Event("wire_in", {
                "from": origin.get("name", "peer"),
                "body": origin.get("body", ""),
                "msg_id": origin.get("msg_id", ""),
            }, ts)
            return
        if origin.get("kind") == "human":
            yield Event("prompt", {"text": _plain(record)}, ts)
            return
        for block in _blocks(record):
            if block.get("type") != "tool_result":
                continue
            yield Event("activity_done", {
                "id": block.get("tool_use_id", ""),
                "ok": not block.get("is_error", False),
                "brief": _flatten(block.get("content"))[:160],
            }, ts)
        return

    if kind == "custom-title":
        yield Event("status", {"what": "nome", "name": record.get("title") or record.get("customTitle")}, ts)
    elif kind == "cost-state":
        yield Event("cost", {"usd": record.get("totalCostUSD") or record.get("costUSD")}, ts)


def _plain(record: dict[str, Any]) -> str:
    content = record.get("message", {}).get("content")
    return _flatten(content)


def _flatten(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or _flatten(block.get("content")) or "")
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return "" if content is None else str(content)
