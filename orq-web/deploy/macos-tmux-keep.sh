#!/bin/sh
# Mac that receives agents over ssh: keeps a tmux server alive in the graphical session from login on.
# Processes created in that server inherit the unlocked Keychain, which plain ssh sessions do not have.
# Installs a user LaunchAgent (no sudo). Idempotent. To remove: --remove
set -eu
LABEL=io.orq-tmux-keep
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMUX=$(command -v tmux || echo /opt/homebrew/bin/tmux)
UID_=$(id -u)
if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "removed"; exit 0
fi
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/sh</string><string>-c</string>
    <string>while :; do $TMUX has-session -t keep 2>/dev/null || $TMUX new-session -d -s keep; sleep 60; done</string>
  </array>
  <key>WorkingDirectory</key><string>$HOME</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
</dict></plist>
PL
launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_" "$PLIST"
sleep 2
$TMUX has-session -t keep 2>/dev/null && echo "LaunchAgent $LABEL active; tmux session 'keep' alive" || { echo "LaunchAgent loaded, but the keep session did not appear"; exit 1; }
