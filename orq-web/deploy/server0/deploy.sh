#!/bin/sh
# Sobe (ou atualiza) o orq-web em container nesta máquina, publicado só no IP do ZeroTier.
# Gera a senha (se não houver), o ssh_config do container e garante ssh para a própria máquina.
# Uso: deploy/server0/deploy.sh            (na raiz do clone do orq-web, na máquina de destino)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
CFG="$HOME/.config/orq-web"
CONF="${CLAUDE_MULTICONTA_CONF:-$HOME/.config/claude-multiconta/accounts.json}"
HOSTNAME_=$(hostname -s)
ZT_IP=$(ip -4 -o addr show 2>/dev/null | awk '/ zt/ {split($4,a,"/"); print a[1]; exit}')
[ -n "$ZT_IP" ] || { echo "não achei interface ZeroTier (zt*)"; exit 1; }
mkdir -p "$CFG/state" "$CFG/known_hosts.d"

# senha: uma linha, só para o dono. Leia com: cat ~/.config/orq-web/password
if [ ! -s "$CFG/password" ]; then
  umask 077; openssl rand -base64 18 > "$CFG/password"; umask 022
  echo "senha nova gravada em $CFG/password"
fi
chmod 600 "$CFG/password"

# ssh para a própria máquina (dentro do container, "$HOSTNAME_" é remoto)
KEY="$HOME/.ssh/id_ed25519"
[ -f "$KEY" ] || { echo "sem $KEY; gere com ssh-keygen -t ed25519"; exit 1; }
touch "$HOME/.ssh/authorized_keys"; chmod 600 "$HOME/.ssh/authorized_keys"
grep -qF "$(cut -d' ' -f2 "$KEY.pub")" "$HOME/.ssh/authorized_keys" || cat "$KEY.pub" >> "$HOME/.ssh/authorized_keys"

# ssh_config do container: um Host por máquina, com IP resolvido aqui (o container não tem o /etc/hosts
# desta máquina nem o ~/.ssh/config dela). known_hosts próprio, gravável, com accept-new.
hosts=$(python3 -c "import json;print(' '.join(json.load(open('$CONF')).get('orq_hosts',[])))")
{
  echo "# gerado por deploy.sh em $(date -Is); não edite, rode o deploy de novo"
  seen=" "
  for h in $hosts laptop "$HOSTNAME_"; do
    case "$seen" in *" $h "*) continue;; esac; seen="$seen$h "
    ip=$(ssh -G "$h" 2>/dev/null | awk '/^hostname /{print $2}')
    case "$ip" in ""|"$h") ip=$(getent hosts "$h" 2>/dev/null | awk '{print $1; exit}');; esac
    [ "$h" = "$HOSTNAME_" ] && ip="$ZT_IP"
    [ -n "$ip" ] || { echo "aviso: não resolvi $h; pulando" >&2; continue; }
    printf 'Host %s\n    HostName %s\n    User %s\n    IdentityFile %s\n    IdentitiesOnly yes\n    StrictHostKeyChecking accept-new\n    UserKnownHostsFile %s/known_hosts\n    ConnectTimeout 8\n    ServerAliveInterval 15\n\n' \
      "$h" "$ip" "$(id -un)" "$KEY" "$CFG"
  done
} > "$CFG/ssh_config"
chmod 600 "$CFG/ssh_config"
echo "ssh_config do container: $(grep -c '^Host ' "$CFG/ssh_config") máquinas"

cd "$HERE"
export ORQ_ZT_IP="$ZT_IP" ORQ_HOSTNAME="$HOSTNAME_" UID="$(id -u)" GID="$(id -g)" USER="$(id -un)" HOME
# imagem da CI (GHCR) se houver login (docker login ghcr.io com PAT read:packages); senão constrói aqui.
# ORQ_BUILD_LOCAL=1 força construir com o código enviado (antes de ele chegar à main).
if [ -z "${ORQ_BUILD_LOCAL:-}" ] && docker compose pull --quiet 2>/dev/null; then docker compose up -d; else echo "construindo localmente"; docker compose up -d --build; fi
sleep 3
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
echo
echo "orq-web em http://$ZT_IP:8765/   senha: cat $CFG/password"
