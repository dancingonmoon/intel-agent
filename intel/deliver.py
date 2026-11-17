"""Local archive plus Feishu, WeChat and email delivery.

Feishu is the primary channel when configured: the Markdown is imported as a
native docx and the reading link is sent by the bot. If that fails, WeChat
sends a short text plus a PDF attachment. Email stays text-only in v1.
"""

from __future__ import annotations

import json
import os
import shutil
import smtplib
import ssl
import subprocess
from email.message import EmailMessage
from pathlib import Path

from .contracts import FACET_LABELS, Digest, Report


def local_path(settings: dict, topic_id: str, report_date: str) -> Path:
    return Path(settings["paths"]["reports_dir"]) / topic_id / f"{report_date}.md"


def write_local(markdown: str, settings: dict, topic_id: str, report_date: str) -> Path:
    path = local_path(settings, topic_id, report_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(markdown, encoding="utf-8")
    tmp.replace(path)
    return path


def _headline_lines(digest: Digest, report: Report, limit: int = 5) -> list[str]:
    items = digest.items_by_id
    titles = {
        entry.item_id: entry.title_zh
        for entries in report.sections.values()
        for entry in entries
        if entry.title_zh
    }
    lines: list[str] = []
    for item_id in report.headline_item_ids[:limit]:
        item = items.get(item_id)
        if item:
            title = titles.get(item_id) or report.title_en.get(item_id) or item.title_original
            lines.append(f"· {title}（{item.source}）")
    if not lines:
        for facet in ("contracts", "industry", "technology", "players"):
            for entry in report.sections.get(facet, [])[:1]:
                item = items.get(entry.item_id)
                if item:
                    lines.append(f"· {entry.title_zh or report.title_en.get(entry.item_id) or item.title_original}（{item.source}）")
            if len(lines) >= limit:
                break
    return lines


def wechat_text(digest: Digest, report: Report, topic: dict, path: Path, max_chars: int = 1200) -> str:
    counts = " / ".join(
        f"{FACET_LABELS[facet]} {len(report.sections.get(facet, []))}" for facet in FACET_LABELS
    )
    lines = [
        f"【{topic.get('name', digest.topic)}】{report.report_date}",
        counts,
    ]
    lines.extend(_headline_lines(digest, report))
    lines.append(f"全文：{path}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20].rstrip() + "\n…（已截断）"
    return text


def email_text(digest: Digest, report: Report, topic: dict, path: Path) -> str:
    lines = [
        f"{topic.get('name', digest.topic)} · 每日情报 {report.report_date}",
        "",
        f"条目：采集 {len(digest.items)}，入选 {sum(len(v) for v in report.sections.values())}",
        "",
        "今日要点：",
    ]
    lines.extend(_headline_lines(digest, report, limit=8) or ["· 今日无入选条目"])
    lines.extend(["", f"全文：{path}", ""])
    coverage = report.coverage
    if coverage.failed:
        lines.append(f"失败来源：{', '.join(coverage.failed)}")
    if coverage.degraded:
        lines.append(f"降级来源：{', '.join(coverage.degraded)}")
    if report.degraded:
        lines.append(f"自动摘要不可用：{report.degraded}")
    return "\n".join(lines)


def _run(cmd: list[str], *, stdin: str | None = None, timeout: int = 60) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, input=stdin, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
    output = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return proc.returncode == 0, output


def send_wechat(text: str, settings: dict) -> tuple[bool, str]:
    cfg = settings["delivery"]["wechat"]
    script = Path(str(cfg["script"]))
    if not script.exists():
        return False, f"wechat script not found: {script}"
    ok, output = _run([str(script), text], timeout=45)
    if not ok and "prepare failed" in output:
        return False, "WeChat context token expired; send any message to the bot, then retry"
    return ok, output


def _wechat_user(settings: dict) -> str:
    contacts = Path.home() / ".weixin-mcp" / "contacts.json"
    try:
        data = json.loads(contacts.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return next(iter(data), "")


def send_wechat_file(path: Path, caption: str, settings: dict) -> tuple[bool, str]:
    exe = shutil.which("weixin-mcp")
    if not exe:
        return False, "weixin-mcp not found on PATH"
    user = _wechat_user(settings)
    if not user:
        return False, "no WeChat contact in ~/.weixin-mcp/contacts.json"
    cmd = [exe, "send", user, "--file", str(path), "--caption", caption]
    ok, output = _run(cmd, timeout=120)
    if not ok and "prepare failed" in output:
        return False, "WeChat context token expired; send any message to the bot, then retry"
    return ok, output


def send_email(text: str, settings: dict, subject: str) -> tuple[bool, str]:
    cfg = settings["delivery"]["email"]
    mode = str(cfg.get("mode", "smtp")).lower()
    if mode == "smtp":
        host = str(cfg.get("host") or "").strip()
        recipients = [x.strip() for x in str(cfg.get("to") or "").split(",") if x.strip()]
        if not host or not recipients:
            return False, "email smtp host/to not configured"
        user = str(cfg.get("user") or "")
        password = os.getenv(str(cfg.get("password_env", "SMTP_PASSWORD")), "")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = user or f"intel-agent@{host}"
        message["To"] = ", ".join(recipients)
        message.set_content(text)
        port = int(cfg.get("port", 587))
        try:
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                server = smtplib.SMTP(host, port, timeout=30)
                if cfg.get("tls", True):
                    server.starttls(context=ssl.create_default_context())
            if user:
                server.login(user, password)
            server.send_message(message)
            server.quit()
            return True, "smtp sent"
        except (OSError, smtplib.SMTPException) as exc:
            return False, f"smtp failed: {exc.__class__.__name__}: {exc}"
    cli = Path(str(cfg.get("cli") or ""))
    if not cli.exists():
        return False, f"email cli not found: {cli}"
    cmd = ["python3", "-I", str(cli), "send", str(cfg["to"]), subject]
    return _run(cmd, stdin=text, timeout=60)


def send_feishu(digest: Digest, report: Report, topic: dict, topic_id: str, path: Path, cfg: dict) -> dict:
    """Publish the Markdown as a Feishu docx and DM a card with the link."""
    from .feishu import publish, send_card

    title = f"{topic.get('name', topic_id)} {report.report_date}"
    pub = publish(path, title)
    if not pub.get("ok"):
        return {"ok": False, "error": pub.get("error", "publish failed")}
    ok, output = send_card(
        pub["url"],
        title,
        digest,
        report,
        _headline_lines(digest, report),
        str(cfg.get("open_id", "")),
        str(cfg.get("as", "bot")),
        topic_id=topic_id,
        recall_previous_cards=bool(cfg.get("recall_previous", True)),
    )
    return {"ok": ok, "url": pub["url"], "output": output}


def deliver(
    markdown: str,
    digest: Digest,
    report: Report,
    topic: dict,
    settings: dict,
    *,
    dry_run: bool = False,
    enable_all: bool = False,
) -> dict:
    topic_id = str(topic.get("id", digest.topic))
    path = write_local(markdown, settings, topic_id, report.report_date)
    results: dict[str, dict] = {"local": {"ok": True, "path": str(path)}}

    wx_text = wechat_text(digest, report, topic, path, int(settings["delivery"]["wechat"].get("max_chars", 1200)))
    mail_text = email_text(digest, report, topic, path)
    subject = f"{settings['delivery']['email'].get('subject_prefix', '[intel]')} {topic.get('name', topic_id)} {report.report_date}"

    wx_enabled = enable_all or bool(settings["delivery"]["wechat"].get("enabled"))
    mail_enabled = enable_all or bool(settings["delivery"]["email"].get("enabled"))
    feishu_cfg = settings.get("delivery", {}).get("feishu", {})
    feishu_enabled = enable_all or bool(feishu_cfg.get("enabled"))
    primary = str(settings.get("delivery", {}).get("primary", "wechat")).lower()
    # In primary mode Feishu is tried first; WeChat is only a fallback.
    feishu_primary = primary == "feishu" and feishu_enabled and not enable_all
    if feishu_primary:
        wx_enabled = False

    wx_cfg = settings["delivery"]["wechat"]
    fmt = str(wx_cfg.get("format", "pdf")).lower()
    pdf_needed = fmt in ("pdf", "both", "image")
    pdf_path = path.with_suffix(".pdf")

    if dry_run:
        results["wechat"] = {
            "ok": True,
            "skipped": not wx_enabled,
            "fallback": feishu_primary,
            "payload": wx_text,
            "pdf_planned": str(pdf_path) if pdf_needed else "",
        }
        results["email"] = {"ok": True, "skipped": not mail_enabled, "subject": subject, "payload": mail_text}
        results["feishu"] = {"ok": True, "skipped": not feishu_enabled, "primary": feishu_primary}
        return results

    if feishu_primary:
        results["feishu"] = send_feishu(digest, report, topic, topic_id, path, feishu_cfg)
        if not results["feishu"].get("ok"):
            wx_enabled = True
            results["feishu"]["fallback_to_wechat"] = True

    if wx_enabled:
        ok, output = send_wechat(wx_text, settings)
        results["wechat"] = {"ok": ok, "output": output}

        if pdf_needed:
            from .docgen import render_bundle

            title = f"{topic.get('name', topic_id)} {report.report_date}"
            bundle = render_bundle(
                markdown,
                path,
                title=title,
                max_pages=int(wx_cfg.get("max_images", 3)),
            )
            if bundle.get("pdf"):
                file_ok, file_out = send_wechat_file(
                    Path(bundle["pdf"]), f"{title} 完整报告（PDF）", settings
                )
                results["wechat_pdf"] = {"ok": file_ok, "output": file_out, "path": bundle["pdf"]}
            else:
                results["wechat_pdf"] = {
                    "ok": False,
                    "error": bundle.get("pdf_error", "pdf generation failed"),
                }

            if not results["wechat_pdf"].get("ok"):
                detail = results["wechat_pdf"].get("output") or results["wechat_pdf"].get("error", "")
                results["wechat"] = {"ok": False, "output": f"{output} | pdf: {detail}"}
            else:
                results["wechat"]["output"] = f"{output} | pdf: ok"

            if fmt in ("image", "both"):
                results["wechat_image"] = {
                    "ok": True,
                    "skipped": True,
                    "note": "iLink image API rejected in testing; PDF is used instead",
                }
    else:
        results["wechat"] = {"ok": True, "skipped": True}

    if mail_enabled:
        ok, output = send_email(mail_text, settings, subject)
        results["email"] = {"ok": ok, "output": output}
    else:
        results["email"] = {"ok": True, "skipped": True}

    if feishu_primary:
        pass  # already attempted before the WeChat fallback
    elif feishu_enabled:
        results["feishu"] = send_feishu(digest, report, topic, topic_id, path, feishu_cfg)
    else:
        results["feishu"] = {"ok": True, "skipped": True}

    return results
