"""Minimal Markdown → HTML renderer for the report's small syntax subset.

The reports are generated here, so we only need headings, blockquotes, lists,
bold labels and links. Keeping it local avoids a Markdown dependency.
"""

from __future__ import annotations

import html
import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

CSS = """
@page { size: A4; margin: 16mm; }
* { box-sizing: border-box; }
body {
  font-family: "Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans", sans-serif;
  max-width: 860px; margin: 0 auto; padding: 24px 8px; color: #1f2328;
  line-height: 1.75; font-size: 15px;
}
h1 { font-size: 26px; line-height: 1.35; border-bottom: 2px solid #2f6feb;
     padding-bottom: 10px; margin: 0 0 18px; }
h2 { font-size: 20px; color: #1f4e9c; margin: 30px 0 12px; }
h3 { font-size: 17px; margin: 22px 0 8px; }
p { margin: 8px 0; }
a { color: #0b63ce; text-decoration: none; word-break: break-all; }
blockquote { background: #f5f7fb; border-left: 4px solid #2f6feb;
             margin: 12px 0; padding: 10px 16px; color: #555; }
blockquote p { margin: 2px 0; }
ul { padding-left: 22px; margin: 6px 0 14px; }
li { margin: 4px 0; }
hr { border: none; border-top: 1px solid #eaecef; margin: 24px 0; }
"""


def _inline(text: str) -> str:
    text = html.escape(text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)
    return text


def markdown_to_html(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    in_list = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append("<p>" + "<br>".join(_inline(line) for line in paragraph) + "</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            close_list()
            continue
        if stripped.startswith("### "):
            flush_paragraph(); close_list()
            out.append(f"<h3>{_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            flush_paragraph(); close_list()
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            flush_paragraph(); close_list()
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped.startswith("> "):
            flush_paragraph(); close_list()
            out.append(f"<blockquote><p>{_inline(stripped[2:])}</p></blockquote>")
        elif stripped.startswith("- "):
            flush_paragraph()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
        else:
            close_list()
            paragraph.append(stripped)
    flush_paragraph()
    close_list()
    return "\n".join(out)


def render_html(markdown_text: str, title: str = "") -> str:
    body = markdown_to_html(markdown_text)
    safe_title = html.escape(title or "每日情报")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>{CSS}</style>
</head>
<body>
{body}
</body>
</html>
"""
