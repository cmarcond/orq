"""Agent events: numbered log with replay, origin stamp, derived state and old history.

Everything here is pure (no process, no socket) so it can be tested. The lines are JSON bytes ending
in `\\n`, in the same format as the stream-json of `claude -p`, plus two types of our own:

- `{"type": "orq", "level": ..., "text": ...}`: notice from the supervisor;
- `{"type": "orq", "subtype": "permission_answered", ...}`: the page answered a permission.
"""

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPLAY_MAX = 400  # entries of the old history resent on resume


def encode(obj: Dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


def orq_event(level: str, text: str, **extra: Any) -> bytes:
    e = {"type": "orq", "level": level, "text": text, "ts": time.time()}
    e.update(extra)
    return encode(e)


def text_of(message: Optional[Dict[str, Any]]) -> str:
    """Text of a user/assistant message: a string, or the `text` blocks concatenated."""
    c = (message or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


class EventLog:
    """Lines persisted in memory and, optionally, on disk. The index is the sequence number."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.lines: List[bytes] = []
        self.lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")

    def append(self, line: bytes) -> int:
        """Stores the line and returns its sequence (1-based)."""
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
    """Texts the page sent that have not yet come back in claude's echo (for the `page` stamp)."""

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
        """True if `text` was ours; consumes one occurrence."""
        with self._lock:
            if text in self._items:
                self._items.remove(text)
                return True
            return False


def stamp_origin(event: Dict[str, Any], sent: SentTexts) -> Dict[str, Any]:
    """Stamps `orq_origin` on a `user` message coming from claude.

    - `agent`: came from another Claude Code session through SendMessage (origin.kind == "peer"; the
      clean text is in origin.body and the sender in origin.name);
    - `page`: echo of something the page sent (claude re-emits it with --replay-user-messages);
    - `injected`: no mark of a human (origin.kind) nor of a client (promptSource): cron, loop, system;
    - no stamp: human message typed in the terminal, or meta.
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


QUOTA_RE = re.compile(
    r"(usage|rate) limit|limit reached|hit your limit|out of (extra )?usage|too many requests|"
    r"quota|cota|resets? (at|in) ",
    re.I,
)


def quota_exhausted(event: Dict[str, Any]) -> bool:
    """This agent's account ran out of quota: claude signals it in a `rate_limit_event` with status
    `rejected`, or the turn ends in an error that mentions a limit."""
    t = event.get("type")
    if t == "rate_limit_event":
        return (event.get("rate_limit_info") or {}).get("status") == "rejected"
    if t == "result" and event.get("is_error"):
        return bool(QUOTA_RE.search(str(event.get("result") or event.get("errors") or "")))
    return False


def derive_state(event: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Agent state implied by a claude event: (state, short message), or None if it does not change.

    `quota` = the account ran out of quota; the server migrates the agent to another account (`msg` gives
    the reason).
    """
    t = event.get("type")
    if quota_exhausted(event):
        info = event.get("rate_limit_info") or {}
        why = (
            str(event.get("result") or "").strip()[:120]
            or f"quota exhausted ({info.get('rateLimitType', '')})"
        )
        return "quota", why
    if t == "control_request" and (event.get("request") or {}).get("subtype") == "can_use_tool":
        r = event["request"]
        short = _short_input(r.get("input") or {})
        return "permission", "{} {}".format(r.get("tool_name", ""), short[:60]).strip()
    if t == "result":
        if event.get("is_error"):
            return "error", str(event.get("result") or event.get("subtype") or "error")[:120]
        return "waiting", ""
    if t == "assistant":
        return "working", ""
    return None


def derive_state_from_client(obj: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """State implied by something the client sent: a new message or a permission answer."""
    if obj.get("type") in ("user", "control_response"):
        return "working", ""
    return None


def permission_answered(obj: Dict[str, Any]) -> Optional[bytes]:
    """Our own event recording the page's answer to a permission (so the replay closes the card)."""
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
    """Folder where Claude Code keeps the sessions of `cwd` under this account."""
    return config_dir / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))


def transcript_entries(path: Path, limit: int = REPLAY_MAX) -> List[Dict[str, Any]]:
    """user/assistant messages of a recorded session, without sidechains, marked `replay`.

    Returns only the last `limit`.
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
