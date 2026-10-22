#!/bin/sh
# Instala o esquema de múltiplas contas do Claude Code + uso (claude-multiconta).
#   ./install.sh            instala/atualiza (instala claude e codex se não existirem)
#   ./install.sh --no-install  idem, mas sem baixar claude/codex
#   ./install.sh --uninstall remove os arquivos e a linha de source
# Idempotente: pode rodar de novo depois de editar accounts.json.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BIN="$HOME/.local/bin"
CONF_DIR="$HOME/.config/claude-multiconta"
ALIASES="$CONF_DIR/aliases.sh"
MARK="# >>> claude-multiconta >>>"
SOURCE_LINE="[ -r \"$ALIASES\" ] && . \"$ALIASES\"   $MARK"

rc_files() {
  # zsh e/ou bash, o que existir; cria .bashrc se não houver nenhum
  found=0
  for f in "$HOME/.zshrc" "$HOME/.bashrc"; do [ -f "$f" ] && { echo "$f"; found=1; }; done
  [ $found = 0 ] && { touch "$HOME/.bashrc"; echo "$HOME/.bashrc"; }
}

# --- claude e codex: instala se faltarem -------------------------------------
have() { command -v "$1" >/dev/null 2>&1 || [ -x "$BIN/$1" ]; }

ensure_claude() {
  have claude && { echo "claude: ok ($(command -v claude || echo "$BIN/claude"))"; return; }
  echo "claude: não encontrado, instalando (script oficial em claude.ai/install.sh)..."
  command -v curl >/dev/null || { echo "precisa de curl para instalar o claude"; return 1; }
  curl -fsSL https://claude.ai/install.sh | bash || { echo "falha ao instalar o claude"; return 1; }
  have claude && echo "claude: instalado em $BIN/claude"
}

ensure_codex() {
  # codex instalado por npm/nvm só aparece no PATH de shells interativas; procura também lá
  for c in "$HOME"/.nvm/versions/node/*/bin/codex "$HOME"/.npm-global/bin/codex; do
    [ -x "$c" ] && { echo "codex: ok ($c, via npm; para atualizar: npm install -g @openai/codex)"; return; }
  done
  have codex && { echo "codex: ok ($(command -v codex || echo "$BIN/codex"))"; return; }
  if command -v npm >/dev/null 2>&1; then
    echo "codex: não encontrado, instalando via npm..."
    npm install -g @openai/codex && return
    echo "npm falhou, tentando o binário do GitHub..."
  fi
  echo "codex: não encontrado, baixando o binário oficial do GitHub (~100 MB)..."
  command -v curl >/dev/null || { echo "precisa de curl (ou npm) para instalar o codex"; return 1; }
  os=$(uname -s); arch=$(uname -m)
  case "$arch" in x86_64|amd64) arch=x86_64;; aarch64|arm64) arch=aarch64;; *) echo "arquitetura $arch sem binário"; return 1;; esac
  case "$os" in Linux) target="$arch-unknown-linux-musl";; Darwin) target="$arch-apple-darwin";; *) echo "SO $os sem binário"; return 1;; esac
  tmp=$(mktemp -d)
  url="https://github.com/openai/codex/releases/latest/download/codex-$target.tar.gz"
  curl -fL --progress-bar -o "$tmp/codex.tgz" "$url" || { echo "download falhou: $url"; rm -rf "$tmp"; return 1; }
  tar xzf "$tmp/codex.tgz" -C "$tmp" && mv "$tmp/codex-$target" "$BIN/codex" && chmod +x "$BIN/codex"
  rm -rf "$tmp"
  [ -x "$BIN/codex" ] && echo "codex: instalado em $BIN/codex ($("$BIN/codex" --version 2>/dev/null))"
}

if [ "${1:-}" = "--uninstall" ]; then
  for f in $(rc_files); do
    grep -v "$MARK" "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  done
  rm -f "$BIN/codex-usage-fast" "$BIN/usage-all" "$BIN/claude-last" "$BIN/orq" "$BIN/orq-run" "$BIN/orq-hook" "$BIN/claude-login-url" "$BIN/claude-whoami" "$BIN/usage-sessoes" "$BIN/usage-hist"
  rm -rf "$CONF_DIR"
  echo "removido. As contas em ~/.claude* e ~/.codex ficaram intactas."
  exit 0
fi

command -v python3 >/dev/null || { echo "precisa de python3 (>= 3.9 de preferência): apt install python3"; exit 1; }
mkdir -p "$BIN" "$CONF_DIR"
if [ "${1:-}" != "--no-install" ]; then
  ensure_claude || echo "aviso: claude não instalado; instale à mão e rode ./install.sh de novo"
  ensure_codex  || echo "aviso: codex não instalado; instale à mão e rode ./install.sh de novo"
fi
cp "$HERE"/bin/codex-usage-fast "$HERE"/bin/usage-all "$HERE"/bin/claude-last "$HERE"/bin/orq "$HERE"/bin/orq-run "$HERE"/bin/orq-hook "$HERE"/bin/claude-login-url "$HERE"/bin/claude-whoami "$HERE"/bin/usage-sessoes "$HERE"/bin/usage-hist "$BIN/"
chmod +x "$BIN"/codex-usage-fast "$BIN"/usage-all "$BIN"/claude-last "$BIN"/orq "$BIN"/orq-run "$BIN"/orq-hook "$BIN"/claude-login-url "$BIN"/claude-whoami "$BIN"/usage-sessoes "$BIN"/usage-hist
cp "$HERE/accounts.json" "$CONF_DIR/accounts.json"

# gera aliases.sh a partir de accounts.json (funciona em bash e zsh)
python3 - "$CONF_DIR/accounts.json" "$ALIASES" <<'PY'
import json, sys
conf = json.load(open(sys.argv[1]))
accounts = conf["accounts"]
names = [a["name"] for a in accounts]
default = conf.get("default_account")
use_tmux = conf.get("tmux", True)
if default and default not in names:
    sys.exit(f"default_account '{default}' não está em accounts")

out = ["# gerado pelo install.sh do claude-multiconta a partir de accounts.json — não edite à mão",
       'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH";; esac',
       ""]
if use_tmux:
    out += [
"# claude_run <prefixo> <config_dir ou vazio=padrão> [args do claude]",
"# Sessão interativa abre num tmux NOVO nomeado <prefixo>-<pasta> (-2, -3... se repetir);",
"# attach é manual: tmux ls / tmux attach -t <nome>. Roda DIRETO (sem tmux) quando:",
"# já está dentro do tmux, não há tmux, ou é subcomando rápido (auth, --version, -p, mcp...).",
"claude_run() {",
"    local prefix=$1 dir=$2 direct= bin base name n=1; shift 2",
"    case \"${1-}\" in auth|mcp|config|doctor|update|install|plugin|agents|-p|--print|-v|--version|-h|--help) direct=1;; esac",
'    [ -n "$TMUX" ] && direct=1',
"    command -v tmux >/dev/null 2>&1 || direct=1",
'    bin=$(claude_bin) || { echo "claude não encontrado no PATH" >&2; return 127; }',
'    if [ -n "$dir" ]; then set -- env CLAUDE_CONFIG_DIR="$dir" "$bin" "$@"; else set -- env -u CLAUDE_CONFIG_DIR "$bin" "$@"; fi',
'    if [ -n "$direct" ]; then "$@"; return; fi',
'    base="$prefix-${PWD##*/}"; base=${base//[^a-zA-Z0-9_-]/-}; name=$base',
'    while tmux has-session -t "=$name" 2>/dev/null; do name="$base-$((++n))"; done',
'    # o servidor tmux herda a pasta de quem o cria; nascendo aqui, apagar esta pasta quebra todas as janelas',
'    local here=$PWD',
'    (cd ~ && tmux new-session -s "$name" -c "$here" "$(printf \'%q \' "$@")")',
"}",
"# caminho do binário claude, ignorando a função `claude` desta shell",
'claude_bin() { if [ -n "$BASH_VERSION" ]; then type -P claude; else whence -p claude; fi; }',
""]
for a in accounts:
    n, d, em = a["name"], a["config_dir"], a.get("email", "")
    if d.rstrip("/") in ("~/.claude", "$HOME/.claude"):
        env = "env -u CLAUDE_CONFIG_DIR"
    else:
        env = 'env CLAUDE_CONFIG_DIR="' + d.replace("~", "$HOME", 1) + '"'
    email = f" --email {em}" if em else ""
    prefix = "claude" if n == default else f"claude-{n}"
    out.append(f"# {a.get('label', n)}" + (f"  [sessão tmux {prefix}-<pasta>]" if use_tmux else ""))
    if use_tmux:
        dirarg = '""' if env.startswith("env -u") else '"' + d.replace("~", "$HOME", 1) + '"'
        out.append(f'claude-{n}() {{ claude_run {prefix} {dirarg} "$@"; }}')
    else:
        out.append(f'claude-{n}() {{ {env} claude "$@"; }}')
    out += [f"alias claude-{n}-login='{env} BROWSER=\"$HOME/.local/bin/claude-login-url\" claude auth login{email}'",
            f"alias claude-usage-{n}='usage-all {n}'", ""]
if default:
    out += [f"# `claude` puro = conta {default}  (confira com: claude-whoami)", f'claude() {{ claude-{default} "$@"; }}', ""]

if conf.get("codex", True):
    out += ["# Codex (OpenAI)",
            "alias codex-login='codex login --device-auth'",
            "alias codex-usage='codex-usage-fast'", ""]
out += ["# Uso de todas as contas de uma vez: é o script ~/.local/bin/usage-all",
        "#   usage-all [-v|--verbose] [conta ...]   (ex.: usage-all work; -v = completo)",
        "unalias usage-all 2>/dev/null", "",
        "# Retomar a conversa mais recente desta pasta na conta certa (lista: claude-last)",
        "#   claude-resume [--as CONTA] [-a] [N] [args do claude]   N = número da linha do claude-last",
        "#   --as CONTA: retoma usando outra conta (copia a conversa para ela; a original fica)",
        "claude-resume() {",
        "  local all= as= n=1 out acc sid",
        "  while :; do",
        '    case "$1" in',
        '      -a|--all) all=-a; shift;;',
        '      --as) as=$2; shift 2;;',
        '      --as=*) as=${1#--as=}; shift;;',
        '      *) break;;',
        "    esac",
        "  done",
        '  case "$1" in ""|*[!0-9]*) ;; *) n=$1; shift;; esac',
        '  out=$(claude-last --pick $all ${as:+--as=$as} "$n") || return 1',
        '  read -r acc sid <<< "$out"',
        '  echo "abrindo: claude-$acc --resume $sid"',
        '  "claude-$acc" --resume "$sid" "$@"',
        "}", ""]
if conf.get("tip_on_login", True):
    out += ["case $- in *i*) echo \"💡 usage-all: uso do Claude\";; esac", ""]
open(sys.argv[2], "w").write("\n".join(out))
# hooks que o orq-run passa ao claude com --settings (só valem nos painéis do orq)
import os
hook = os.path.expanduser("~/.local/bin/orq-hook")
h = [{"hooks": [{"type": "command", "command": hook}]}]
json.dump({"hooks": {e: h for e in ("SessionStart", "UserPromptSubmit", "Stop", "Notification", "StopFailure", "SessionEnd")}},
          open(os.path.join(os.path.dirname(sys.argv[2]), "orq-hooks.json"), "w"), indent=1)
print("contas:", ", ".join(names), "| `claude` puro =", default or "(sem padrão)")
PY

# marca o onboarding como feito no .claude.json de cada conta: `claude auth login` grava a
# credencial mas não essa marca, e aí o primeiro `claude-<conta>` abre o assistente de primeira
# execução, que termina num login novo pelo navegador (callback em localhost) e quebra com
# "Redirect URI ... not supported". Idempotente; não mexe em quem já tem a marca.
CLAUDE_EXE=$(command -v claude || echo "$BIN/claude")
python3 - "$CONF_DIR/accounts.json" "$CLAUDE_EXE" <<'PY'
import json, os, pathlib, subprocess, sys
conf = json.load(open(sys.argv[1])); accounts = conf["accounts"]
home = pathlib.Path.home()

def write_json(f, data):
    # .tmp + replace: o Claude Code reescreve esses arquivos; nunca deixar pela metade
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2)); os.chmod(str(tmp), 0o600); os.replace(str(tmp), str(f))

# Renderizador do terminal: "default" escreve a conversa no scrollback nativo (tmux, iTerm,
# PgUp e roda do mouse enxergam tudo); "fullscreen" guarda num buffer próprio e o histórico
# some depois de uma tela. É o mesmo que digitar /tui default dentro do Claude Code, mas
# vale para todas as contas. "tui": false no accounts.json deixa cada conta como está.
tui = conf.get("tui", "default")
for a in accounts:
    if tui is False: break
    d = pathlib.Path(os.path.expanduser(a["config_dir"].replace("$HOME", "~", 1)))
    d.mkdir(parents=True, exist_ok=True)
    f = d / "settings.json"
    try:
        st = json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        print(f"aviso: {f} não é JSON válido; não mexi"); continue
    if st.get("tui") == tui: continue
    st["tui"] = tui
    write_json(f, st)
    print(f"tui={tui} em {f}")

try:
    ver = subprocess.run([sys.argv[2], "--version"], stdout=subprocess.PIPE,
                         universal_newlines=True, timeout=30).stdout.split()[0]
except Exception:
    ver = None
for a in accounts:
    d = pathlib.Path(os.path.expanduser(a["config_dir"].replace("$HOME", "~", 1)))
    d.mkdir(parents=True, exist_ok=True)
    # conta em ~/.claude guarda o estado em ~/.claude.json; as outras, dentro do config_dir
    f = home / ".claude.json" if d.resolve() == (home / ".claude").resolve() else d / ".claude.json"
    try:
        cfg = json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        print(f"aviso: {f} não é JSON válido; não mexi"); continue
    if cfg.get("hasCompletedOnboarding"):
        continue
    cfg["hasCompletedOnboarding"] = True
    if ver: cfg.setdefault("lastOnboardingVersion", ver)
    cfg.setdefault("theme", "dark")
    write_json(f, cfg)
    print(f"onboarding marcado como feito em {f}")
PY

# linha de source nos rc: no .zshrc com Powerlevel10k vai ANTES do bloco de instant prompt
# (o aliases.sh imprime a dica, e p10k reclama de saída depois desse bloco)
for f in $(rc_files); do
  grep -qF "$MARK" "$f" && continue
  if grep -q "p10k-instant-prompt" "$f"; then
    { echo "$SOURCE_LINE"; cat "$f"; } > "$f.tmp" && mv "$f.tmp" "$f"
  else
    printf '\n%s\n' "$SOURCE_LINE" >> "$f"
  fi
  echo "source adicionado em $f"
done

for f in $(rc_files); do
  if grep -v "$MARK" "$f" | grep -qE '^[[:space:]]*(function[[:space:]]+)?claude[[:space:]]*\(\)'; then
    echo "aviso: $f ainda define uma função claude() própria; ela vai brigar com a do claude-multiconta. Remova o bloco antigo."
  fi
done
echo
echo "instalado. Abra um terminal novo (ou: . $ALIASES) e então:"
for n in $(python3 -c "import json;print(' '.join(a['name'] for a in json.load(open('$CONF_DIR/accounts.json'))['accounts']))"); do
  echo "  claude-$n-login    # login da conta $n (abre link, cola código)"
done
echo "  codex-login          # login do Codex por device auth"
echo "  usage-all            # uso do Claude, resumido: uma linha por conta, mini-barra com ritmo e tempo até o reset"
echo "  usage-all -v         # completo, com barra e marcador de ritmo"
echo "  usage-all gmail      # só a conta escolhida (também aceita -v); codex-usage para o Codex"
echo "  orq                  # abre até 4 projetos (qualquer máquina) numa janela com 4 painéis"
echo "  claude-last          # qual conta teve a conversa mais recente nesta pasta"
echo "  claude-resume        # retoma essa conversa na conta certa (claude-resume 2 = a segunda)"
echo "  claude-resume --as gmail   # retoma a mesma conversa usando outra conta"
