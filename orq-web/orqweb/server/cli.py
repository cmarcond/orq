"""orq-web: local page with quota, tabs and chat with the orq agents.

Usage: orq-web [--port N] [--no-open]   (default 8765; 127.0.0.1 only, with token in the link)

Restarting on the same port keeps the token: the tab that is already open stays valid.
"""

import argparse
import os
import sys

from .app import serve


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="orq-web", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser (= ORQ_NO_OPEN=1)")
    a = ap.parse_args(argv)
    if a.no_open:
        os.environ["ORQ_NO_OPEN"] = "1"
    return serve(a.port)


if __name__ == "__main__":
    sys.exit(main())
