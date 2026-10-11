"""orq-web: página local com cota, abas e chat com os agentes do orq.

Uso: orq-web [--port N]      (padrão 8765; só 127.0.0.1, com token no link)
"""

import argparse
import sys

from .app import serve


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="orq-web", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    return serve(a.port)


if __name__ == "__main__":
    sys.exit(main())
