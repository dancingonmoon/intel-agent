"""Small curl-based HTTP client.

The machine has no PySocks and the proxy is a mixed SOCKS/HTTP inbound on
127.0.0.1:10808. Using curl keeps DNS resolution on the proxy side
(`--socks5-hostname`) and avoids another Python dependency.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class HttpResult:
    status: int
    text: str
    final_url: str
    error: str | None = None
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 400


def _proxy_args(proxy: str) -> list[str]:
    proxy = (proxy or "").strip()
    if not proxy:
        return []
    if proxy.startswith("socks5h://") or proxy.startswith("socks5://"):
        return ["--socks5-hostname", proxy.split("://", 1)[1]]
    if proxy.startswith("http://") or proxy.startswith("https://"):
        return ["--proxy", proxy]
    return ["--proxy", proxy]


def fetch(
    url: str,
    settings: dict,
    *,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    retry_sleep: float = 1.5,
) -> HttpResult:
    http_cfg = settings.get("http", {})
    timeout = int(timeout if timeout is not None else http_cfg.get("timeout_seconds", 25))
    retries = int(retries if retries is not None else http_cfg.get("retries", 2))
    ua = str(http_cfg.get("user_agent", "intel-agent/0.1"))
    proxy = str(settings.get("proxy", {}).get("url", ""))

    last_error = "unknown error"
    started = time.time()
    for attempt in range(retries + 1):
        with tempfile.NamedTemporaryFile(prefix="intel-http-", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        cmd = [
            "curl",
            "-sS",
            "-L",
            "--compressed",
            "--max-time",
            str(timeout),
            "-A",
            ua,
            "-o",
            str(tmp_path),
            "-w",
            "%{http_code}\t%{url_effective}",
        ]
        cmd.extend(_proxy_args(proxy))
        for key, value in (headers or {}).items():
            cmd.extend(["-H", f"{key}: {value}"])
        cmd.append(url)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
            meta = (proc.stdout or "").strip().split("\t")
            status = int(meta[0]) if meta and meta[0].isdigit() else 0
            final_url = meta[1] if len(meta) > 1 else url
            text = tmp_path.read_text(encoding="utf-8", errors="replace") if tmp_path.exists() else ""
            if status and status < 500:
                return HttpResult(
                    status=status,
                    text=text,
                    final_url=final_url,
                    error=None if status < 400 else (proc.stderr or "").strip(),
                    duration_ms=int((time.time() - started) * 1000),
                )
            last_error = (proc.stderr or f"HTTP {status}").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
        finally:
            tmp_path.unlink(missing_ok=True)
        if attempt < retries:
            time.sleep(retry_sleep * (attempt + 1))

    return HttpResult(
        status=0,
        text="",
        final_url=url,
        error=last_error,
        duration_ms=int((time.time() - started) * 1000),
    )
