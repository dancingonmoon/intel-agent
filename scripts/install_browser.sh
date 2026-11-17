#!/usr/bin/env bash
# Check the browser backend used for X/Yandex login-state search.
# This project uses the already-installed agent-browser CLI; no Playwright
# Chromium download is required.
set -euo pipefail

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "agent-browser is not installed." >&2
  echo "Install it first, or switch to the optional Playwright backend." >&2
  exit 1
fi

agent-browser doctor

echo
echo "Browser backend ready. Next:"
echo "  ./scripts/start_chrome_cdp.sh      # normal Chrome on 9222 (webdriver=false)"
echo "  .venv/bin/python scripts/x_login.py"
echo "  .venv/bin/python scripts/yandex_login.py"
