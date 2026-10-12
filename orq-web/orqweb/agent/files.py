"""Arquivos que o agente compartilha com a página: a pasta `_outbox/` do projeto.

O agente copia para lá o que quer entregar (PDF, imagem, vídeo…); a página lista e serve. Como a
pasta fica na máquina do agente, o servidor chega nela por `orq-agent ls` e `orq-agent cat` (por
ssh quando remota), que são as duas funções daqui: listar a pasta e mandar os bytes de um arquivo
precedidos de uma linha JSON com o tamanho (é o que permite Content-Length e faixas na resposta HTTP).
"""

import json
import os
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Tuple

OUTBOX = "_outbox"
CHUNK = 256 * 1024


def list_dir(folder: Path) -> List[Dict[str, object]]:
    """Arquivos comuns da pasta (sem os ocultos e sem subpastas), do mais novo para o mais antigo.

    Pasta inexistente = lista vazia: o agente ainda não compartilhou nada."""
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
    """Abre `path` posicionado em `start`; devolve (tamanho total, bytes a ler, arquivo).

    `end` é inclusivo, como no cabeçalho Range; None = até o fim. Faixa fora do arquivo -> ValueError."""
    size = path.stat().st_size
    if end is None or end >= size:
        end = size - 1
    if start < 0 or start > end + 1 or (size > 0 and start >= size):
        raise ValueError(f"faixa {start}-{end} fora do arquivo de {size} bytes")
    f = path.open("rb")
    f.seek(start)
    return size, max(0, end - start + 1), f


def send(path: Path, out: BinaryIO, start: int = 0, end: Optional[int] = None) -> int:
    """Escreve em `out` uma linha JSON {size, start, length} e em seguida os bytes da faixa.

    Erro: só a linha JSON, com `err`. Devolve 0 ou 1 (código de saída)."""
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
    """A linha JSON que `send` põe antes dos bytes (o lado que recebe, local ou por ssh)."""
    line = stream.readline()
    if not line:
        return {"err": "sem resposta da máquina"}
    try:
        return json.loads(line)
    except ValueError:
        return {"err": (line.decode(errors="ignore").strip() or "resposta inválida")[:200]}


def parse_range(spec: Optional[str]) -> Tuple[int, Optional[int]]:
    """'5-9' -> (5, 9); '5-' -> (5, None); vazio -> (0, None)."""
    if not spec:
        return 0, None
    a, _, b = spec.partition("-")
    return int(a or 0), (int(b) if b else None)
