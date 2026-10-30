"""Triage: deterministic code, no LLM. Verified findings in, tiered and sorted findings out."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from reviewer.config import PipelineConfig
from reviewer.models import SEVERITY_ORDER, VerifiedFinding

NEAR_DUPLICATE_LINES = 3
NEAR_DUPLICATE_SIMILARITY = 0.25
"""Minimum word overlap (Jaccard, title + explanation) for two nearby findings to be the same problem."""
LOW_SEVERITY_MIN_CONFIDENCE = 0.9


@dataclass
class TriageResult:
    findings: list[VerifiedFinding]
    """Kept findings first (action required, then review recommended, sorted), then dropped."""
    cap_removed: int = 0
    dropped_by_reason: dict[str, int] = field(default_factory=dict)
    """Reason key -> count, for every drop except the cap (see `cap_removed`)."""


def triage(findings: list[VerifiedFinding], config: PipelineConfig) -> TriageResult:
    dropped: list[VerifiedFinding] = []
    reasons: dict[str, int] = {}

    def drop(f: VerifiedFinding, key: str, text: str, *, count: bool = True) -> None:
        dropped.append(f.model_copy(update={"triage": "dropped", "triage_reason": text}))
        if count:
            reasons[key] = reasons.get(key, 0) + 1

    # 1. verifier drops, low confidence, flagged duplicates
    survivors: list[VerifiedFinding] = []
    for f in findings:
        if f.verdict == "drop":
            drop(f, "verifier", f"dropped by verifier: {f.verifier_reason}".rstrip(": "))
        elif f.confidence < config.confidence_threshold:
            drop(f, "below_threshold",
                 f"confidence {f.confidence:.2f} below threshold {config.confidence_threshold:.2f}")
        elif f.duplicate_of:
            drop(f, "duplicate", f"duplicate of {f.duplicate_of}")
        else:
            survivors.append(f)

    # 2. near-duplicates: same file and category, lines within +-3 and about the same problem
    #    (similar text); highest confidence wins. Closeness alone is not enough (D46).
    kept: list[VerifiedFinding] = []
    words = {f.id: _words(f) for f in survivors}
    for f in sorted(survivors, key=lambda f: (-f.confidence, *_rank(f))):
        twin = next((k for k in kept if k.file == f.file and k.category == f.category
                     and abs(k.line - f.line) <= NEAR_DUPLICATE_LINES
                     and _similarity(words[k.id], words[f.id]) >= NEAR_DUPLICATE_SIMILARITY), None)
        if twin:
            drop(f, "near_duplicate", f"near-duplicate of {twin.id} (same file and category, "
                                      f"within {NEAR_DUPLICATE_LINES} lines, similar text)")
        else:
            kept.append(f)

    # 3. low severity needs high confidence; then tier
    tiered: list[VerifiedFinding] = []
    action_rule = config.action_required
    for f in kept:
        if f.severity == "low" and f.confidence < LOW_SEVERITY_MIN_CONFIDENCE:
            drop(f, "low_severity", f"low severity with confidence {f.confidence:.2f} "
                                    f"< {LOW_SEVERITY_MIN_CONFIDENCE}")
            continue
        action = action_rule.applies(f.severity, f.confidence)
        tiered.append(f.model_copy(update={
            "triage": "action_required" if action else "review_recommended", "triage_reason": ""}))

    # 4. sort by tier, severity, confidence; cap
    tiered.sort(key=lambda f: (f.triage != "action_required", *_rank(f)))
    cap = config.max_findings_total
    for f in tiered[cap:]:
        drop(f, "cap", f"over the cap of {cap} findings", count=False)
    return TriageResult(findings=tiered[:cap] + dropped, cap_removed=max(0, len(tiered) - cap),
                        dropped_by_reason=reasons)


def _rank(f: VerifiedFinding) -> tuple:
    """Severity, then confidence (descending), then a stable tiebreak."""
    return (SEVERITY_ORDER[f.severity], -f.confidence, f.file, f.line, f.id)


_STOPWORDS = frozenset("""the and for are but not you all any can has have had was were will with without this that
these those from into onto than then them they their there its what when where which who why how also may might
must should would could does did been being such only other more most some one two""".split())


def _words(f: VerifiedFinding) -> frozenset[str]:
    """The content words of a finding's title and explanation (lowercase, no stopwords, with a
    crude plural/-ing/-ed strip). Evidence is left out: two findings on one line quote the same code."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", f"{f.title} {f.explanation}".lower()):
        if len(w) > 2 and w not in _STOPWORDS:
            out.add(re.sub(r"(ing|ed|es|s)$", "", w) if len(w) > 4 else w)
    return frozenset(out)


def _similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard overlap of two word sets (0 when both are empty)."""
    return len(a & b) / len(a | b) if a | b else 0.0
