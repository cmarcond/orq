#!/bin/sh
# Instala o orq-web nas máquinas: ./deploy.sh [maquina ...]  (sem argumentos: laptop + orq_hosts do accounts.json)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
CONF=${CLAUDE_MULTICONTA_CONF:-$HOME/.config/claude-multiconta/accounts.json}
if [ $# -gt 0 ]; then HOSTS="$*"; else HOSTS="laptop $(python3 -c "import json; print(' '.join(json.load(open('$CONF')).get('orq_hosts', [])))")"; fi
TB=$(mktemp -t orq-web.XXXXXX).tar.gz
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$TB" -C "$HERE" --exclude __pycache__ orqweb install.sh
for h in $HOSTS; do
  if [ "$h" = laptop ]; then sh "$HERE/install.sh" | sed 's/^/laptop: /'; continue; fi
  ( scp -q -o BatchMode=yes -o ConnectTimeout=12 "$TB" "$h:/tmp/orq-web.tgz" \
    && ssh -o BatchMode=yes -o ConnectTimeout=12 "$h" 'd=$(mktemp -d) && tar xzf /tmp/orq-web.tgz -C "$d" && sh "$d/install.sh" && rm -rf "$d" /tmp/orq-web.tgz' ) 2>&1 | sed "s/^/$h: /" || echo "$h: ✗ falhou"
done
rm -f "$TB"
