"""Conjuntos: um JSON que descreve agentes para abrir juntos, com ou sem um professor líder.

Um conjunto é dado, como as receitas de níveis: fica em `~/.config/orq-web/conjuntos/<id>.json`
(`ORQ_SETS_DIR`), e a página lista, abre, recebe um pronto ou fotografa as abas abertas num novo.

```json
{"nome": "artigo-kaggle", "descricao": "…", "permissao": "auto",
 "agentes": [{"maquina": "server0", "pasta": "~/Projects/kaggle", "modo": "resume",
              "conta": "work3", "prompt": "Você cuida do modelo…", "tarefa": "Retome e reporte."}],
 "lider": {"receita": "professor-executores", "prompt": "…", "tarefa": "…"}}
```

Sem `lider`, cada agente sobe independente. Com `lider`, o professor sobe acima do primeiro agente e os
demais são vinculados a ele (receita `professor-executores`), como o ⇅ da página já faz. Este módulo é
puro (formato, validação, catálogo, plano e fotografia); quem abre de fato é `handlers.open_set_job`.
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
    """Id (nome do arquivo) a partir do nome: minúsculas, `[a-z0-9_-]`, até 60 caracteres."""
    s = re.sub(r"[^a-z0-9_-]+", "-", str(nome or "").strip().lower()).strip("-")
    return s[:60].rstrip("-")


# ------------------------------------------------------------------ validação
def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def validate(data: Any, hosts: Iterable[str], accounts: Iterable[str], recipes: Iterable[str]) -> dict:
    """Confere e normaliza um conjunto. Levanta ValueError com todos os problemas de uma vez."""
    errs: List[str] = []
    if not isinstance(data, dict):
        raise ValueError("o conjunto precisa ser um objeto JSON")
    for k in data:
        if k not in SET_KEYS:
            errs.append(f"campo desconhecido: {k}")
    out: Dict[str, Any] = {"nome": _text(data.get("nome")), "descricao": _text(data.get("descricao"))}
    if not set_id(out["nome"]):
        errs.append("`nome` é obrigatório (vira o id do arquivo)")
    perm = _text(data.get("permissao"))
    if perm and perm not in PERMISSIONS:
        errs.append(f"`permissao` precisa ser um de {', '.join(PERMISSIONS)}: {perm!r}")
    out["permissao"] = perm or None
    hosts, accounts, recipes = list(hosts), list(accounts), list(recipes)
    agentes = data.get("agentes")
    if not isinstance(agentes, list) or not agentes:
        errs.append("`agentes` precisa ser uma lista com pelo menos um agente")
        agentes = []
    seen: List[Tuple[str, str]] = []
    out["agentes"] = []
    for i, a in enumerate(agentes, 1):
        if not isinstance(a, dict):
            errs.append(f"agente {i}: precisa ser um objeto")
            continue
        for k in a:
            if k not in AGENT_KEYS:
                errs.append(f"agente {i}: campo desconhecido: {k}")
        host, cwd = _text(a.get("maquina")), _text(a.get("pasta")).rstrip("/") or _text(a.get("pasta"))
        if host not in hosts:
            errs.append(
                f"agente {i}: `maquina` {host!r} não está configurada ({', '.join(hosts) or 'nenhuma'})"
            )
        if not cwd:
            errs.append(f"agente {i}: `pasta` é obrigatória")
        mode = _text(a.get("modo")) or "resume"
        if mode not in MODES:
            errs.append(f"agente {i}: `modo` precisa ser resume ou new: {mode!r}")
        acc = _text(a.get("conta")) or None
        if acc and acc not in accounts:
            errs.append(f"agente {i}: `conta` {acc!r} desconhecida ({', '.join(accounts) or 'nenhuma'})")
        if host and cwd:
            if any(h == host and same_dir(c, cwd) for h, c in seen):
                errs.append(f"agente {i}: {host}:{cwd} repetido no conjunto")
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
            errs.append("`lider` precisa ser um objeto (ou ficar de fora)")
        else:
            for k in lider:
                if k not in LEADER_KEYS:
                    errs.append(f"lider: campo desconhecido: {k}")
            receita = _text(lider.get("receita")) or DEFAULT_RECIPE
            if receita not in recipes:
                errs.append(f"lider: `receita` {receita!r} desconhecida ({', '.join(recipes) or 'nenhuma'})")
            out["lider"] = {
                "receita": receita,
                "prompt": _text(lider.get("prompt")),
                "tarefa": _text(lider.get("tarefa")),
            }
    if errs:
        raise ValueError("; ".join(errs))
    return out


def same_dir(a: str, b: str) -> bool:
    """`~/Projects/x` e `/home/user/Projects/x` são a mesma pasta; o servidor não conhece o HOME remoto,
    então compara o caminho relativo à pasta pessoal quando dá, e o caminho inteiro quando não."""

    def rel(p: str) -> Tuple[bool, str]:
        p = p.strip().rstrip("/")
        if p.startswith("~/"):
            return True, p[2:]
        m = re.match(r"^/(?:home|Users)/[^/]+/(.*)$", p)
        return (True, m.group(1)) if m else (False, p)

    ra, rb = rel(a), rel(b)
    return ra == rb


# ------------------------------------------------------------------ catálogo em disco
def _path(directory: Optional[Path], sid: str) -> Path:
    return (directory or sets_dir()) / f"{sid}.json"


def catalog(directory: Optional[Path] = None) -> Dict[str, dict]:
    """Conjuntos salvos, por id. Arquivo quebrado é pulado, não derruba a página."""
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
        raise KeyError(f"conjunto desconhecido: {sid}")
    try:
        data = json.loads(_path(directory, sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError(f"conjunto desconhecido: {sid}") from None
    if not isinstance(data, dict):
        raise KeyError(f"conjunto desconhecido: {sid}")
    return data


def save(data: dict, directory: Optional[Path] = None) -> str:
    """Grava o conjunto (já validado) e devolve o id."""
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
    """O JSON como vai para o arquivo: sem campos vazios, na ordem do formato."""
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


# ------------------------------------------------------------------ abrir: conflitos e plano
def live_in(agents: Iterable[dict], host: str, cwd: str) -> List[str]:
    """Rótulos dos agentes vivos (da página ou de terminal) nessa máquina e pasta."""
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
    """Agentes já abertos que ocupam alguma máquina:pasta do conjunto (a página recusa abrir)."""
    agents = list(agents)
    out: List[str] = []
    for a in data.get("agentes") or []:
        out += [lab for lab in live_in(agents, a["maquina"], a["pasta"]) if lab not in out]
    return out


def plan(
    data: dict, taken: Iterable[str], usage: Dict[str, dict], accounts: List[str], running: Iterable[str]
) -> List[dict]:
    """Um item por agente, na ordem do JSON: host, cwd, mode, label (único entre os vivos), acc (fixa
    ou escolhida pela folga, para o conjunto inteiro de uma vez), why, prompt, tarefa."""
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
            it["acc"], it["why"] = a["conta"], "fixa no conjunto"
        items.append(it)
    assign(items, usage, accounts, running)
    return items


# ------------------------------------------------------------------ resumo e fotografia
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
    """Receita cujos níveis (do topo para baixo) batem com os que estão de pé; com vinculados, a `multi`."""
    match = [p for p in cat.values() if [n["nome"] for n in p.niveis] == level_names]
    for p in match:
        if p.multi == has_links:
            return p.id
    return match[0].id if match else DEFAULT_RECIPE


def snapshot(
    agents: List[dict], links: List[dict], cat: Optional[Dict[str, Any]] = None
) -> Tuple[dict, List[str]]:
    """Um conjunto a partir do que está aberto: um agente por aba (modo resume, conta atual, prompt se
    subiu com um), e, se alguma aba tem níveis acima, ela vira a primeira e o `lider` ganha a receita.
    Devolve (conjunto, avisos)."""
    cat = cat if cat is not None else pipelines.catalog()
    live = [a for a in agents if a.get("state") != "closed" and a.get("kind") == "sdk" and a.get("label")]
    bases = [a for a in live if not a.get("parent")]
    avisos: List[str] = []
    with_levels = [b for b in bases if pipelines.levels_above(live, b["label"])]
    first = with_levels[0] if with_levels else None
    if len(with_levels) > 1:
        avisos.append(
            "só o primeiro professor entra como líder; os de "
            + ", ".join(b["label"] for b in with_levels[1:])
            + " ficam de fora"
        )
    ordered = ([first] if first else []) + [b for b in bases if b is not first]
    perms = {b.get("permission") for b in ordered if b.get("permission")}
    out: Dict[str, Any] = {"nome": "", "descricao": "", "agentes": []}
    if len(perms) == 1:
        out["permissao"] = perms.pop()
    elif len(perms) > 1:
        avisos.append("os agentes abertos têm modos de permissão diferentes; o conjunto fica sem `permissao`")
    for b in ordered:
        item = {"maquina": b.get("host") or "?", "pasta": b.get("dir") or "", "modo": "resume"}
        if b.get("account"):
            item["conta"] = b["account"]
        if b.get("role"):
            item["prompt"] = b["role"]
        out["agentes"].append(item)
    if first:
        above = pipelines.levels_above(live, first["label"])
        names = [next((a.get("level") for a in live if a.get("label") == lab), "") for lab in reversed(above)]
        has_links = any(
            first["label"] in (lk.get("local"), lk.get("remote")) and lk.get("up", True) for lk in links
        )
        out["lider"] = {"receita": infer_recipe([n for n in names if n], has_links, cat)}
    return out, avisos
