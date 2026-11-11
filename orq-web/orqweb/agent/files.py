"""Files the agent shares with the page: the project's `_outbox/` folder.

The agent copies there whatever it wants to deliver (PDF, image, video…); the page lists and serves it.
Since the folder lives on the agent's machine, the server reaches it through `orq-agent ls` and
`orq-agent cat` (over ssh when remote), which are the two functions here: list the folder and send the
bytes of a file preceded by a JSON line with the size (that is what allows Content-Length and ranges in
the HTTP response).
"""

import json
import os
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Tuple

OUTBOX = "_outbox"
CHUNK = 256 * 1024


def list_dir(folder: Path) -> List[Dict[str, object]]:
    """Regular files of the folder (no hidden ones and no subfolders), newest to oldest.

    Missing folder = empty list: the agent has not shared anything yet."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    out = []
    for e in entries:
        if e.name.startswith(".") or not e.is_file():
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        out.append({"name": e.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    out.sort(key=lambda f: (-int(f["mtime"]), str(f["name"])))
    return out


def open_range(path: Path, start: int = 0, end: Optional[int] = None) -> Tuple[int, int, BinaryIO]:
    """Opens `path` positioned at `start`; returns (total size, bytes to read, file).

    `end` is inclusive, as in the Range header; None = to the end. Range outside the file -> ValueError."""
    size = path.stat().st_size
    if end is None or end >= size:
        end = size - 1
    if start < 0 or start > end + 1 or (size > 0 and start >= size):
        raise ValueError(f"range {start}-{end} outside the file of {size} bytes")
    f = path.open("rb")
    f.seek(start)
    return size, max(0, end - start + 1), f


def send(path: Path, out: BinaryIO, start: int = 0, end: Optional[int] = None) -> int:
    """Writes to `out` a JSON line {size, start, length} followed by the bytes of the range.

    Error: only the JSON line, with `err`. Returns 0 or 1 (exit code)."""
    try:
        size, length, f = open_range(path, start, end)
    except (OSError, ValueError) as e:
        out.write((json.dumps({"err": str(e)[:200]}) + "\n").encode())
        out.flush()
        return 1
    out.write((json.dumps({"size": size, "start": start, "length": length}) + "\n").encode())
    with f:
        left = length
        while left > 0:
            chunk = f.read(min(CHUNK, left))
            if not chunk:
                break
            out.write(chunk)
            left -= len(chunk)
    out.flush()
    return 0


def read_header(stream: BinaryIO) -> dict:
    """The JSON line that `send` puts before the bytes (the receiving side, local or over ssh)."""
    line = stream.readline()
    if not line:
        return {"err": "no response from the machine"}
    try:
        return json.loads(line)
    except ValueError:
        return {"err": (line.decode(errors="ignore").strip() or "invalid response")[:200]}


def parse_range(spec: Optional[str]) -> Tuple[int, Optional[int]]:
    """'5-9' -> (5, 9); '5-' -> (5, None); empty -> (0, None)."""
    if not spec:
        return 0, None
    a, _, b = spec.partition("-")
    return int(a or 0), (int(b) if b else None)
