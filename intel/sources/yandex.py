"""Yandex News search through a logged-in CDP Chrome.

`yandex.ru/news/search` redirects to `dzen.ru/news/search`, which returns
recent news cards with publisher, date and snippet. The adapter reads those
cards; it does not bypass SmartCaptcha. A captcha stops the source and is
recorded as an incident.
"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from . import agent_browser
from .base import Article, SourceResult, clean_html

_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_DATE_RE = re.compile(
    r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"(?:\s+в\s+(\d{1,2}):(\d{2}))?"
)


def _parse_ru_date(text: str) -> str:
    text = (text or "").lower()
    tz = timezone(timedelta(hours=3))  # Moscow
    match = _DATE_RE.search(text)
    if match:
        day, month = int(match.group(1)), _MONTHS[match.group(2)]
        hour, minute = int(match.group(3) or 0), int(match.group(4) or 0)
        now = datetime.now(tz)
        year = now.year - 1 if month > now.month + 1 else now.year
        try:
            return datetime(year, month, day, hour, minute, tzinfo=tz).isoformat()
        except ValueError:
            return ""
    if "сегодня" in text:
        return datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if "вчера" in text:
        return (datetime.now(tz) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return ""

_EXTRACT_JS = """
JSON.stringify(Array.from(document.querySelectorAll('a[href*="/news/story/"]')).map(a => {
  let card = a;
  for (let i = 0; i < 6 && card && (card.innerText || '').length < 120; i++) card = card.parentElement;
  const text = ((card && card.innerText) || a.innerText || '').trim();
  return {
    title: (a.innerText || '').trim(),
    url: a.href.split('?')[0],
    snippet: text.slice(0, 500)
  };
}).filter(x => x.title && x.url))
"""

_PROBE_JS = """
JSON.stringify({
  href: location.href,
  stories: document.querySelectorAll('a[href*="/news/story/"]').length,
  captcha: !!document.querySelector('.CheckboxCaptcha, #captcha-container, form[action*="showcaptcha"]'),
  webdriver: navigator.webdriver,
  body: (document.body ? document.body.innerText : '').slice(0, 400)
})
"""

_last_call = 0.0


def _profile(settings: dict) -> Path:
    return Path(str(settings.get("browser", {}).get("yandex_profile", "~/.local/state/intel-agent/yandex-profile"))).expanduser()


def _throttle(settings: dict) -> None:
    global _last_call
    interval = float(settings.get("yandex", {}).get("min_interval_seconds", 40))
    if interval <= 0:
        return
    wait = interval - (time.monotonic() - _last_call) + random.uniform(0, 10)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _incident(settings: dict, profile: Path, reason: str) -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    folder = Path(str(settings["paths"]["state_dir"])) / "incidents" / f"yandex-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    agent_browser.screenshot(profile, folder / "page.png")
    html = agent_browser.get_html(profile)
    if html:
        (folder / "page.html").write_text(html, encoding="utf-8")
    (folder / "reason.txt").write_text(reason, encoding="utf-8")
    return str(folder)


def search(query: str, lang: str, settings: dict) -> SourceResult:
    started = time.time()
    profile = _profile(settings)
    agent_browser.configure(settings)
    if not agent_browser.available():
        return SourceResult(source="yandex", articles=[], ok=False, error="agent-browser not found")

    lr = str(settings.get("yandex", {}).get("lr", "213"))
    url = f"https://yandex.ru/news/search?text={quote_plus(query)}&lr={lr}"

    _throttle(settings)
    opened = agent_browser.navigate(profile, url, headed=bool(settings.get("browser", {}).get("headed", False)))
    if not opened.ok:
        return SourceResult(source="yandex", articles=[], ok=False, error=f"navigate failed: {opened.error}", duration_ms=int((time.time() - started) * 1000))

    state: dict = {}
    for _ in range(20):
        time.sleep(1.5)
        probe = agent_browser.eval_js(profile, _PROBE_JS)
        if probe.ok and isinstance(probe.value, dict):
            state = probe.value
            if state.get("stories") or state.get("captcha"):
                break
            body = str(state.get("body", ""))
            href = str(state.get("href", ""))
            # Yandex first shows a short "checking your browser" interstitial;
            # wait it out rather than treating it as the interactive captcha.
            if "showcaptchafast" in href or "Checking your browser" in body or "Проверка" in body:
                continue

    if "showcaptcha" in str(state.get("href", "")) or state.get("captcha"):
        incident = _incident(settings, profile, "yandex captcha")
        return SourceResult(source="yandex", articles=[], ok=False, error=f"captcha; evidence: {incident}", duration_ms=int((time.time() - started) * 1000))

    extracted = agent_browser.eval_js(profile, _EXTRACT_JS, timeout=60)
    if not extracted.ok:
        return SourceResult(source="yandex", articles=[], ok=False, error=f"extract failed: {extracted.error}", duration_ms=int((time.time() - started) * 1000))

    rows = extracted.value or []
    if not isinstance(rows, list):
        rows = []

    articles: list[Article] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        link = str(row.get("url", "")).strip()
        title = clean_html(str(row.get("title", "")))
        if not link or not title or link in seen:
            continue
        seen.add(link)
        snippet = clean_html(str(row.get("snippet", "")))[:1000]
        articles.append(
            Article(
                title=title,
                url=link,
                source="Yandex News",
                source_kind="aggregator",
                published_at=_parse_ru_date(snippet),
                snippet=snippet,
                lang=lang,
                query=query,
            )
        )

    return SourceResult(
        source="yandex",
        articles=articles,
        ok=True,
        duration_ms=int((time.time() - started) * 1000),
    )
