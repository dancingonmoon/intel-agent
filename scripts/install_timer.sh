#!/usr/bin/env bash
# Install and enable the user timer for one topic.
#
#   ./scripts/install_timer.sh ru-tetra
#
# Copies the portable unit templates, substitutes this checkout's absolute
# paths, and enables the daily and watchdog timers.
set -euo pipefail
TOPIC="${1:-ru-tetra}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${HOME}"
UNIT_DIR="$HOME/.config/systemd/user"
PYTHON="$ROOT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "error: $PYTHON not found; create the venv and run 'pip install -e .' first" >&2
  exit 1
fi
if [ ! -f "$ROOT/config/topics/$TOPIC.yaml" ]; then
  echo "error: unknown topic '$TOPIC' (no config/topics/$TOPIC.yaml)" >&2
  exit 1
fi

mkdir -p "$UNIT_DIR" "$HOME_DIR/.config/intel-agent" "$HOME_DIR/.local/state/intel-agent"

install_unit() {
  local src="$1"
  local dst="$UNIT_DIR/$(basename "$src")"
  sed -e "s|__ROOT__|$ROOT|g" -e "s|__HOME__|$HOME_DIR|g" "$src" > "$dst"
}

for unit in "$ROOT"/systemd/*.service "$ROOT"/systemd/*.timer; do
  install_unit "$unit"
done
chmod +x "$ROOT/scripts/alert.sh" "$ROOT/scripts/watchdog.sh" "$ROOT/scripts/start_chrome_cdp.sh"

systemctl --user daemon-reload
systemctl --user enable --now "intel-agent@${TOPIC}.timer"
systemctl --user enable --now "intel-agent-watchdog@${TOPIC}.timer"
systemctl --user list-timers "intel-agent*"

cat <<EOF

Installed for topic: $TOPIC
  repo:    $ROOT
  reports: $HOME_DIR/reports/intel/$TOPIC
  env:     $HOME_DIR/.config/intel-agent/env

Manual test:
  systemctl --user start intel-agent@$TOPIC.service
  journalctl --user -u intel-agent@$TOPIC.service -f

To keep the timer running while you are logged out:
  loginctl enable-linger "$USER"
EOF
