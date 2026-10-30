"""Output: Markdown and JSON renderings of a ReviewResult."""
from __future__ import annotations

import json
import re

from reviewer.models import ReviewResult, VerifiedFinding

_BADGE = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW"}


def render_json(result: ReviewResult) -> str:
    """The full review result, dropped findings included."""
    return json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)


def render_markdown(result: ReviewResult) -> str:
    action = result.in_tier("action_required")
    recommended = result.in_tier("review_recommended")
    out = [f"# AI Review: {len(action)} action required · {len(recommended)} recommended", ""]
    if not action and not recommended:
        out += ["No findings. Nothing in this change needs attention.", ""]
    if action:
        out += ["## Action required", ""]
        out += [_finding_md(f) for f in action]
    if recommended:
        out += ["## Review recommended", ""]
        out += [_finding_md(f) for f in recommended]
    out.append(_stats_md(result.stats))
    return "\n".join(out).rstrip() + "\n"


def _finding_md(f: VerifiedFinding) -> str:
    lines = [f"### `{f.file}:{f.line}` [{_BADGE[f.severity]}] {f.title}", "", f.explanation, ""]
    lines += [_code_block(f.evidence), ""]
    if f.rule_id:
        lines += [f"Rule: {f.rule_id}", ""]
    if f.suggestion:
        lines += [f"Suggestion: {f.suggestion}", ""]
    lines.append(f"_{f.reviewer} · confidence {f.confidence:.2f} · {f.category}_")
    lines.append("")
    return "\n".join(lines)


def _code_block(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def _stage_lines(stages: dict, counts: dict) -> list[str]:
    out = []
    v = stages.get("verifier")
    if v and v["status"] == "skipped":
        out.append("- Verifier: skipped")
    elif v:
        tag = " (cached)" if v.get("cached") else ""
        note = "" if v["status"] == "ok" else f" — {v['status'].upper()}: findings kept unverified"
        out.append(f"- Verifier: {v['findings_in']} in → {counts.get('after_verifier', 0)} kept, "
                   f"{v['wall_s']:.1f}s{tag}{note}")
    t = stages.get("triage")
    if t:
        removed = t.get("cap_removed", 0)
        out.append(f"- Triage: {counts.get('after_verifier', 0)} → {counts.get('after_triage', 0)} kept"
                   + (f" ({removed} removed by the cap)" if removed else ""))
    return out


def _context_lines(c: dict | None) -> list[str]:
    if not c or not c.get("enabled"):
        return ["- Context: off (diff only)"] if c else []
    omitted = f", {c['omitted_blocks']} left out for the budget" if c.get("omitted_blocks") else ""
    return [f"- Context: {c.get('changed_functions', 0)} changed, {c.get('callers', 0)} callers, "
            f"{c.get('callees', 0)} callees, {c.get('rules', 0)} rules ({c.get('chars', 0)} chars{omitted})"]


def _stats_md(stats: dict) -> str:
    totals = stats.get("totals", {})
    counts = stats.get("counts", {})
    cached = " (cached)" if totals.get("cached") else ""
    lines = [
        "<details>",
        "<summary>Review stats</summary>",
        "",
        f"- Files reviewed: {len(stats.get('files_reviewed', []))}",
        *_context_lines(stats.get("context")),
        f"- Findings: {counts.get('raw', 0)} raw → {counts.get('after_triage', 0)} kept",
        f"- Tokens: {totals.get('input_tokens', 0)} in / {totals.get('output_tokens', 0)} out",
        f"- Estimated cost: ${totals.get('cost_usd', 0.0):.4f}",
        f"- Latency: {totals.get('wall_s', 0.0):.1f}s{cached}",
    ]
    lines += _stage_lines(stats.get("stages", {}), counts)
    for name, s in stats.get("specialists", {}).items():
        if s["status"] == "failed":
            lines.append(f"- Specialist {name}: FAILED ({s['error']})")
        else:
            tag = " (cached)" if s.get("cached") else ""
            lines.append(
                f"- Specialist {name}: {s['raw_findings']} raw → {s['kept_findings']} kept, "
                f"{s['wall_s']:.1f}s{tag}"
            )
    lines += ["", "</details>"]
    return "\n".join(lines)


def render_inline_comment(f: VerifiedFinding) -> str:
    """Body of an inline PR comment for one finding (the file:line heading doubles as a label)."""
    return _finding_md(f).strip()


def render_review_body(result: ReviewResult, inline_ids: set[str]) -> str:
    """Summary body of a posted PR review. Findings whose id is in `inline_ids` were posted
    as inline comments and are only listed; every other finding is spelled out in full."""
    action = result.in_tier("action_required")
    recommended = result.in_tier("review_recommended")
    out = [f"# AI Review: {len(action)} action required · {len(recommended)} recommended", ""]
    if not action and not recommended:
        out += ["No findings. Nothing in this change needs attention.", ""]
    inline = [f for f in action + recommended if f.id in inline_ids]
    rest = [f for f in action + recommended if f.id not in inline_ids]
    if inline:
        out += ["Posted as inline comments:", ""]
        out += [f"- `{f.file}:{f.line}` [{_BADGE[f.severity]}] {f.title}" for f in inline]
        out.append("")
    if rest:
        out += ["## Findings not on a changed line", ""]
        out += [_finding_md(f) for f in rest]
    out.append(_stats_md(result.stats))
    return "\n".join(out).rstrip() + "\n"
