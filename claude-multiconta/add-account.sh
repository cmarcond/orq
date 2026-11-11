#!/bin/sh
# Adds (or removes) a Claude Code account and regenerates all the aliases.
#   ./add-account.sh <name> <email> [label]       e.g.: ./add-account.sh account3 someone@x.com "Someone (x.com)"
#   ./add-account.sh --remove <name>              removes it from accounts.json (the ~/.claude-<name> directory stays)
# Creates: aliases claude-<name>, claude-<name>-login, claude-usage-<name>; usage-all includes the account automatically.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
CONF="$HERE/accounts.json"

if [ "${1:-}" = "--remove" ]; then
  [ $# -eq 2 ] || { echo "usage: $0 --remove <name>"; exit 1; }
  python3 - "$CONF" "$2" <<'PY'
import json, sys
p, name = sys.argv[1], sys.argv[2]
c = json.load(open(p))
before = len(c["accounts"])
c["accounts"] = [a for a in c["accounts"] if a["name"] != name]
if len(c["accounts"]) == before: sys.exit(f"account '{name}' does not exist in {p}")
json.dump(c, open(p, "w"), indent=2, ensure_ascii=False); open(p, "a").write("\n")
print(f"account '{name}' removed from accounts.json")
PY
  sh "$HERE/install.sh"
  exit 0
fi

[ $# -ge 2 ] || { sed -n '2,5p' "$0"; exit 1; }
NAME=$1; EMAIL=$2; LABEL=${3:-"$NAME ($EMAIL)"}
case "$NAME" in *[!a-z0-9_-]*|"") echo "name must contain only [a-z0-9_-]: '$NAME'"; exit 1;; esac
DIR="~/.claude-$NAME"

python3 - "$CONF" "$NAME" "$EMAIL" "$LABEL" "$DIR" <<'PY'
import json, sys
p, name, email, label, d = sys.argv[1:6]
c = json.load(open(p))
if any(a["name"] == name for a in c["accounts"]): sys.exit(f"account '{name}' already exists in {p}")
if any(a.get("config_dir") == d for a in c["accounts"]): sys.exit(f"config_dir {d} already in use")
c["accounts"].append({"name": name, "email": email, "label": label, "config_dir": d})
json.dump(c, open(p, "w"), indent=2, ensure_ascii=False); open(p, "a").write("\n")
print(f"account '{name}' added: {label} -> {d}")
PY
mkdir -p "$HOME/.claude-$NAME"
sh "$HERE/install.sh"
echo
echo "next steps (in a new terminal, or: . ~/.config/claude-multiconta/aliases.sh):"
echo "  claude-$NAME-login     # open the link, paste the code (logged in as $EMAIL)"
echo "                         # (onboarding already marked as done by install.sh; no other login needed)"
echo "  claude-$NAME           # use it"
echo "  claude-usage-$NAME     # usage of this account; usage-all already includes it"
