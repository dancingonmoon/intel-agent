"""Environment and integration health checks."""

from __future__ import annotations

import shutil
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .config import load_settings


def _tcp(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _playwright_chromium() -> str | None:
    cache = Path.home() / ".cache" / "ms-playwright"
    if not cache.exists():
        return None
    patterns = ("chromium-*/chrome-linux/chrome", "chromium-*/chrome-linux64/chrome")
    for pattern in patterns:
        for path in cache.glob(pattern):
            if path.exists():
                return str(path)
    return None


def run_checks(settings: dict | None = None) -> list[dict]:
    settings = settings or load_settings()
    home = Path.home()
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "", optional: bool = False) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "optional": optional})

    add("python", sys.version_info >= (3, 12), sys.version.split()[0])
    try:
        import yaml  # noqa: F401

        add("pyyaml", True, "import ok")
    except Exception as exc:  # pragma: no cover - environment specific
        add("pyyaml", False, str(exc))

    proxy = str(settings.get("proxy", {}).get("url", "") or "")
    if not proxy:
        add("proxy", True, "disabled (direct connection)")
    else:
        parsed = urlsplit(proxy)
        try:
            port = parsed.port or (1080 if parsed.scheme.startswith("socks") else 8080)
        except ValueError:
            port = 0
        add("proxy", bool(parsed.hostname) and _tcp(str(parsed.hostname), port), proxy)
    llm = settings.get("llm", {})
    host, port = llm.get("preflight_host", "127.0.0.1"), int(llm.get("preflight_port", 15721))
    add("cc-switch", _tcp(str(host), port), f"{host}:{port}")

    add("agent-browser", shutil.which("agent-browser") is not None, "used for X/Yandex login-state search")
    browser = "/usr/bin/google-chrome" if Path("/usr/bin/google-chrome").exists() else "/usr/bin/microsoft-edge"
    add("chrome-or-edge", Path(browser).exists(), browser)
    browser_cfg = settings.get("browser", {})
    if str(browser_cfg.get("mode", "launch")).lower() == "cdp":
        cdp_port = int(browser_cfg.get("cdp_port", 9222))
        add("chrome-cdp", _tcp("127.0.0.1", cdp_port), f"port {cdp_port}; start via scripts/start_chrome_cdp.sh")
    chromium = _playwright_chromium()
    add("playwright-chromium (optional)", chromium is not None, chromium or "not installed; agent-browser backend is used", optional=True)
    x_profile = home / ".local/state/intel-agent/x-profile"
    yandex_profile = home / ".local/state/intel-agent/yandex-profile"
    add("x-profile", (x_profile / ".intel-login-ok").exists(), "login once via scripts/x_login.py")
    add("yandex-profile", (yandex_profile / ".intel-login-ok").exists(), "login once via scripts/yandex_login.py")

    wx = Path(str(settings["delivery"]["wechat"].get("script", "")))
    add("wechat-script", wx.exists(), str(wx))
    mail = Path(str(settings["delivery"]["email"].get("cli", "")))
    add("email-cli", mail.exists(), str(mail))

    reports = Path(str(settings["paths"]["reports_dir"]))
    add("reports-dir-writable", reports.exists() or reports.parent.exists(), str(reports))
    return checks


def render_checks(checks: list[dict]) -> str:
    lines = []
    for check in checks:
        if check["ok"]:
            mark = "OK  "
        else:
            mark = "WARN" if check.get("optional") else "FAIL"
        lines.append(f"[{mark}] {check['name']}: {check['detail']}")
    return "\n".join(lines)
