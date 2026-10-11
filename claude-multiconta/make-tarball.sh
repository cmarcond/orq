#!/bin/sh
# Gera claude-multiconta-YYYYMMDD.tar.gz em ~/Projects para enviar a outra máquina.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HOME/Projects/claude-multiconta-$(date +%Y%m%d).tar.gz"
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$OUT" -C "$(dirname "$HERE")" --exclude .git --exclude __pycache__ --exclude '*.tar.gz' "$(basename "$HERE")"
echo "$OUT"
echo "envie e instale:"
echo "  scp $OUT user@maquina:"
echo "  ssh user@maquina 'tar xzf $(basename "$OUT") && ./claude-multiconta/install.sh'"
