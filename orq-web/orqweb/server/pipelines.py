"""Level recipes (`orqweb/pipelines/*.toml`): who is added above a page agent and with what role.

A recipe is data, not code: each `[[niveis]]` is one more level, from the top down. The tab's agent
is always the base (the executor) and gets no new role; the levels start on the same machine, folder and
account, and talk through Claude Code's native SendMessage/ListAgents. `{acima}`/`{abaixo}`/`{eu}` in the role
become the real names of the sessions (the ones SendMessage accepts, without `@`).

The TOML is read by `tomllib` (3.11+); on older Python a minimal reader covers the subset that
the recipes use (plain and triple-quoted strings, `[[tables]]`, comments).
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import host_of, session_name

PIPELINE_DIR = Path(__file__).resolve().parent.parent / "pipelines"
DEFAULT = "professor-executor"
SEP = "~"  # `kaggle~professor@server0`: level `professor` over `kaggle@server0`


@dataclass(frozen=True)
class Pipeline:
    id: str
    titulo: str
    descricao: str
    niveis: List[Dict[str, Any]]  # from the top down: nome, papel, cor

    @property
    def multi(self) -> bool:
        """Recipe whose bottom level coordinates several executors (uses `{executores}`): the agents
        linked (🔗) to the tab's one go in as executors and get a link to that level."""
        return any("{executores}" in str(n.get("papel", "")) for n in self.niveis)

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "titulo": self.titulo,
            "descricao": self.descricao,
            "niveis": [n["nome"] for n in self.niveis],
            "cores": {n["nome"]: n.get("cor", "") for n in self.niveis},
            "multi": self.multi,
        }


# ------------------------------------------------------------------ reading
def parse_toml(text: str) -> Dict[str, Any]:
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        return _parse_minimal(text)
    return tomllib.loads(text)


def _unquote(raw: str) -> str:
    """One-line string, basic or literal; what comes after the closing quote (comment) is ignored."""
    raw = raw.strip()
    if raw.startswith('"'):
        i, esc = 1, False
        while i < len(raw):
            c = raw[i]
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                return json.loads(raw[: i + 1])  # basic TOML escapes coincide with JSON's
            i += 1
        raise ValueError(f"unterminated string: {raw!r}")
    if raw.startswith("'"):
        end = raw.find("'", 1)
        if end < 0:
            raise ValueError(f"unterminated string: {raw!r}")
        return raw[1:end]
    raise ValueError(f"unsupported value: {raw!r}")


def _parse_minimal(text: str) -> Dict[str, Any]:
    """Subset of TOML: `k = "v"`, `k = '''v'''`/`k = \"\"\"v\"\"\"`, `[[table]]`, comments."""
    root: Dict[str, Any] = {}
    cur = root
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.startswith("[[") and line.endswith("]]"):
            cur = {}
            root.setdefault(line[2:-2].strip(), []).append(cur)
            continue
        if "=" not in line:
            raise ValueError(f"invalid line: {line!r}")
        key, raw = (s.strip() for s in line.split("=", 1))
        if raw[:3] in ('"""', "'''"):
            q = raw[:3]
            body = raw[3:]
            if body.endswith(q) and len(body) >= 3:
                cur[key] = body[:-3]
                continue
            parts = [body] if body else []
            while i < len(lines):
                ln = lines[i]
                i += 1
                if ln.rstrip().endswith(q):
                    parts.append(ln.rstrip()[:-3])
                    break
                parts.append(ln)
            cur[key] = "\n".join(parts)
            if q == '"""':
                cur[key] = cur[key].replace('\\"', '"').replace("\\\\", "\\")
            continue
        cur[key] = _unquote(raw)
    return root


def _read(path: Path) -> Pipeline:
    data = parse_toml(path.read_text(encoding="utf-8"))
    niveis = data.get("niveis") or []
    if not niveis:
        raise ValueError(f"{path.name}: no level defined")
    for n in niveis:
        if "nome" not in n or "papel" not in n:
            raise ValueError(f"{path.name}: every level needs `nome` and `papel`")
        if "@" in n["nome"] or SEP in n["nome"] or "/" in n["nome"]:
            raise ValueError(f"{path.name}: invalid level name: {n['nome']!r}")
    return Pipeline(
        id=path.stem,
        titulo=str(data.get("titulo") or path.stem),
        descricao=str(data.get("descricao") or ""),
        niveis=[dict(n) for n in niveis],
    )


def catalog(directory: Optional[Path] = None) -> Dict[str, Pipeline]:
    """Available recipes, by id (file name). A broken file is skipped, it does not bring the page down."""
    out: Dict[str, Pipeline] = {}
    for p in sorted((directory or PIPELINE_DIR).glob("*.toml")):
        if p.name.startswith("."):
            continue
        try:
            out[p.stem] = _read(p)
        except (OSError, ValueError):
            continue
    return out


def get(pipeline_id: Optional[str], directory: Optional[Path] = None) -> Pipeline:
    cat = catalog(directory)
    key = pipeline_id or DEFAULT
    if key not in cat:
        raise KeyError(f"unknown recipe: {key}")
    return cat[key]


# ------------------------------------------------------------------ assembly
def level_label(base_label: str, nome: str) -> str:
    """`kaggle@server0` + `professor` -> `kaggle~professor@server0`."""
    base = base_label.rsplit("@", 1)[0] if "@" in base_label else base_label
    return f"{base}{SEP}{nome}@{host_of(base_label)}"


def base_of_label(label: str) -> Optional[str]:
    """Label of the tab's agent from the label of a level; None if it is not a level."""
    if SEP not in label:
        return None
    base, _, rest = label.partition(SEP)
    host = rest.rsplit("@", 1)[1] if "@" in rest else ""
    return f"{base}@{host}" if host else base


def executors_list(base: dict, others: List[dict]) -> str:
    """`{executores}`: one line per executor (the tab's one first), with session name, machine and folder."""
    lines = []
    for a, tag in [(base, " (the one in the tab)")] + [(o, "") for o in others]:
        name = a.get("name") or session_name(a.get("label", ""))
        lines.append(f"- `{name}`{tag}: machine {a.get('host') or '?'}, folder {a.get('dir') or '?'}")
    return "\n".join(lines)


def plan_levels(p: Pipeline, base_label: str, executors: str = "") -> List[Dict[str, Any]]:
    """The levels to add over `base_label`, from the bottom up (the last one is the top).

    Each item: label, level (name), parent (label right below), role (role with the neighbours
    resolved) and color. The `{abaixo}` of the lowest level is the tab's agent itself; `{executores}`
    (`multi` recipes) is the ready-made list from `executors_list`, only in the lowest level.
    """
    labels = [level_label(base_label, n["nome"]) for n in p.niveis]  # top -> bottom
    out = []
    for i, n in enumerate(p.niveis):
        acima = labels[i - 1] if i > 0 else ""
        abaixo = labels[i + 1] if i + 1 < len(labels) else base_label
        bottom = i + 1 == len(labels)
        role = (
            str(n["papel"])
            .replace("{acima}", session_name(acima) if acima else "")
            .replace("{abaixo}", session_name(abaixo))
            .replace("{eu}", session_name(labels[i]))
            .replace("{executores}", executors if bottom else "")
            .strip()
        )
        out.append(
            {
                "label": labels[i],
                "level": n["nome"],
                "parent": abaixo,
                "role": role,
                "color": n.get("cor", ""),
            }
        )
    out.reverse()
    return out


def levels_above(agents: List[dict], label: str) -> List[str]:
    """Labels of the live levels over `label`, from the bottom up, following `parent`."""
    out: List[str] = []
    cur = label
    while True:
        nxt = next((a for a in agents if a.get("parent") == cur and a.get("state") != "closed"), None)
        if nxt is None or nxt["label"] in out or nxt["label"] == label:
            break
        out.append(nxt["label"])
        cur = nxt["label"]
    return out
