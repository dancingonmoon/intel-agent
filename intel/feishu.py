"""Publish the report to Feishu Docs and send the reading link.

The doc is imported as a native docx owned by the logged-in user; the link is
then sent as an interactive card with a button. Requires `lark-cli` with a
ready user token and a bot that can DM the user.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .contracts import FACET_LABELS, Digest, Report


def available() -> bool:
    return shutil.which("lark-cli") is not None


def _run(cmd: list[str], timeout: int = 180) -> tuple[bool, dict | str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
    stdout = (proc.stdout or "").strip()
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return proc.returncode == 0, (stdout or (proc.stderr or "").strip())[:1000]
    ok = proc.returncode == 0 and bool(data.get("ok", True))
    return ok, data


def whoami_open_id() -> str:
    ok, data = _run(["lark-cli", "whoami", "--json"], timeout=30)
    if ok and isinstance(data, dict):
        return str((data.get("onBehalfOf") or {}).get("openId") or "")
    return ""


STATE_PATH = Path.home() / ".local/state/intel-agent/feishu-cards.json"


def _load_card_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_card_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def recall(message_id: str, as_identity: str = "bot") -> tuple[bool, str]:
    """Recall one bot message. The user has authorized automatic card recall."""
    if not message_id:
        return False, "empty message_id"
    ok, data = _run(
        [
            "lark-cli",
            "im",
            "messages",
            "delete",
            "--message-id",
            message_id,
            "--yes",
            "--as",
            as_identity,
            "--json",
        ],
        timeout=30,
    )
    return ok, str(data)[:300]


def recall_previous(topic_id: str, keep_id: str, as_identity: str = "bot") -> list[dict]:
    """Recall all previously recorded cards for this topic except the new one."""
    state = _load_card_state()
    previous = [str(x) for x in state.get(topic_id, []) if str(x) and str(x) != keep_id]
    results: list[dict] = []
    failed: list[str] = []
    for message_id in previous:
        ok, output = recall(message_id, as_identity)
        results.append({"message_id": message_id, "ok": ok, "output": output})
        if not ok:
            failed.append(message_id)
    # Keep the new card plus any failed recalls so the next run can retry.
    state[topic_id] = ([keep_id] if keep_id else []) + failed
    _save_card_state(state)
    return results


def _safe_name(title: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "-", title).strip()
    return (name[:80] or "intel-report") + ".md"


def publish(markdown_path: Path, title: str) -> dict:
    """Import the Markdown report as a Feishu docx. Returns {ok,url,token,error}."""
    if not available():
        return {"ok": False, "url": "", "token": "", "error": "lark-cli not found"}
    # lark-cli only reads from cwd, /tmp or ~/files. Copy to /tmp.
    work = Path("/tmp/intel-feishu")
    work.mkdir(parents=True, exist_ok=True)
    local = work / _safe_name(title)
    local.write_text(Path(markdown_path).read_text(encoding="utf-8"), encoding="utf-8")
    ok, data = _run(
        [
            "lark-cli",
            "drive",
            "+import",
            "--file",
            str(local),
            "--type",
            "docx",
            "--name",
            title,
            "--as",
            "user",
            "--json",
        ],
        timeout=240,
    )
    if not ok or not isinstance(data, dict):
        return {"ok": False, "url": "", "token": "", "error": str(data)[:500]}
    payload = data.get("data") or {}
    return {
        "ok": bool(payload.get("url")),
        "url": str(payload.get("url") or ""),
        "token": str(payload.get("token") or ""),
        "error": "" if payload.get("url") else str(data.get("error") or data)[:500],
    }


def send_link(url: str, text: str, open_id: str = "", as_identity: str = "bot") -> tuple[bool, str]:
    if not available():
        return False, "lark-cli not found"
    open_id = open_id or whoami_open_id()
    if not open_id:
        return False, "no Feishu open_id (run lark-cli auth login)"
    ok, data = _run(
        [
            "lark-cli",
            "im",
            "+messages-send",
            "--user-id",
            open_id,
            "--markdown",
            text,
            "--as",
            as_identity,
            "--json",
        ],
        timeout=90,
    )
    if ok:
        return True, str(data)[:500]
    return False, str(data)[:500]


def _period(digest: Digest) -> str:
    try:
        start = datetime.fromisoformat(digest.window.start)
        end = datetime.fromisoformat(digest.window.end)
        if start.tzinfo is None:
            start = start.replace(tzinfo=end.tzinfo)
        hours = (end - start).total_seconds() / 3600
    except (TypeError, ValueError):
        return "今日"
    return "本周" if hours >= 48 else "今日"


def build_card(
    title: str,
    url: str,
    digest: Digest,
    report: Report,
    lines: list[str],
    subtitle: str = "",
) -> dict:
    """Build the Card 2.0 payload: four facet counts, headlines and a button."""
    counts = {facet: len(report.sections.get(facet, [])) for facet in FACET_LABELS}
    columns = []
    for facet, label in FACET_LABELS.items():
        columns.append(
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "background_style": "grey-50",
                "padding": "12px",
                "vertical_spacing": "2px",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": f"## <font color='blue'>{counts[facet]}</font>",
                        "text_align": "center",
                    },
                    {
                        "tag": "markdown",
                        "content": f"<font color='grey'>{label}</font>",
                        "text_align": "center",
                        "text_size": "notation",
                    },
                ],
            }
        )

    points = [
        line[1:].strip() if line.startswith("·") else line.strip()
        for line in lines[:4]
        if line.strip()
    ]
    period = _period(digest)
    body_points = "\n".join(f"- {point}" for point in points) or f"- {period}暂无入选条目"
    subtitle = subtitle or f"{report.report_date} · 采集 {len(digest.items)} 条 · 四维分类"

    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": "blue",
            "icon": {"tag": "standard_icon", "token": "file-lark-minutes_colorful"},
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "elements": [
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_spacing": "8px",
                    "margin": "0px 0px 12px 0px",
                    "columns": columns,
                },
                {
                    "tag": "markdown",
                    "content": f"**{period}要点**\n{body_points}",
                    "margin": "0px 0px 12px 0px",
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "阅读完整报告"},
                    "type": "primary_filled",
                    "width": "fill",
                    "behaviors": [{"type": "open_url", "default_url": url}],
                },
            ],
        },
    }


def send_card(
    url: str,
    title: str,
    digest: Digest,
    report: Report,
    lines: list[str],
    open_id: str = "",
    as_identity: str = "bot",
    topic_id: str = "",
    recall_previous_cards: bool = True,
) -> tuple[bool, str]:
    """Send an interactive card whose button opens the published doc."""
    if not available():
        return False, "lark-cli not found"
    open_id = open_id or whoami_open_id()
    if not open_id:
        return False, "no Feishu open_id (run lark-cli auth login)"
    card = build_card(title, url, digest, report, lines)
    ok, data = _run(
        [
            "lark-cli",
            "im",
            "+messages-send",
            "--user-id",
            open_id,
            "--msg-type",
            "interactive",
            "--content",
            json.dumps(card, ensure_ascii=False),
            "--as",
            as_identity,
            "--json",
        ],
        timeout=90,
    )
    if ok:
        message_id = ""
        if isinstance(data, dict):
            message_id = str((data.get("data") or {}).get("message_id") or "")
        recall_note = ""
        if message_id and topic_id and recall_previous_cards:
            try:
                results = recall_previous(topic_id, message_id, as_identity)
                failed = [row for row in results if not row.get("ok")]
                recall_note = f"; recalled={len(results) - len(failed)} failed={len(failed)}"
            except OSError as exc:
                recall_note = f"; recall error: {exc}"
        return True, str(data)[:500] + recall_note

    # Older clients or a card-schema rejection should not lose the report.
    card_error = str(data)[:300]
    text = "\n".join([f"**{title}**", f"[在飞书打开完整报告]({url})", ""] + list(lines))
    fallback_ok, fallback_output = send_link(url, text, open_id, as_identity)
    if fallback_ok:
        return True, f"card failed, fell back to post: {card_error} | {fallback_output}"
    return False, f"card failed: {card_error} | post failed: {fallback_output}"
