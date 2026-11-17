"""X/Twitter search through a logged-in agent-browser profile.

No API key is used. The browser profile is created by `scripts/x_login.py` and
reused read-only. Selectors are isolated in `X_SELECTORS`; a page that has
content but no tweets raises a drift error instead of being reported as a
quiet day.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

from . import agent_browser
from .base import Article, SourceResult

X_SELECTORS = {
    "tweet": 'article[data-testid="tweet"]',
    "text": '[data-testid="tweetText"]',
    "time": "time[datetime]",
    "status_link": 'a[href*="/status/"]',
    "user": '[data-testid="User-Name"]',
    "login_button": '[data-testid="loginButton"]',
    "account": '[data-testid="SideNav_AccountSwitcher_Button"]',
}

_EXTRACT_JS = """
JSON.stringify(Array.from(document.querySelectorAll('article[data-testid="tweet"]')).map(a => {
  const text = a.querySelector('[data-testid="tweetText"]');
  const time = a.querySelector('time[datetime]');
  const links = Array.from(a.querySelectorAll('a[href*="/status/"]'));
  const status = links.find(x => x.href.includes('/status/'));
  const user = a.querySelector('[data-testid="User-Name"]');
  return {
    text: text ? text.innerText : '',
    datetime: time ? time.getAttribute('datetime') : '',
    url: status ? status.href : '',
    author: user ? user.innerText.split('\\n')[0] : ''
  };
}))
"""

_PROBE_JS = """
JSON.stringify({
  href: location.href,
  loginButton: !!document.querySelector('[data-testid="loginButton"]'),
  account: !!document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]'),
  home: !!document.querySelector('[data-testid="AppTabBar_Home_Link"], a[href="/home"], [aria-label="Home"], [aria-label="首页"]'),
  tweets: document.querySelectorAll('article[data-testid="tweet"]').length,
  webdriver: navigator.webdriver,
  body: (document.body ? document.body.innerText : '').slice(0, 600)
})
"""

_last_call = 0.0


def _profile(settings: dict) -> Path:
    return Path(str(settings.get("browser", {}).get("x_profile", "~/.local/state/intel-agent/x-profile"))).expanduser()


def _interval(settings: dict) -> float:
    return float(settings.get("x", {}).get("min_interval_seconds", 45))


def _throttle(settings: dict) -> None:
    global _last_call
    interval = _interval(settings)
    if interval <= 0:
        return
    wait = interval - (time.monotonic() - _last_call) + random.uniform(0, 15)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _incident(settings: dict, profile: Path, reason: str) -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    folder = Path(str(settings["paths"]["state_dir"])) / "incidents" / f"x-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    agent_browser.screenshot(profile, folder / "page.png")
    html = agent_browser.get_html(profile)
    if html:
        (folder / "page.html").write_text(html, encoding="utf-8")
    (folder / "reason.txt").write_text(reason, encoding="utf-8")
    return str(folder)


def search(query: str, settings: dict, window_hours: int = 24) -> SourceResult:
    started = time.time()
    profile = _profile(settings)
    agent_browser.configure(settings)
    if not agent_browser.available():
        return SourceResult(source="x", articles=[], ok=False, error="agent-browser not found")
    if not agent_browser.login_confirmed(profile):
        return SourceResult(
            source="x",
            articles=[],
            ok=False,
            error="X login not confirmed; run scripts/x_login.py",
        )

    # X's search UI is sensitive to date operators combined with quoted
    # Cyrillic queries. Fetch plain results and let the pipeline's published_at
    # window filter do the date cut.
    q = str(query)
    url = "https://x.com/search?q=" + quote_plus(q) + "&src=typed_query&f=live"

    _throttle(settings)
    opened = agent_browser.navigate(profile, url, headed=bool(settings.get("browser", {}).get("headed", False)))
    if not opened.ok:
        return SourceResult(source="x", articles=[], ok=False, error=f"navigate failed: {opened.error}", duration_ms=int((time.time() - started) * 1000))

    # X is a slow SPA; wait for actual tweets / login wall / explicit empty
    # state. Do NOT break on generic body text (tabs render long before results).
    state: dict = {}
    for _ in range(30):
        time.sleep(1.5)
        probe = agent_browser.eval_js(profile, _PROBE_JS)
        if probe.ok and isinstance(probe.value, dict):
            state = probe.value
            href = str(state.get("href", ""))
            body = str(state.get("body", ""))
            if (
                state.get("tweets")
                or state.get("loginButton")
                or "/i/flow/login" in href
                or "/i/jf/onboarding" in href
                or "mode=login" in href
                or "没有关于" in body
                or "no results" in body.lower()
            ):
                break

    if not state:
        return SourceResult(source="x", articles=[], ok=False, error="probe failed or page did not render", duration_ms=int((time.time() - started) * 1000))

    if state.get("webdriver") and not state.get("tweets"):
        return SourceResult(
            source="x",
            articles=[],
            ok=False,
            error="navigator.webdriver=true; x.com will not render. Use browser.mode=cdp with a normal Chrome on port 9222.",
            duration_ms=int((time.time() - started) * 1000),
        )

    href = str(state.get("href", ""))
    body = str(state.get("body", ""))
    if href.startswith("chrome-error://") or "HTTP ERROR 403" in body or "拒绝访问" in body or "Access denied" in body:
        incident = _incident(settings, profile, f"x.com blocked: {href} {body[:200]}")
        agent_browser.clear_login(profile)
        return SourceResult(
            source="x",
            articles=[],
            ok=False,
            error=f"x.com blocked (HTTP 403) from current exit IP; switch proxy node. evidence: {incident}",
            duration_ms=int((time.time() - started) * 1000),
        )

    body_lower = body.lower()
    if "出错了" in body or "something went wrong" in body_lower:
        incident = _incident(settings, profile, f"x search error: {body[:200]}")
        return SourceResult(
            source="x",
            articles=[],
            ok=False,
            error=f"X search error (query rejected or rate limited); evidence: {incident}",
            duration_ms=int((time.time() - started) * 1000),
        )
    login_wall = (
        "showcaptcha" in href
        or "/i/flow/login" in href
        or "/i/jf/onboarding" in href
        or "mode=login" in href
        or state.get("loginButton")
        or (
            not state.get("tweets")
            and not state.get("account")
            and not state.get("home")
            and (
                "电子邮箱或用户名" in body
                or "使用 google 继续" in body_lower
                or "sign in" in body_lower
                or "登录" in body
            )
        )
    )
    if login_wall:
        incident = _incident(settings, profile, "login wall or captcha")
        agent_browser.clear_login(profile)
        return SourceResult(source="x", articles=[], ok=False, error=f"X login required or expired; evidence: {incident}", duration_ms=int((time.time() - started) * 1000))

    seen: dict[str, Article] = {}
    max_attempts = 4
    for attempt in range(max_attempts):
        extracted = agent_browser.eval_js(profile, _EXTRACT_JS, timeout=60)
        if not extracted.ok:
            return SourceResult(source="x", articles=[], ok=False, error=f"extract failed: {extracted.error}", duration_ms=int((time.time() - started) * 1000))
        rows = extracted.value or []
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            link = str(row.get("url", "")).strip()
            text = str(row.get("text", "")).strip()
            if not link or not text or link in seen:
                continue
            first_line = text.split("\n", 1)[0].strip()
            title = first_line if len(first_line) <= 140 else first_line[:137].rstrip() + "…"
            seen[link] = Article(
                title=title,
                url=link,
                source="X",
                source_kind="social",
                published_at=str(row.get("datetime", "")),
                snippet=text[:1000],
                lang="",
                query=query,
            )
        if seen and attempt >= 1:
            break
        if attempt < max_attempts - 1:
            agent_browser.run(profile, ["scroll", "down", "1400"], timeout=30)
            time.sleep(8)

    if not seen:
        body = str(state.get("body", "")).lower()
        if (
            "no results" in body
            or "no posts" in body
            or "ничего не найдено" in body
            or "没有关于" in body
            or "没有结果" in body
        ):
            return SourceResult(source="x", articles=[], ok=True, duration_ms=int((time.time() - started) * 1000))
        incident = _incident(settings, profile, "0 tweets but page has content (selector drift?)")
        return SourceResult(source="x", articles=[], ok=False, error=f"selector drift or empty page; evidence: {incident}", duration_ms=int((time.time() - started) * 1000))

    return SourceResult(
        source="x",
        articles=list(seen.values()),
        ok=True,
        duration_ms=int((time.time() - started) * 1000),
    )
