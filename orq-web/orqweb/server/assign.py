"""Choice of each project's account, spreading the load over the accounts with the most slack on the 7d.

Rules (the same as the terminal `orq`): an account with 5h or 7d at 95% is out of quota and is never
chosen; each agent already running on an account weighs 20 points against it; staying on the
conversation's account is worth 10 points, so as not to copy conversations for nothing.
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
    """Points below (negative) or above (positive) the pace on the 7d."""
    return u.get("7d", 0) - u.get("7d_pace", 0)


def assign(
    projs: List[dict], usage: Dict[str, dict], accounts: List[str], running: Iterable[str] = ()
) -> List[dict]:
    """Fills in p["acc"] and p["why"] in each project. p["account"] is the conversation's account, if any."""
    load = Counter(running)
    healthy = [n for n in accounts if n in usage and not exhausted(usage[n])]
    for p in projs:
        cur = p.get("account")
        if p.get("acc"):
            load[p["acc"]] += 1
            continue
        if not healthy:
            p["acc"], p["why"] = cur or (accounts[0] if accounts else "?"), "no account with slack"
        else:
            best = min(
                healthy,
                key=lambda n, cur=cur: slack(usage[n]) + PER_AGENT * load[n] - (STAY if n == cur else 0),
            )
            p["acc"] = best
            if best == cur:
                p["why"] = "the conversation's"
            elif not cur:
                p["why"] = f"most slack ({slack(usage[best]):+.0f} from pace)"
            elif cur not in healthy:
                p["why"] = f"{cur} out of quota"
            elif load[cur]:
                p["why"] = (
                    f"{cur} already has {load[cur]} agent(s); {best} is {slack(usage[best]):+.0f} from pace"
                )
            else:
                a, b = slack(usage[best]), slack(usage[cur])
                p["why"] = f"{best} has more slack ({a:+.0f} against {b:+.0f} for {cur})"
        load[p["acc"]] += 1
    return projs


def label_for(cwd: str, host: str, taken: Set[str]) -> str:
    """`folder@machine`, with a -2, -3… suffix if the name is already in use."""
    base = os.path.basename(cwd.rstrip("/")) or "home"
    lab, n = f"{base}@{host}", 1
    while lab in taken:
        n += 1
        lab = f"{base}-{n}@{host}"
    return lab
