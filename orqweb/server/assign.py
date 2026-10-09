"""Escolha da conta de cada projeto, espalhando a carga pelas contas com mais folga no 7d.

Regras (as mesmas do `orq` do terminal): conta com 5h ou 7d em 95% está sem cota e nunca é
escolhida; cada agente já rodando numa conta pesa 20 pontos contra ela; ficar na conta da
conversa vale 10 pontos, para não copiar conversas à toa.
"""

import os
from collections import Counter
from typing import Dict, Iterable, List, Optional, Set

LIMIT = 95
PER_AGENT = 20
STAY = 10


def exhausted(u: Optional[dict]) -> bool:
    return (not u) or ("err" in u) or u.get("5h", 0) >= LIMIT or u.get("7d", 0) >= LIMIT


def slack(u: dict) -> float:
    """Pontos abaixo (negativo) ou acima (positivo) do ritmo no 7d."""
    return u.get("7d", 0) - u.get("7d_pace", 0)


def assign(
    projs: List[dict], usage: Dict[str, dict], accounts: List[str], running: Iterable[str] = ()
) -> List[dict]:
    """Preenche p["acc"] e p["why"] em cada projeto. p["account"] é a conta da conversa, se houver."""
    load = Counter(running)
    healthy = [n for n in accounts if n in usage and not exhausted(usage[n])]
    for p in projs:
        cur = p.get("account")
        if p.get("acc"):
            load[p["acc"]] += 1
            continue
        if not healthy:
            p["acc"], p["why"] = cur or (accounts[0] if accounts else "?"), "nenhuma conta com folga"
        else:
            best = min(
                healthy,
                key=lambda n, cur=cur: slack(usage[n]) + PER_AGENT * load[n] - (STAY if n == cur else 0),
            )
            p["acc"] = best
            if best == cur:
                p["why"] = "a da conversa"
            elif not cur:
                p["why"] = f"mais folga ({slack(usage[best]):+.0f} do ritmo)"
            elif cur not in healthy:
                p["why"] = f"{cur} sem cota"
            elif load[cur]:
                p["why"] = (
                    f"{cur} já tem {load[cur]} agente(s); {best} está {slack(usage[best]):+.0f} do ritmo"
                )
            else:
                a, b = slack(usage[best]), slack(usage[cur])
                p["why"] = f"{best} tem mais folga ({a:+.0f} contra {b:+.0f} da {cur})"
        load[p["acc"]] += 1
    return projs


def label_for(cwd: str, host: str, taken: Set[str]) -> str:
    """`pasta@maquina`, com sufixo -2, -3… se o nome já estiver em uso."""
    base = os.path.basename(cwd.rstrip("/")) or "home"
    lab, n = f"{base}@{host}", 1
    while lab in taken:
        n += 1
        lab = f"{base}-{n}@{host}"
    return lab
