"""LLM summarization with a hard deterministic degrade path.

The LLM receives a digest (facts) and returns a report (item ids + Chinese
prose). It never receives or returns URLs, original titles, sources or dates.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

import requests

from .contracts import Digest, Report, validate_report_data
from .render import build_fallback_report

_RESEARCH_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {"markdown": {"type": "string"}},
        "required": ["markdown"],
        "additionalProperties": False,
    }
)


def _looks_like_markdown(text: str) -> bool:
    if len(text.strip()) < 500:
        return False
    if "DSML" in text or "<|" in text[:200]:
        return False
    return bool(re.search(r"[一-鿿]", text))


def preflight(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _digest_for_prompt(digest: Digest, max_items: int = 40) -> list[dict]:
    rows = []
    for item in digest.items[:max_items]:
        rows.append(
            {
                "item_id": item.item_id,
                "cluster_id": item.cluster_id,
                "title_original": item.title_original,
                "lang": item.lang,
                "source": item.source,
                "published_at": item.published_at,
                "snippet": item.snippet[:400],
                "facets": {
                    "primary": item.facets.primary,
                    "secondary": item.facets.secondary,
                },
                "entities": item.entities,
                "corroboration_count": item.corroboration_count,
            }
        )
    return rows


def build_prompt(
    digest: Digest,
    topic: dict,
    glossary: dict[str, str],
    schema_text: str,
    week_items: list[dict] | None = None,
) -> str:
    payload = {
        "topic": {"id": topic.get("id"), "name": topic.get("name")},
        "report_date": digest.window.end[:10],
        "items": _digest_for_prompt(digest),
        "week_items": [
            {
                "week_id": f"w{idx:02d}",
                "title": row.get("title_original") or row.get("title") or "",
                "source": row.get("source") or "",
                "date": (row.get("published_at") or row.get("first_seen") or "")[:10],
                "facet": row.get("facet") or "",
            }
            for idx, row in enumerate((week_items or [])[:20], start=1)
        ],
        "clusters": [
            {
                "cluster_id": c.cluster_id,
                "member_count": c.member_count,
                "first_seen": c.first_seen,
                "last_seen": c.last_seen,
            }
            for c in digest.clusters
        ],
        "source_health": [
            {"source": h.source, "ok": h.ok, "count": h.count, "error": h.error}
            for h in digest.source_health
        ],
    }
    glossary_lines = "\n".join(f"- {ru} → {zh}" for ru, zh in glossary.items())
    return f"""你是行业情报分析师。请把下面的 digest 写成中文日报。

硬性规则：
1. 只能引用 digest 中存在的 item_id 和 cluster_id；不要编造任何 id。
2. 绝对不要在输出里写 URL、原文标题、来源、日期；这些由渲染器从 digest 回填。
3. 中文摘要不得出现 digest 中没有的数字、金额、日期、人名。
4. 同一事件多源报道只写一条，其余用 cluster 的 member_count 表示。
5. 四个 section 都必须存在；没有内容就留空数组，并把该 facet 写进 coverage.no_news_facets。
6. 输出必须符合下面的 JSON Schema，且只输出 JSON。
7. 另外写 week_review：3–5 条中文要点，概括过去 7 天 week_items 的背景趋势；
   只使用 week_items，不写 URL；week_items 与今日条目重合时可以把它作为周背景提及，
   不要整段复述今日摘要。
8. title_en：对 digest 中标题不是中文或英文的条目，给出忠实的英文标题翻译，
   key 用 item_id；中文/英文标题不要放进 title_en。只翻译标题，不加观点。
9. 最终展示字段（title_zh、summary_zh、why_it_matters、week_review、
   watchlist.reason）不得出现俄文或其他非中英文字符；人名、公司名、型号用英文
   转写（如 РТК-Сервис→RTK-Service，Константин Савчук→Konstantin Savchuk，
   Информтехника→Informtekhnika）。中文/英文内容保持原样。

术语表：
{glossary_lines or '（无）'}

JSON Schema：
{schema_text}

digest：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def complete(prompt: str, settings: dict) -> tuple[str, str]:
    """Free-form completion for deep research. Returns (text, status)."""
    llm = settings.get("llm", {})
    provider = str(llm.get("provider", "none"))
    if provider != "claude-cli":
        return "", "none"
    claude_bin = shutil.which(str(llm.get("claude_bin", "claude")))
    if not claude_bin:
        return "", "degraded:claude-cli-not-found"
    host = str(llm.get("preflight_host", "127.0.0.1"))
    port = int(llm.get("preflight_port", 15721))
    if not preflight(host, port):
        return "", "degraded:cc-switch-unreachable"
    args = [
        claude_bin,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        _RESEARCH_SCHEMA,
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--max-budget-usd",
        str(llm.get("max_budget_usd", 0.30)),
    ]
    model = str(llm.get("model") or "").strip()
    if model:
        args.extend(["--model", model])
    try:
        proc = subprocess.run(
            args,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=int(llm.get("timeout_seconds", 300)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"degraded:claude-run-failed:{exc.__class__.__name__}"
    stdout = (proc.stdout or "").strip()
    if not stdout:
        return "", "degraded:empty-llm-output"
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        if _looks_like_markdown(stdout):
            return stdout, "ok"
        return "", "degraded:invalid-llm-output"
    if not isinstance(payload, dict):
        return "", "degraded:invalid-llm-output"

    text = None
    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        text = structured.get("markdown")
    if not isinstance(text, str):
        result = payload.get("result")
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict) and isinstance(parsed.get("markdown"), str):
                    text = parsed["markdown"]
            except json.JSONDecodeError:
                text = result
    if isinstance(text, str) and _looks_like_markdown(text):
        return text.strip(), "ok"
    return "", "degraded:invalid-llm-output"


def _json_payload(text: str) -> dict | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _anthropic_report(prompt: str, llm: dict) -> tuple[dict | None, str]:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None, "missing-ANTHROPIC_API_KEY"
    base = (os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    model = str(llm.get("model") or "claude-sonnet-5-5")
    body = {
        "model": model,
        "max_tokens": int(llm.get("max_tokens", 4096)),
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    proxies = None
    proxy = str(llm.get("proxy") or "").strip()
    if proxy.startswith("http://") or proxy.startswith("https://"):
        proxies = {"http": proxy, "https": proxy}
    try:
        response = requests.post(
            f"{base}/v1/messages",
            headers=headers,
            json=body,
            timeout=int(llm.get("timeout_seconds", 300)),
            proxies=proxies,
        )
    except requests.RequestException as exc:
        return None, f"request-failed:{exc.__class__.__name__}"
    if response.status_code != 200:
        return None, f"http-{response.status_code}:{response.text[:200]}"
    try:
        payload = response.json()
    except ValueError:
        return None, "invalid-api-json"
    text = "".join(
        block.get("text", "")
        for block in payload.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    data = _json_payload(text)
    return (data, "") if data is not None else (None, "invalid-report-json")


def _report_or_fallback(
    data: dict | None,
    digest: Digest,
    topic: dict,
    week_items: list[dict] | None,
    reason: str,
) -> tuple[Report, str]:
    if not isinstance(data, dict):
        return build_fallback_report(digest, topic, week_items=week_items, reason=reason), f"degraded:{reason}"
    errors = validate_report_data(data, digest)
    if errors:
        return build_fallback_report(digest, topic, week_items=week_items, reason="llm-contract-violation"), "degraded:llm-contract-violation"
    try:
        return Report.from_dict(data), "ok"
    except ValueError:
        return build_fallback_report(digest, topic, week_items=week_items, reason="invalid-report-shape"), "degraded:invalid-report-shape"


def summarize(
    digest: Digest,
    topic: dict,
    settings: dict,
    glossary: dict[str, str] | None = None,
    schema_path: Path | None = None,
    week_items: list[dict] | None = None,
) -> tuple[Report, str]:
    """Return (report, status). status is one of ok / none / degraded:<reason>."""
    llm = settings.get("llm", {})
    provider = str(llm.get("provider", "none")).lower()
    claude_bin = shutil.which(str(llm.get("claude_bin", "claude")))
    host = str(llm.get("preflight_host", "127.0.0.1"))
    port = int(llm.get("preflight_port", 15721))

    if provider == "auto":
        if claude_bin and preflight(host, port):
            provider = "claude-cli"
        elif os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            provider = "none"
    if provider == "none":
        return build_fallback_report(digest, topic, week_items=week_items, reason="provider-none"), "none"

    schema_path = schema_path or (Path(__file__).resolve().parent / "prompts" / "report.schema.json")
    schema_text = schema_path.read_text(encoding="utf-8") if schema_path.exists() else "{}"
    glossary = glossary or {}
    prompt = build_prompt(digest, topic, glossary, schema_text, week_items=week_items)

    if provider == "anthropic":
        data, error = _anthropic_report(prompt, llm)
        if data is None:
            return build_fallback_report(
                digest, topic, week_items=week_items, reason=f"anthropic-{error}"
            ), f"degraded:anthropic-{error}"
        return _report_or_fallback(data, digest, topic, week_items, "invalid-report")

    if provider != "claude-cli":
        return build_fallback_report(digest, topic, week_items=week_items, reason=f"unknown-provider:{provider}"), f"degraded:unknown-provider"

    if not claude_bin:
        return build_fallback_report(digest, topic, week_items=week_items, reason="claude-cli-not-found"), "degraded:claude-cli-not-found"

    if not preflight(host, port):
        return build_fallback_report(digest, topic, week_items=week_items, reason="cc-switch-unreachable"), "degraded:cc-switch-unreachable"

    args = [
        claude_bin,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        schema_text,
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--max-budget-usd",
        str(llm.get("max_budget_usd", 0.30)),
    ]
    model = str(llm.get("model") or "").strip()
    if model:
        args.extend(["--model", model])

    try:
        proc = subprocess.run(
            args,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=int(llm.get("timeout_seconds", 300)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return build_fallback_report(digest, topic, week_items=week_items, reason=f"claude-run-failed:{exc.__class__.__name__}"), "degraded:claude-run-failed"

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return build_fallback_report(digest, topic, week_items=week_items, reason="empty-llm-output"), "degraded:empty-llm-output"
    if proc.returncode != 0:
        return build_fallback_report(digest, topic, week_items=week_items, reason=f"claude-exit-{proc.returncode}"), "degraded:claude-exit"

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "schema_version" not in data:
        # `--output-format json` wraps the model output in an envelope.
        if isinstance(data.get("structured_output"), dict):
            data = data["structured_output"]
        elif "result" in data:
            result = data.get("result")
            if isinstance(result, str):
                try:
                    data = json.loads(result)
                except json.JSONDecodeError:
                    data = None
            elif isinstance(result, dict):
                data = result
            else:
                data = None
    if not isinstance(data, dict):
        return build_fallback_report(digest, topic, week_items=week_items, reason="invalid-llm-json"), "degraded:invalid-llm-json"

    errors = validate_report_data(data, digest)
    if errors:
        return build_fallback_report(digest, topic, week_items=week_items, reason="llm-contract-violation"), "degraded:llm-contract-violation"

    try:
        return Report.from_dict(data), "ok"
    except ValueError:
        return build_fallback_report(digest, topic, week_items=week_items, reason="invalid-report-shape"), "degraded:invalid-report-shape"
