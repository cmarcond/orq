#!/bin/sh
# Do laptop: manda o código para o server0 e roda o deploy lá (que puxa a imagem do GHCR se logado, senão constrói).
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
HOST=${1:-server0}
TB=$(mktemp -t orq-web-src.XXXXXX).tgz
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$TB" -C "$HERE" --exclude .git --exclude __pycache__ --exclude .pytest_cache orqweb deploy Dockerfile .dockerignore pyproject.toml README.md
# o tarball vai pelo stdin do ssh (uma conexão só; scp/sftp às vezes caem em redes instáveis)
ssh -o ConnectTimeout=15 "$HOST" 'set -e; mkdir -p ~/orq-web && tar xzf - -C ~/orq-web && cd ~/orq-web && sh deploy/server0/deploy.sh' < "$TB"
rm -f "$TB"
