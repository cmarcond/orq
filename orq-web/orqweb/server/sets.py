"""Sets: a JSON that describes agents to open together, with or without a leading teacher.

A set is data, like the level recipes: it lives in `~/.config/orq-web/conjuntos/<id>.json`
(`ORQ_SETS_DIR`), and the page lists, opens, receives a ready-made one or snapshots the open tabs into a
new one.

```json
{"nome": "artigo-kaggle", "descricao": "…", "permissao": "auto",
 "agentes": [{"maquina": "server0", "pasta": "~/Projects/kaggle", "modo": "resume",
              "conta": "work3", "prompt": "You look after the model…", "tarefa": "Resume and report."}],
 "lider": {"receita": "professor-executores", "prompt": "…", "tarefa": "…"}}
```

Without `lider`, each agent starts independently. With `lider`, the teacher is added above the first agent
and the others are linked to it (recipe `professor-executores`), as the page's ⇅ already does. This module is
pure (format, validation, catalog, plan and snapshot); what actually opens is `handlers.open_set_job`.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import pipelines
from .assign import assign, label_for

DEFAULT_DIR = Path.home() / ".config/orq-web/conjuntos"
MODES = ("resume", "new")
PERMISSIONS = ("auto", "manual", "acceptEdits", "plan")
DEFAULT_RECIPE = "professor-executores"
SET_KEYS = {"nome", "descricao", "permissao", "agentes", "lider"}
AGENT_KEYS = {"maquina", "pasta", "modo", "conta", "prompt", "tarefa"}
LEADER_KEYS = {"receita", "prompt", "tarefa"}


def sets_dir() -> Path:
    return Path(os.path.expanduser(os.environ.get("ORQ_SETS_DIR") or str(DEFAULT_DIR)))


def set_id(nome: Any) -> str:
    """Id (file name) from the name: lower case, `[a-z0-9_-]`, up to 60 characters."""
    s = re.sub(r"[^a-z0-9_-]+", "-", str(nome or "").strip().lower()).strip("-")
    return s[:60].rstrip("-")


# ------------------------------------------------------------------ validation
def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def validate(data: Any, hosts: Iterable[str], accounts: Iterable[str], recipes: Iterable[str]) -> dict:
    """Checks and normalises a set. Raises ValueError with all the problems at once."""
    errs: List[str] = []
    if not isinstance(data, dict):
        raise ValueError("the set must be a JSON object")
    for k in data:
        if k not in SET_KEYS:
            errs.append(f"unknown field: {k}")
    out: Dict[str, Any] = {"nome": _text(data.get("nome")), "descricao": _text(data.get("descricao"))}
    if not set_id(out["nome"]):
        errs.append("`nome` is required (it becomes the id of the file)")
    perm = _text(data.get("permissao"))
    if perm and perm not in PERMISSIONS:
        errs.append(f"`permissao` must be one of {', '.join(PERMISSIONS)}: {perm!r}")
    out["permissao"] = perm or None
    hosts, accounts, recipes = list(hosts), list(accounts), list(recipes)
    agentes = data.get("agentes")
    if not isinstance(agentes, list) or not agentes:
        errs.append("`agentes` must be a list with at least one agent")
        agentes = []
    seen: List[Tuple[str, str]] = []
    out["agentes"] = []
    for i, a in enumerate(agentes, 1):
        if not isinstance(a, dict):
            errs.append(f"agent {i}: must be an object")
            continue
        for k in a:
            if k not in AGENT_KEYS:
                errs.append(f"agent {i}: unknown field: {k}")
        host, cwd = _text(a.get("maquina")), _text(a.get("pasta")).rstrip("/") or _text(a.get("pasta"))
        if host not in hosts:
            errs.append(f"agent {i}: `maquina` {host!r} is not configured ({', '.join(hosts) or 'none'})")
        if not cwd:
            errs.append(f"agent {i}: `pasta` is required")
        mode = _text(a.get("modo")) or "resume"
        if mode not in MODES:
            errs.append(f"agent {i}: `modo` must be resume or new: {mode!r}")
        acc = _text(a.get("conta")) or None
        if acc and acc not in accounts:
            errs.append(f"agent {i}: unknown `conta` {acc!r} ({', '.join(accounts) or 'none'})")
        if host and cwd:
            if any(h == host and same_dir(c, cwd) for h, c in seen):
                errs.append(f"agent {i}: {host}:{cwd} repeated in the set")
            seen.append((host, cwd))
        out["agentes"].append(
            {
                "maquina": host,
                "pasta": cwd,
                "modo": mode,
                "conta": acc,
                "prompt": _text(a.get("prompt")),
                "tarefa": _text(a.get("tarefa")),
            }
        )
    lider = data.get("lider")
    out["lider"] = None
    if lider is not None:
        if not isinstance(lider, dict):
            errs.append("`lider` must be an object (or be left out)")
        else:
            for k in lider:
                if k not in LEADER_KEYS:
                    errs.append(f"lider: unknown field: {k}")
            receita = _text(lider.get("receita")) or DEFAULT_RECIPE
            if receita not in recipes:
                errs.append(f"lider: unknown `receita` {receita!r} ({', '.join(recipes) or 'none'})")
            out["lider"] = {
                "receita": receita,
                "prompt": _text(lider.get("prompt")),
                "tarefa": _text(lider.get("tarefa")),
            }
    if errs:
        raise ValueError("; ".join(errs))
    return out


def same_dir(a: str, b: str) -> bool:
    """`~/Projects/x` and `/home/user/Projects/x` are the same folder; the server does not know the remote
    HOME, so it compares the path relative to the home folder when it can, and the whole path when not."""

    def rel(p: str) -> Tuple[bool, str]:
        p = p.strip().rstrip("/")
        if p.startswith("~/"):
            return True, p[2:]
        m = re.match(r"^/(?:home|Users)/[^/]+/(.*)$", p)
        return (True, m.group(1)) if m else (False, p)

    ra, rb = rel(a), rel(b)
    return ra == rb


# ------------------------------------------------------------------ catalog on disk
def _path(directory: Optional[Path], sid: str) -> Path:
    return (directory or sets_dir()) / f"{sid}.json"


def catalog(directory: Optional[Path] = None) -> Dict[str, dict]:
    """Saved sets, by id. A broken file is skipped, it does not bring the page down."""
    out: Dict[str, dict] = {}
    d = directory or sets_dir()
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        if p.name.startswith("."):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out[p.stem] = data
    return out


def load(sid: str, directory: Optional[Path] = None) -> dict:
    if sid != set_id(sid) or not sid:
        raise KeyError(f"unknown set: {sid}")
    try:
        data = json.loads(_path(directory, sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError(f"unknown set: {sid}") from None
    if not isinstance(data, dict):
        raise KeyError(f"unknown set: {sid}")
    return data


def save(data: dict, directory: Optional[Path] = None) -> str:
    """Writes the set (already validated) and returns the id."""
    sid = set_id(data.get("nome"))
    p = _path(directory, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(clean(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(p))
    return sid


def delete(sid: str, directory: Optional[Path] = None) -> bool:
    if sid != set_id(sid) or not sid:
        return False
    try:
        _path(directory, sid).unlink()
        return True
    except OSError:
        return False


def clean(data: dict) -> dict:
    """The JSON as it goes to the file: without empty fields, in the order of the format."""
    out: Dict[str, Any] = {"nome": data.get("nome", "")}
    if data.get("descricao"):
        out["descricao"] = data["descricao"]
    if data.get("permissao"):
        out["permissao"] = data["permissao"]
    out["agentes"] = []
    for a in data.get("agentes") or []:
        item = {
            "maquina": a.get("maquina", ""),
            "pasta": a.get("pasta", ""),
            "modo": a.get("modo") or "resume",
        }
        for k in ("conta", "prompt", "tarefa"):
            if a.get(k):
                item[k] = a[k]
        out["agentes"].append(item)
    if data.get("lider"):
        ld = {"receita": data["lider"].get("receita") or DEFAULT_RECIPE}
        for k in ("prompt", "tarefa"):
            if data["lider"].get(k):
                ld[k] = data["lider"][k]
        out["lider"] = ld
    return out


# ------------------------------------------------------------------ open: conflicts and plan
def live_in(agents: Iterable[dict], host: str, cwd: str) -> List[str]:
    """Labels of the live agents (page or terminal) on that machine and folder."""
    return [
        a["label"]
        for a in agents
        if a.get("state") != "closed"
        and a.get("host") == host
        and a.get("label")
        and not a.get("parent")
        and same_dir(a.get("dir") or "", cwd)
    ]


def conflicts(data: dict, agents: Iterable[dict]) -> List[str]:
    """Agents already open that occupy some machine:folder of the set (the page refuses to open)."""
    agents = list(agents)
    out: List[str] = []
    for a in data.get("agentes") or []:
        out += [lab for lab in live_in(agents, a["maquina"], a["pasta"]) if lab not in out]
    return out


def plan(
    data: dict, taken: Iterable[str], usage: Dict[str, dict], accounts: List[str], running: Iterable[str]
) -> List[dict]:
    """One item per agent, in the order of the JSON: host, cwd, mode, label (unique among the live ones), acc
    (fixed or chosen by slack, for the whole set at once), why, prompt, tarefa."""
    taken = set(taken)
    items = []
    for a in data["agentes"]:
        label = label_for(a["pasta"], a["maquina"], taken)
        taken.add(label)
        it = {
            "host": a["maquina"],
            "cwd": a["pasta"],
            "mode": a.get("modo") or "resume",
            "label": label,
            "account": None,
            "prompt": a.get("prompt") or "",
            "tarefa": a.get("tarefa") or "",
        }
        if a.get("conta"):
            it["acc"], it["why"] = a["conta"], "fixed in the set"
        items.append(it)
    assign(items, usage, accounts, running)
    return items


# ------------------------------------------------------------------ summary and snapshot
def summary(sid: str, data: dict, agents: Iterable[dict] = ()) -> dict:
    ag = data.get("agentes") or []
    ag = [a for a in ag if isinstance(a, dict)]
    return {
        "id": sid,
        "nome": str(data.get("nome") or sid),
        "descricao": str(data.get("descricao") or ""),
        "n": len(ag),
        "maquinas": sorted({str(a.get("maquina") or "?") for a in ag}),
        "lider": bool(data.get("lider")),
        "permissao": data.get("permissao") or "",
        "abertos": conflicts({"agentes": [a for a in ag if a.get("maquina") and a.get("pasta")]}, agents),
    }


def infer_recipe(level_names: List[str], has_links: bool, cat: Dict[str, "pipelines.Pipeline"]) -> str:
    """Recipe whose levels (from the top down) match the ones that are up; with linked agents, the `multi`."""
    match = [p for p in cat.values() if [n["nome"] for n in p.niveis] == level_names]
    for p in match:
        if p.multi == has_links:
            return p.id
    return match[0].id if match else DEFAULT_RECIPE


def snapshot(
    agents: List[dict], links: List[dict], cat: Optional[Dict[str, Any]] = None
) -> Tuple[dict, List[str]]:
    """A set from what is open: one agent per tab (resume mode, current account, prompt if it
    started with one), and, if some tab has levels above, it becomes the first and `lider` gets the recipe.
    Returns (set, warnings)."""
    cat = cat if cat is not None else pipelines.catalog()
    live = [a for a in agents if a.get("state") != "closed" and a.get("kind") == "sdk" and a.get("label")]
    bases = [a for a in live if not a.get("parent")]
    avisos: List[str] = []
    with_levels = [b for b in bases if pipelines.levels_above(live, b["label"])]
    first = with_levels[0] if with_levels else None
    if len(with_levels) > 1:
        avisos.append(
            "only the first teacher goes in as leader; the ones of "
            + ", ".join(b["label"] for b in with_levels[1:])
            + " are left out"
        )
    ordered = ([first] if first else []) + [b for b in bases if b is not first]
    perms = {b.get("permission") for b in ordered if b.get("permission")}
    out: Dict[str, Any] = {"nome": "", "descricao": "", "agentes": []}
    if len(perms) == 1:
        out["permissao"] = perms.pop()
    elif len(perms) > 1:
        avisos.append("the open agents have different permission modes; the set is left without `permissao`")
    for b in ordered:
        item = {"maquina": b.get("host") or "?", "pasta": b.get("dir") or "", "modo": "resume"}
        if b.get("account"):
            item["conta"] = b["account"]
        if b.get("role"):
            item["prompt"] = b["role"]
        out["agentes"].append(item)
    if first:
        above = pipelines.levels_above(live, first["label"])
        lv = [next((a for a in live if a.get("label") == lab), {}) for lab in reversed(above)]
        recorded = next((a.get("pipeline") for a in lv if a.get("pipeline") in cat), None)
        if recorded:
            receita = recorded
        else:  # level from before this version, without `pipeline` in the state: by the names and the links
            has_links = any(
                first["label"] in (lk.get("local"), lk.get("remote")) and lk.get("up", True) for lk in links
            )
            receita = infer_recipe([a.get("level") for a in lv if a.get("level")], has_links, cat)
        out["lider"] = {"receita": receita}
    return out, avisos
