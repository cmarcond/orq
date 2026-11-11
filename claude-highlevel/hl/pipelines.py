"""The pipelines, read from `pipelines/*.toml`.

A pipeline is data, not code: adding a level — professor → arquiteto → executor —
means writing one more `[[niveis]]`, without touching Python.

TOML instead of YAML (a deviation from the plan): `tomllib` has been in the standard library
since 3.11, and YAML would require an external dependency that the whole project does not have.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .session import LevelSpec

PIPELINE_DIR = Path(__file__).resolve().parent.parent / "pipelines"
PADRAO = "professor-executor"


@dataclass(frozen=True)
class Pipeline:
    id: str
    titulo: str
    descricao: str
    niveis: tuple[dict[str, Any], ...]
    bootstrap: str

    def nomes(self, tag: str = "") -> list[str]:
        """The real names of this run's sessions, with the suffix that avoids collisions
        between simultaneous runs."""
        return [f"{nivel['nome']}{tag}" for nivel in self.niveis]

    def montar(self, model: str | None = None, mode: str = "auto",
               tag: str = "") -> list[LevelSpec]:
        """The levels ready to start, with {acima} and {abaixo} already resolved.

        They all inherit the same permission mode by default — different classes make
        Claude Code hold the messages between the levels, and the pipeline stalls before it
        starts. The TOML can override it per level, for those who know what they are doing.
        """
        nomes = self.nomes(tag)
        especificacoes = []
        for i, nivel in enumerate(self.niveis):
            vizinhos = {
                "acima": nomes[i - 1] if i > 0 else "",
                "abaixo": nomes[i + 1] if i + 1 < len(nomes) else "",
            }
            especificacoes.append(LevelSpec(
                name=nomes[i],
                system_prompt=nivel["papel"].format(**vizinhos).strip(),
                permission_mode=nivel.get("modo", mode),
                model=nivel.get("modelo", model),
                color=str(nivel.get("cor", "37")),
            ))
        return especificacoes

    def texto_bootstrap(self, tarefa: str, tag: str = "") -> str:
        nomes = self.nomes(tag)
        return self.bootstrap.format(
            tarefa=tarefa,
            abaixo=nomes[1] if len(nomes) > 1 else nomes[0],
        ).strip()

    def resumo(self) -> dict[str, Any]:
        return {
            "id": self.id, "titulo": self.titulo, "descricao": self.descricao,
            "niveis": [n["nome"] for n in self.niveis],
        }


def _ler(caminho: Path) -> Pipeline:
    dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
    niveis = dados.get("niveis") or []
    if not niveis:
        raise ValueError(f"{caminho.name}: no level defined")
    for nivel in niveis:
        if "nome" not in nivel or "papel" not in nivel:
            raise ValueError(f"{caminho.name}: every level needs `nome` and `papel`")
    return Pipeline(
        id=caminho.stem,
        titulo=dados.get("titulo", caminho.stem),
        descricao=dados.get("descricao", ""),
        niveis=tuple(niveis),
        bootstrap=dados.get("bootstrap", "{tarefa}"),
    )


@lru_cache(maxsize=1)
def catalogo(diretorio: Path = PIPELINE_DIR) -> dict[str, Pipeline]:
    receitas = sorted(
        p for p in Path(diretorio).glob("*.toml") if not p.name.startswith(".")
    )
    return {p.stem: _ler(p) for p in receitas}


def pegar(nome: str | None) -> Pipeline:
    disponiveis = catalogo()
    escolhido = nome or PADRAO
    if escolhido not in disponiveis:
        raise KeyError(f"unknown pipeline: {escolhido}")
    return disponiveis[escolhido]


def mode_mismatch(levels: list[LevelSpec]) -> set[str]:
    """Distinct modes across the levels — the cause of the held message."""
    return {spec.permission_mode for spec in levels}
