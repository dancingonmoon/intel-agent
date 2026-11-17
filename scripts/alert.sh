#!/usr/bin/env bash
# Send one alert line through a user-supplied command, or fall back to the
# desktop/log. Set INTEL_ALERT_CMD in ~/.config/intel-agent/env, e.g.
#   INTEL_ALERT_CMD=/home/me/bin/send-alert
# The command receives the message as its single argument.
set -u
MSG="${1:-intel-agent alert}"

if [ -n "${INTEL_ALERT_CMD:-}" ]; then
  # shellcheck disable=SC2086
  if $INTEL_ALERT_CMD "$MSG"; then
    exit 0
  fi
  echo "alert command failed: $INTEL_ALERT_CMD" >&2
fi

if command -v notify-send >/dev/null 2>&1; then
  notify-send "intel-agent" "$MSG" 2>/dev/null && exit 0
fi

logger -t intel-agent "$MSG" 2>/dev/null || true
echo "intel-agent: $MSG" >&2
