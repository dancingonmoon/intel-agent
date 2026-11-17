"""Deep-research mode: multi-source evidence + long-form report.

v1 reuses the daily collectors with a longer window and asks the LLM for a
long Markdown report. The model cites `[[item_id]]`; the renderer replaces each
citation with the original title and URL, so the model never emits links.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import date
from pathlib import Path

from .config import load_glossary
from .contracts import FACET_LABELS, FACETS, Digest
from .llm import complete
from .pipeline import collect

CITATION_RE = re.compile(r"\[\[(it_\d+)\]\]")
DEFAULT_WINDOW_HOURS = 2160  # 90 days


def _slug(text: str, max_length: int = 40) -> str:
    slug = re.sub(r"[^\w一-鿿-]+", "-", text.strip().lower()).strip("-")
    return (slug[:max_length].rstrip("-") or "research")


def _evidence_line(item) -> str:
    return (
        f"- [[{item.item_id}]] {item.title_original} | {item.source} | "
        f"{item.published_at[:10]} | {item.snippet[:300]}"
    )


def _prompt(question: str, digest: Digest, glossary: dict[str, str]) -> str:
    lines = []
    for facet in FACETS:
        items = [item for item in digest.items if item.facets.primary == facet]
        if not items:
            continue
        lines.append(f"\n## {FACET_LABELS[facet]}")
        lines.extend(_evidence_line(item) for item in items[:15])
    glossary_lines = "\n".join(f"- {ru} → {zh}" for ru, zh in glossary.items())
    return f"""你是资深行业研究员。请基于下面给出的证据，回答研究问题。

研究问题：{question}

要求：
1. 结论先行，给出 5 条以内的核心判断，每条标注证据强度。
2. 只能使用证据中出现的 [[item_id]] 作为引用；不要编造 id，不要写 URL。
3. 关键数字、金额、型号、日期、人名至少有两个独立来源；单源必须写「单一来源，待核实」。
4. 区分事实、推断、未证实；冲突证据并列呈现。
5. 按以下结构写 3000–6000 字中文报告：
   结论摘要 / 背景与范围 / 重大合同与项目 / 技术与产品 / 玩家与生态 /
   风险与不确定性 / 时间线 / 来源清单。
6. 来源清单只写 [[item_id]] 和一句话说明，链接由渲染器补全。

术语表：
{glossary_lines or '（无）'}

证据：
{''.join(lines) if lines else '（无）'}
"""


def _fallback(question: str, digest: Digest, status: str) -> str:
    lines = [
        f"# {question}",
        "",
        f"> 自动深度研究不可用（{status}）。以下为证据清单，结论待人工归纳。",
        "",
        "## 结论",
        "",
        "待人工归纳。",
        "",
        "## 证据",
        "",
    ]
    for facet in FACETS:
        items = [item for item in digest.items if item.facets.primary == facet]
        if not items:
            continue
        lines.append(f"### {FACET_LABELS[facet]}")
        lines.append("")
        for item in items[:20]:
            lines.append(f"- [[{item.item_id}]] — {item.source} · {item.published_at[:10]}")
            if item.snippet:
                lines.append(f"  {item.snippet[:300]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _resolve_citations(text: str, digest: Digest) -> str:
    def replace(match: re.Match) -> str:
        item = digest.items_by_id.get(match.group(1))
        if not item:
            return match.group(0)
        return f"[{item.title_original}]({item.canonical_url})"

    return CITATION_RE.sub(replace, text)


def run(
    topic: dict,
    settings: dict,
    question: str,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> Path:
    digest = collect(topic, settings, window_hours=window_hours)
    prompt = _prompt(question, digest, load_glossary())
    text, status = complete(prompt, settings)
    if status != "ok" or not text.strip():
        text = _fallback(question, digest, status)
    text = _resolve_citations(text, digest)

    folder = Path(str(settings["paths"]["reports_dir"])) / str(topic["id"]) / "research"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{date.today().isoformat()}-{_slug(question)}.md"
    out.write_text(text, encoding="utf-8")

    evidence = {
        "question": question,
        "run_id": digest.run_id,
        "window": dataclasses.asdict(digest.window),
        "status": status,
        "items": [dataclasses.asdict(item) for item in digest.items],
    }
    out.with_suffix(".evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out
