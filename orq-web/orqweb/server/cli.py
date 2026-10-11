"""orq-web: página local com cota, abas e chat com os agentes do orq.

Uso: orq-web [--port N] [--no-open]   (padrão 8765; só 127.0.0.1, com token no link)

Reiniciar na mesma porta mantém o token: a aba já aberta continua valendo.
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
    ap.add_argument("--no-open", action="store_true", help="não abre o navegador (= ORQ_NO_OPEN=1)")
    a = ap.parse_args(argv)
    if a.no_open:
        os.environ["ORQ_NO_OPEN"] = "1"
    return serve(a.port)


if __name__ == "__main__":
    sys.exit(main())
