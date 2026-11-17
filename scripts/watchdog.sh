#!/usr/bin/env bash
# Hourly watchdog: alert if the topic has no report newer than 26 hours.
# Override the report root with INTEL_REPORTS_DIR if you changed
# paths.reports_dir in config/settings.yaml.
set -u
TOPIC="${1:?usage: watchdog.sh <topic>}"
REPORTS="${INTEL_REPORTS_DIR:-$HOME/reports/intel}/$TOPIC"
ALERT="$(cd "$(dirname "$0")" && pwd)/alert.sh"

LATEST="$(ls -t "$REPORTS"/*.md 2>/dev/null | head -1 || true)"
if [ -z "$LATEST" ]; then
  "$ALERT" "intel-agent watchdog: $TOPIC has no report yet (looked in $REPORTS)." || true
  exit 0
fi
AGE=$(( $(date +%s) - $(stat -c %Y "$LATEST") ))
if [ "$AGE" -gt $((26 * 3600)) ]; then
  "$ALERT" "intel-agent watchdog: $TOPIC has no report newer than 26h (latest: $LATEST)." || true
fi
