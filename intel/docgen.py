"""Render the Markdown report to PDF (and optional PNG pages).

Uses the system Chrome in headless mode. No Pandoc/Playwright dependency.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .html_render import render_html

CHROME_CANDIDATES = ("/usr/bin/google-chrome", "/usr/bin/microsoft-edge")


def _chrome() -> str | None:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def html_to_pdf(html_text: str, pdf_path: Path, title: str = "") -> tuple[bool, str]:
    chrome = _chrome()
    if not chrome:
        return False, "chrome not found"
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    html_path = pdf_path.with_suffix(".html")
    html_path.write_text(html_text, encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="intel-chrome-") as tmp:
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--user-data-dir={tmp}",
            f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{exc.__class__.__name__}: {exc}"
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        return False, (proc.stderr or proc.stdout or "pdf not produced").strip()[:1000]
    return True, str(pdf_path)


def pdf_to_pngs(pdf_path: Path, assets_dir: Path, dpi: int = 150, max_pages: int = 3) -> list[Path]:
    pdf_path = Path(pdf_path)
    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)
    for old in assets_dir.glob("page-*.png"):
        old.unlink(missing_ok=True)
    cmd = [
        "pdftoppm",
        "-png",
        "-r",
        str(dpi),
        "-f",
        "1",
        "-l",
        str(max_pages),
        str(pdf_path),
        str(assets_dir / "page"),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(assets_dir.glob("page-*.png"))


def render_bundle(markdown_text: str, report_path: Path, title: str = "", max_pages: int = 3) -> dict:
    """Write .html/.pdf and page PNGs next to the Markdown report."""
    report_path = Path(report_path)
    html_text = render_html(markdown_text, title=title)
    pdf_path = report_path.with_suffix(".pdf")
    ok, detail = html_to_pdf(html_text, pdf_path, title=title)
    result = {"pdf": str(pdf_path) if ok else "", "pdf_error": "" if ok else detail, "images": []}
    if ok:
        images = pdf_to_pngs(pdf_path, report_path.parent / "assets" / report_path.stem, max_pages=max_pages)
        result["images"] = [str(p) for p in images]
    return result
