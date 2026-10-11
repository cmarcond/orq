#!/bin/sh
# Instala o orq-web nesta máquina sem pip: copia o pacote para ~/.local/lib/orq-web e cria os
# comandos orq-agent e orq-web em ~/.local/bin. Só precisa de python3 (3.8+). Idempotente.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
LIB="$HOME/.local/lib/orq-web"
BIN="$HOME/.local/bin"
command -v python3 >/dev/null || { echo "precisa de python3 (3.8 ou mais)"; exit 1; }
mkdir -p "$LIB" "$BIN"
rm -rf "$LIB/orqweb"
cp -R "$HERE/orqweb" "$LIB/orqweb"
find "$LIB/orqweb" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
python3 -m compileall -q "$LIB/orqweb" >/dev/null 2>&1 || true
for cmd in orq-agent:orqweb.agent.cli orq-web:orqweb.server.cli converte-web:orqweb.convert; do
  name=${cmd%%:*}; mod=${cmd#*:}
  cat > "$BIN/$name" <<SH
#!/bin/sh
# gerado pelo install.sh do orq-web; o código está em $LIB
PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m $mod "\$@"
SH
  chmod +x "$BIN/$name"
done
ver=$(python3 -c "import sys; sys.path.insert(0, '$LIB'); import orqweb; print(orqweb.__version__)")
echo "orq-web $ver instalado: $BIN/orq-agent, $BIN/orq-web e $BIN/converte-web (código em $LIB)"
