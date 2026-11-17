#!/usr/bin/env bash
# Start a NORMAL Chrome with remote debugging, using a dedicated profile.
# agent-browser attaches via AGENT_BROWSER_CDP=<port>. Chrome is started without
# automation flags, so navigator.webdriver is false, which x.com requires.
#
# Optional environment:
#   CHROME_BIN, CHROME_PROFILE, CDP_PORT
#   CHROME_PROXY=socks5://127.0.0.1:10808   (omit for a direct connection)
set -euo pipefail

CHROME="${CHROME_BIN:-/usr/bin/google-chrome}"
PROFILE="${CHROME_PROFILE:-$HOME/.local/state/intel-agent/chrome-profile}"
PORT="${CDP_PORT:-9222}"
PROXY="${CHROME_PROXY:-}"
LOG="${INTEL_CHROME_LOG:-$HOME/.cache/intel-agent/chrome-cdp.log}"

mkdir -p "$PROFILE" "$(dirname "$LOG")"

if curl -s --max-time 1 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  echo "CDP already listening on ${PORT}"
  exit 0
fi

PROXY_ARGS=()
if [ -n "$PROXY" ]; then
  PROXY_ARGS=(--proxy-server="$PROXY" --proxy-bypass-list="localhost;127.0.0.1;::1")
fi

nohup "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  "${PROXY_ARGS[@]}" \
  --no-first-run \
  --no-default-browser-check \
  about:blank >"$LOG" 2>&1 &

for _ in $(seq 1 30); do
  sleep 0.5
  if curl -s --max-time 1 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    echo "CDP ready on ${PORT} (profile: ${PROFILE})"
    exit 0
  fi
done

echo "Chrome CDP failed to start; see $LOG" >&2
exit 1
