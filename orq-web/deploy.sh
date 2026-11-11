#!/bin/sh
# Installs orq-web on the machines: ./deploy.sh [machine ...]  (no arguments: laptop + orq_hosts from accounts.json)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
CONF=${CLAUDE_MULTICONTA_CONF:-$HOME/.config/claude-multiconta/accounts.json}
if [ $# -gt 0 ]; then HOSTS="$*"; else HOSTS="laptop $(python3 -c "import json; print(' '.join(json.load(open('$CONF')).get('orq_hosts', [])))")"; fi
TB=$(mktemp -t orq-web.XXXXXX).tar.gz
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$TB" -C "$HERE" --exclude __pycache__ orqweb install.sh
# "laptop" is the Mac (the page's ORQ_LOCAL_HOST); when run from another machine, it is reached over ssh like the
# others, and the current machine is the one that installs locally
ME=$(hostname -s)
[ "$(uname -s)" = Darwin ] && ME=laptop
for h in $HOSTS; do
  if [ "$h" = "$ME" ]; then sh "$HERE/install.sh" | sed "s/^/$h: /"; continue; fi
  ( scp -q -o BatchMode=yes -o ConnectTimeout=12 "$TB" "$h:/tmp/orq-web.tgz" \
    && ssh -o BatchMode=yes -o ConnectTimeout=12 "$h" 'd=$(mktemp -d) && tar xzf /tmp/orq-web.tgz -C "$d" && sh "$d/install.sh" && rm -rf "$d" /tmp/orq-web.tgz' ) 2>&1 | sed "s/^/$h: /" || echo "$h: ✗ failed"
done
rm -f "$TB"
