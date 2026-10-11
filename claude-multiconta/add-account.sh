#!/bin/sh
# Adiciona (ou remove) uma conta do Claude Code e regera todos os aliases.
#   ./add-account.sh <nome> <email> [rótulo]      ex.: ./add-account.sh conta3 fulano@x.com "Fulano (x.com)"
#   ./add-account.sh --remove <nome>              tira do accounts.json (o diretório ~/.claude-<nome> fica)
# Cria: aliases claude-<nome>, claude-<nome>-login, claude-usage-<nome>; o usage-all inclui a conta automaticamente.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
CONF="$HERE/accounts.json"

if [ "${1:-}" = "--remove" ]; then
  [ $# -eq 2 ] || { echo "uso: $0 --remove <nome>"; exit 1; }
  python3 - "$CONF" "$2" <<'PY'
import json, sys
p, name = sys.argv[1], sys.argv[2]
c = json.load(open(p))
before = len(c["accounts"])
c["accounts"] = [a for a in c["accounts"] if a["name"] != name]
if len(c["accounts"]) == before: sys.exit(f"conta '{name}' não existe em {p}")
json.dump(c, open(p, "w"), indent=2, ensure_ascii=False); open(p, "a").write("\n")
print(f"conta '{name}' removida de accounts.json")
PY
  sh "$HERE/install.sh"
  exit 0
fi

[ $# -ge 2 ] || { sed -n '2,5p' "$0"; exit 1; }
NAME=$1; EMAIL=$2; LABEL=${3:-"$NAME ($EMAIL)"}
case "$NAME" in *[!a-z0-9_-]*|"") echo "nome deve ser só [a-z0-9_-]: '$NAME'"; exit 1;; esac
DIR="~/.claude-$NAME"

python3 - "$CONF" "$NAME" "$EMAIL" "$LABEL" "$DIR" <<'PY'
import json, sys
p, name, email, label, d = sys.argv[1:6]
c = json.load(open(p))
if any(a["name"] == name for a in c["accounts"]): sys.exit(f"conta '{name}' já existe em {p}")
if any(a.get("config_dir") == d for a in c["accounts"]): sys.exit(f"config_dir {d} já usado")
c["accounts"].append({"name": name, "email": email, "label": label, "config_dir": d})
json.dump(c, open(p, "w"), indent=2, ensure_ascii=False); open(p, "a").write("\n")
print(f"conta '{name}' adicionada: {label} -> {d}")
PY
mkdir -p "$HOME/.claude-$NAME"
sh "$HERE/install.sh"
echo
echo "próximos passos (num terminal novo, ou: . ~/.config/claude-multiconta/aliases.sh):"
echo "  claude-$NAME-login     # abre link, cola código (logado como $EMAIL)"
echo "                         # (onboarding já marcado como feito pelo install.sh; não precisa de outro login)"
echo "  claude-$NAME           # usar"
echo "  claude-usage-$NAME     # uso desta conta; usage-all já inclui ela"
