"""Receitas de níveis (`orqweb/pipelines/*.toml`): quem sobe acima de um agente da página e com que papel.

Uma receita é dado, não código: cada `[[niveis]]` é um nível a mais, do topo para baixo. O agente da
aba é sempre a base (o executor) e não ganha papel novo; os níveis sobem na mesma máquina, pasta e
conta, e conversam pelo SendMessage/ListAgents nativo do Claude Code. `{acima}`/`{abaixo}`/`{eu}` no papel
viram os nomes reais das sessões (os que o SendMessage aceita, sem `@`).

O TOML é lido pelo `tomllib` (3.11+); em Python mais antigo um leitor mínimo cobre o subconjunto que
as receitas usam (strings simples e de três aspas, `[[tabelas]]`, comentários).
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import host_of, session_name

PIPELINE_DIR = Path(__file__).resolve().parent.parent / "pipelines"
DEFAULT = "professor-executor"
SEP = "~"  # `kaggle~professor@server0`: nível `professor` sobre `kaggle@server0`


@dataclass(frozen=True)
class Pipeline:
    id: str
    titulo: str
    descricao: str
    niveis: List[Dict[str, Any]]  # do topo para baixo: nome, papel, cor

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "titulo": self.titulo,
            "descricao": self.descricao,
            "niveis": [n["nome"] for n in self.niveis],
            "cores": {n["nome"]: n.get("cor", "") for n in self.niveis},
        }


# ------------------------------------------------------------------ leitura
def parse_toml(text: str) -> Dict[str, Any]:
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        return _parse_minimal(text)
    return tomllib.loads(text)


def _unquote(raw: str) -> str:
    """String de uma linha, básica ou literal; o que vem depois do fecho (comentário) é ignorado."""
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
                return json.loads(raw[: i + 1])  # escapes do TOML básico coincidem com os do JSON
            i += 1
        raise ValueError(f"string sem fecho: {raw!r}")
    if raw.startswith("'"):
        end = raw.find("'", 1)
        if end < 0:
            raise ValueError(f"string sem fecho: {raw!r}")
        return raw[1:end]
    raise ValueError(f"valor não suportado: {raw!r}")


def _parse_minimal(text: str) -> Dict[str, Any]:
    """Subconjunto do TOML: `k = "v"`, `k = '''v'''`/`k = \"\"\"v\"\"\"`, `[[tabela]]`, comentários."""
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
            raise ValueError(f"linha inválida: {line!r}")
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
        raise ValueError(f"{path.name}: nenhum nível definido")
    for n in niveis:
        if "nome" not in n or "papel" not in n:
            raise ValueError(f"{path.name}: todo nível precisa de `nome` e `papel`")
        if "@" in n["nome"] or SEP in n["nome"] or "/" in n["nome"]:
            raise ValueError(f"{path.name}: nome de nível inválido: {n['nome']!r}")
    return Pipeline(
        id=path.stem,
        titulo=str(data.get("titulo") or path.stem),
        descricao=str(data.get("descricao") or ""),
        niveis=[dict(n) for n in niveis],
    )


def catalog(directory: Optional[Path] = None) -> Dict[str, Pipeline]:
    """Receitas disponíveis, por id (nome do arquivo). Arquivo quebrado é pulado, não derruba a página."""
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
        raise KeyError(f"receita desconhecida: {key}")
    return cat[key]


# ------------------------------------------------------------------ montagem
def level_label(base_label: str, nome: str) -> str:
    """`kaggle@server0` + `professor` -> `kaggle~professor@server0`."""
    base = base_label.rsplit("@", 1)[0] if "@" in base_label else base_label
    return f"{base}{SEP}{nome}@{host_of(base_label)}"


def base_of_label(label: str) -> Optional[str]:
    """Rótulo do agente da aba a partir do rótulo de um nível; None se não é nível."""
    if SEP not in label:
        return None
    base, _, rest = label.partition(SEP)
    host = rest.rsplit("@", 1)[1] if "@" in rest else ""
    return f"{base}@{host}" if host else base


def plan_levels(p: Pipeline, base_label: str) -> List[Dict[str, Any]]:
    """Os níveis a subir sobre `base_label`, de baixo para cima (o de cima do último é o topo).

    Cada item: label, level (nome), parent (rótulo logo abaixo), role (papel com os vizinhos
    resolvidos) e color. O `{abaixo}` do nível mais baixo é o próprio agente da aba.
    """
    labels = [level_label(base_label, n["nome"]) for n in p.niveis]  # topo -> baixo
    out = []
    for i, n in enumerate(p.niveis):
        acima = labels[i - 1] if i > 0 else ""
        abaixo = labels[i + 1] if i + 1 < len(labels) else base_label
        role = (
            str(n["papel"])
            .replace("{acima}", session_name(acima) if acima else "")
            .replace("{abaixo}", session_name(abaixo))
            .replace("{eu}", session_name(labels[i]))
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
    """Rótulos dos níveis vivos sobre `label`, de baixo para cima, seguindo `parent`."""
    out: List[str] = []
    cur = label
    while True:
        nxt = next((a for a in agents if a.get("parent") == cur and a.get("state") != "closed"), None)
        if nxt is None or nxt["label"] in out or nxt["label"] == label:
            break
        out.append(nxt["label"])
        cur = nxt["label"]
    return out
