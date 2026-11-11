#!/bin/sh
# From the laptop: sends the code to server0 and runs the deploy there (which pulls the image from GHCR if logged in,
# otherwise builds it).
# ORQ_BUILD_LOCAL=1 ./deploy/push-server0.sh  builds there with the code sent (not yet published on main).
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
HOST=${1:-server0}
TB=$(mktemp -t orq-web-src.XXXXXX).tgz
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$TB" -C "$HERE" --exclude .git --exclude __pycache__ --exclude .pytest_cache orqweb deploy Dockerfile .dockerignore pyproject.toml README.md
# the tarball goes through ssh's stdin (a single connection; scp/sftp sometimes drop on unstable networks)
ssh -o ConnectTimeout=15 "$HOST" "set -e; mkdir -p ~/orq-web && tar xzf - -C ~/orq-web && cd ~/orq-web && ORQ_BUILD_LOCAL=${ORQ_BUILD_LOCAL:-} sh deploy/server0/deploy.sh" < "$TB"
rm -f "$TB"
