#!/bin/sh
# Builds claude-multiconta-YYYYMMDD.tar.gz in ~/Projects to send to another machine.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HOME/Projects/claude-multiconta-$(date +%Y%m%d).tar.gz"
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$OUT" -C "$(dirname "$HERE")" --exclude .git --exclude __pycache__ --exclude '*.tar.gz' "$(basename "$HERE")"
echo "$OUT"
echo "send and install:"
echo "  scp $OUT user@machine:"
echo "  ssh user@machine 'tar xzf $(basename "$OUT") && ./claude-multiconta/install.sh'"
