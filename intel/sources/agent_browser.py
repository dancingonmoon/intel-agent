"""Thin wrapper around the `agent-browser` CLI (Chrome/Edge via CDP).

We use agent-browser instead of the Playwright Python package on this machine:
Playwright's Chromium download repeatedly timed out through the available
mirrors, while agent-browser is already installed and can reuse a persistent
browser profile.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class AgentBrowserError(RuntimeError):
    pass


LOGIN_MARKER = ".intel-login-ok"


def login_confirmed(profile: Path) -> bool:
    return (profile / LOGIN_MARKER).exists()


def mark_login(profile: Path) -> None:
    profile.mkdir(parents=True, exist_ok=True)
    (profile / LOGIN_MARKER).write_text("login confirmed\n", encoding="utf-8")


def clear_login(profile: Path) -> None:
    (profile / LOGIN_MARKER).unlink(missing_ok=True)


@dataclass(slots=True)
class BrowserResult:
    ok: bool
    value: object = None
    error: str = ""


def available() -> bool:
    return shutil.which("agent-browser") is not None


PROXY_BYPASS = "localhost,127.0.0.1,::1"
_DEFAULT_PROXY = "socks5://127.0.0.1:10808"
_CDP_PORT = 0
_CHROME_CACHE: str | None = None
_UA_CACHE: str | None = None


def chrome_path() -> str | None:
    """Prefer real Chrome; Edge's headless UA triggers X's unsupported page."""
    global _CHROME_CACHE
    if _CHROME_CACHE is None:
        for candidate in ("/usr/bin/google-chrome", "/usr/bin/microsoft-edge"):
            if Path(candidate).exists():
                _CHROME_CACHE = candidate
                break
        else:
            _CHROME_CACHE = ""
    return _CHROME_CACHE or None


def user_agent() -> str:
    """A normal Chrome UA; HeadlessChrome/Edg is rejected by x.com."""
    global _UA_CACHE
    if _UA_CACHE:
        return _UA_CACHE
    major = "157"
    path = chrome_path()
    if path:
        try:
            out = subprocess.check_output([path, "--version"], text=True, timeout=5)
            match = re.search(r"(\d+)\.", out)
            if match:
                major = match.group(1)
        except (OSError, subprocess.SubprocessError):
            pass
    _UA_CACHE = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )
    return _UA_CACHE


def configure(settings: dict) -> None:
    """Use the configured proxy and browser mode.

    `browser.mode: cdp` attaches to a normally-started Chrome on
    `browser.cdp_port`. That browser has `navigator.webdriver = false`, which
    x.com requires; agent-browser's own launch sets it to true and X refuses
    to render the app.
    """
    global _DEFAULT_PROXY, _CDP_PORT
    url = str(settings.get("proxy", {}).get("url", "")).strip()
    if url.startswith("socks5h://"):
        url = "socks5://" + url[len("socks5h://") :]
    if url:
        _DEFAULT_PROXY = url
    browser = settings.get("browser", {})
    if str(browser.get("mode", "launch")).lower() == "cdp":
        _CDP_PORT = int(browser.get("cdp_port", 9222))
    else:
        _CDP_PORT = 0


def cdp_mode() -> bool:
    return _CDP_PORT > 0


def cdp_port() -> int:
    return _CDP_PORT


def cdp_reachable(port: int | None = None) -> bool:
    import socket

    port = int(port or _CDP_PORT or 9222)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def proxy_url() -> str:
    url = os.environ.get("AGENT_BROWSER_PROXY") or _DEFAULT_PROXY
    if url.startswith("socks5h://"):
        url = "socks5://" + url[len("socks5h://") :]
    return url


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["AGENT_BROWSER_PROXY"] = proxy_url()
    env.setdefault("NO_PROXY", PROXY_BYPASS)
    env.setdefault("no_proxy", PROXY_BYPASS)
    if cdp_mode():
        env["AGENT_BROWSER_CDP"] = str(cdp_port())
    return env


def run(profile: Path, args: list[str], timeout: int = 90) -> BrowserResult:
    if not available():
        return BrowserResult(False, error="agent-browser not found on PATH")
    profile.parent.mkdir(parents=True, exist_ok=True)
    if cdp_mode():
        # Attach to the user's normally-started Chrome (webdriver=false).
        cmd = ["agent-browser", "--json", *args]
    else:
        cmd = [
            "agent-browser",
            "--proxy",
            proxy_url(),
            "--proxy-bypass",
            PROXY_BYPASS,
        ]
        chrome = chrome_path()
        if chrome:
            cmd.extend(["--executable-path", chrome])
        cmd.extend(["--user-agent", user_agent()])
        cmd.extend(["--profile", str(profile), "--json", *args])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return BrowserResult(False, error=f"{exc.__class__.__name__}: {exc}")
    stdout = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return BrowserResult(False, error=(proc.stderr or stdout or f"exit {proc.returncode}").strip()[:1000])
    if not stdout:
        return BrowserResult(True, value=None)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return BrowserResult(True, value=stdout)
    if isinstance(payload, dict) and payload.get("success") is False:
        return BrowserResult(False, error=str(payload.get("error") or "agent-browser error"))
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and "result" in data:
        return BrowserResult(True, value=data["result"])
    return BrowserResult(True, value=data if data is not None else payload)


def _transient(error: str) -> bool:
    return "outcome_unknown" in error or "EOF while parsing" in error


def open_url(profile: Path, url: str, *, headed: bool = False, timeout: int = 90) -> BrowserResult:
    args = ["open", url]
    if headed:
        args.append("--headed")
    last: BrowserResult | None = None
    for _ in range(2):
        last = run(profile, args, timeout=timeout)
        if last.ok or not _transient(last.error):
            return last
        time.sleep(1.5)
    return last or BrowserResult(False, error="open failed")


def navigate(profile: Path, url: str, *, headed: bool = False, timeout: int = 30) -> BrowserResult:
    """Open about:blank first, then set location.

    SPA pages (Yandex, X) often never fire a complete load event, which makes
    `open <url>` time out. Navigating from about:blank avoids that wait. The
    daemon occasionally answers `open` with an EOF race; in that case the
    browser is usually alive, so still try the navigation.
    """
    opened = open_url(profile, "about:blank", headed=headed, timeout=timeout)
    if not opened.ok and not _transient(opened.error):
        return opened
    script = f"location.href = {json.dumps(url)}; 'navigating'"
    result = eval_js(profile, script, timeout=timeout)
    if result.ok:
        return result
    return opened if not opened.ok else result


def eval_js(profile: Path, script: str, timeout: int = 60) -> BrowserResult:
    result = run(profile, ["eval", script], timeout=timeout)
    if not result.ok:
        return result
    value = result.value
    if isinstance(value, str):
        try:
            return BrowserResult(True, value=json.loads(value))
        except json.JSONDecodeError:
            return BrowserResult(True, value=value)
    return result


def current_url(profile: Path) -> str:
    result = eval_js(profile, "location.href")
    return str(result.value) if result.ok and result.value else ""


def screenshot(profile: Path, path: Path) -> BrowserResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    return run(profile, ["screenshot", str(path)], timeout=60)


def get_html(profile: Path) -> str:
    result = run(profile, ["get", "html"], timeout=60)
    return str(result.value) if result.ok and result.value else ""


def close(profile: Path) -> None:
    if cdp_mode():
        return
    run(profile, ["close"], timeout=30)


def close_all(profile: Path) -> None:
    """Close every agent-browser session (and the daemon).

    No-op in CDP mode: the attached Chrome is the user's browser and must stay
    open. Needed in launch mode after changing proxy settings, because a daemon
    started without the SOCKS proxy would otherwise be reused.
    """
    if cdp_mode():
        return
    run(profile, ["close", "--all"], timeout=30)
