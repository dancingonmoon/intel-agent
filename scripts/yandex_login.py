#!/usr/bin/env python3
"""Open Yandex once and let the user log in.

Uses the same CDP-connected normal Chrome as the X login when
`browser.mode: cdp`. Yandex search is captcha-prone on shared exit IPs; a
logged-in profile may help, but it is not guaranteed.
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

PROFILE = Path.home() / ".local" / "state" / "intel-agent" / "yandex-profile"
LOGIN_URL = "https://passport.yandex.ru/auth"
SEARCH_URL = "https://yandex.ru/news/search?text=TETRA%20%D1%80%D0%B0%D0%B4%D0%B8%D0%BE%D1%81%D0%B2%D1%8F%D0%B7%D1%8C"


def _probe() -> dict:
    result = agent_browser.eval_js(
        PROFILE,
        "JSON.stringify({"
        "href:location.href,"
        "account:!!document.querySelector('[data-testid=\"username\"],.header-user-name,.user-account__name,a[href*=\"passport.yandex.ru/profile\"]'),"
        "captcha:!!document.querySelector('.CheckboxCaptcha, #captcha-container, form[action*=\"showcaptcha\"]'),"
        "stories:document.querySelectorAll('a[href*=\"/news/story/\"]').length,"
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
        print(f"failed to open Yandex: {opened.error}", file=sys.stderr)
        return 1

    print("A Chrome window is open. Log in to Yandex manually.")
    input("Press Enter here once you are logged in (or to continue without login)... ")

    # Go to a search page. If Yandex shows SmartCaptcha, the user must solve it
    # manually in the browser; this script does not automate captcha bypass.
    agent_browser.navigate(PROFILE, SEARCH_URL)
    state: dict = {}
    for attempt in range(3):
        time.sleep(3)
        state = _probe()
        if state.get("captcha"):
            print("Yandex shows SmartCaptcha. Click 'I am not a robot' in the Chrome window.")
            input("Press Enter after the captcha is gone... ")
            continue
        if state.get("account") or state.get("stories"):
            break
        print(f"No account marker/news stories yet (href={state.get('href', '?')}).")
        if state.get("text"):
            print(f"Page text: {state['text']}")
        input("Press Enter to retry... ")

    if not agent_browser.cdp_mode():
        agent_browser.close(PROFILE)

    if state.get("account") or state.get("stories"):
        agent_browser.mark_login(PROFILE)
        print(f"Yandex login/search OK. Profile: {agent_browser.chrome_path() or PROFILE}")
        return 0

    print(f"Yandex login not confirmed (href={state.get('href', '?')}).", file=sys.stderr)
    if state.get("text"):
        print(f"Page text: {state['text']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
