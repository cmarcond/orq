"""converte-web: converts an interactive `claude` running in tmux, on any machine, into a page agent.

Usage: converte-web                      lists the interactive claudes in tmux on all machines
       converte-web NAME@MACHINE [...]   converts one or more (what the page does via the "convert" button)
       converte-web --all                converts all the listed ones
"""

import argparse
import sys

from . import config
from .agent.convert import terminal_sessions
from .server.external import External


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="converte-web", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("labels", nargs="*")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    conf = config.load()
    ext = External()
    agents, errs = ext.all_agents(conf.hosts)
    sessions = terminal_sessions(agents)
    for h, e in errs.items():
        print(f"  {h}: {e}", file=sys.stderr)
    if not a.labels and not a.all:
        if not sessions:
            print("no interactive claude in tmux on the machines.")
            return 0
        print(f"  {'name':<28} {'state':<10} {'account':<10} folder")
        for s in sessions:
            print(
                f"  {s['label']:<28} {s.get('state', '?'):<10} {s.get('account', '?'):<10} {s.get('dir', '')}"
            )
        print("\nconverte-web NAME@MACHINE   (or --all)")
        return 0
    targets = [s["label"] for s in sessions] if a.all else a.labels
    rc = 0
    for label in targets:
        host = label.rsplit("@", 1)[1] if "@" in label else "laptop"
        res = ext.convert(host, label)
        mark = "✓" if res.get("ok") else "✗"
        print(f"{mark} {label}: {res.get('msg')}" + (f" (closed by {res['how']})" if res.get("how") else ""))
        rc |= 0 if res.get("ok") else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
