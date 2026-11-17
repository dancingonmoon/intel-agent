#!/usr/bin/env python3
"""Open X once and let the user log in.

Two modes:
- `browser.mode: cdp` (recommended): attach to a normally-started Chrome on
  port 9222. `navigator.webdriver` is false, so x.com renders its login page.
- `browser.mode: launch`: let agent-browser launch Chrome. X may refuse to
  render because webdriver is true; use only as a fallback.

The script never asks for or stores a password.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from intel.config import load_settings
from intel.sources import agent_browser

PROFILE = Path.home() / ".local" / "state" / "intel-agent" / "x-profile"
LOGIN_URL = "https://x.com/i/flow/login"


def _probe() -> dict:
    result = agent_browser.eval_js(
        PROFILE,
        "JSON.stringify({"
        "href:location.href,"
        "account:!!document.querySelector('[data-testid=\"SideNav_AccountSwitcher_Button\"]'),"
        "home:!!document.querySelector('[data-testid=\"AppTabBar_Home_Link\"], a[href=\"/home\"], [aria-label=\"Home\"], [aria-label=\"首页\"]'),"
        "login:!!document.querySelector('[data-testid=\"loginButton\"]'),"
        "webdriver:navigator.webdriver,"
        "text:(document.body?document.body.innerText:'').slice(0,300)"
        "})",
    )
    return result.value if result.ok and isinstance(result.value, dict) else {}


def _start_cdp_if_needed() -> bool:
    if agent_browser.cdp_reachable():
        return True
    script = ROOT / "scripts" / "start_chrome_cdp.sh"
    print(f"CDP port {agent_browser.cdp_port()} is not open; starting Chrome...")
    subprocess.run([str(script)], check=False)
    return agent_browser.cdp_reachable()


def main() -> int:
    settings = load_settings()
    agent_browser.configure(settings)
    if not agent_browser.available():
        print("agent-browser is not installed or not on PATH.", file=sys.stderr)
        return 2

    if agent_browser.cdp_mode():
        if not _start_cdp_if_needed():
            print("Chrome CDP is not reachable; run scripts/start_chrome_cdp.sh", file=sys.stderr)
            return 1
        opened = agent_browser.navigate(PROFILE, LOGIN_URL)
    else:
        agent_browser.close_all(PROFILE)
        opened = agent_browser.navigate(PROFILE, LOGIN_URL, headed=True)

    if not opened.ok:
        print(f"failed to open X: {opened.error}", file=sys.stderr)
        return 1

    print("A Chrome window is open. Log in to X manually.")
    input("Press Enter here once you can see your X home timeline... ")

    state: dict = {}
    for _ in range(20):
        state = _probe()
        if state.get("account") or state.get("home"):
            break
        time.sleep(1.5)
    if agent_browser.cdp_mode():
        if state.get("webdriver"):
            print("warning: navigator.webdriver is true; X may refuse to render.", file=sys.stderr)
        # Do not close the user's normal Chrome.
    else:
        agent_browser.close(PROFILE)

    if state.get("account") or state.get("home"):
        agent_browser.mark_login(PROFILE)
        print(f"X login OK. Profile: {agent_browser.chrome_path() or PROFILE}")
        return 0

    print(f"X login not confirmed (href={state.get('href', '?')}).", file=sys.stderr)
    if state.get("text"):
        print(f"Page text: {state['text']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
