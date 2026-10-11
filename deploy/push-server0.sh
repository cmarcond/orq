#!/bin/sh
# Do laptop: manda o código para o server0 e roda o deploy lá. Fase 2 troca isto pelo pull do GHCR.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
HOST=${1:-server0}
TB=$(mktemp -t orq-web-src.XXXXXX).tgz
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$TB" -C "$HERE" --exclude .git --exclude __pycache__ --exclude .pytest_cache orqweb deploy Dockerfile .dockerignore pyproject.toml README.md
scp -q "$TB" "$HOST:/tmp/orq-web-src.tgz"
ssh "$HOST" 'set -e; mkdir -p ~/orq-web && tar xzf /tmp/orq-web-src.tgz -C ~/orq-web && rm /tmp/orq-web-src.tgz && cd ~/orq-web && sh deploy/server0/deploy.sh'
rm -f "$TB"
