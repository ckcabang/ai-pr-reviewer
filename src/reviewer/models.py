"""Data model shared by every stage (terms follow GLOSSARY.md)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Severity = Literal["critical", "high", "medium", "low"]
Reviewer = Literal["correctness", "security", "standards", "baseline"]
Verdict = Literal["keep", "drop"]
Tier = Literal["action_required", "review_recommended", "dropped"]

SEVERITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}
TITLE_MAX = 100


class ReviewInput(BaseModel):
    repo_root: Path
    """Directory containing the repo at HEAD of the change."""
    diff: str
    """Unified diff (base...head), git's standard a/ b/ prefix format."""
    title: str = ""
    description: str = ""
    review_rules_path: Path | None = None
    """Defaults to <repo_root>/REVIEW.md. Not read yet in the walking skeleton."""


class FindingDraft(BaseModel):
    """What a reviewer returns for one finding (the LLM output contract)."""

    model_config = ConfigDict(extra="ignore")

    file: str
    """Repo-relative path."""
    line: int
    """Anchor: NEW-side line number inside the diff that causes the problem (D2)."""
    end_line: int | None = None
    category: str
    """Fine-grained kind, e.g. logic, error_handling, injection, auth, rule."""
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    title: str
    explanation: str
    evidence: str
    """Exact quoted code and/or cited rule text."""
    suggestion: str | None = None
    rule_id: str | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def _lower_severity(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("title")
    @classmethod
    def _trim_title(cls, v: str) -> str:
        v = " ".join(v.split())
        return v if len(v) <= TITLE_MAX else v[: TITLE_MAX - 1].rstrip() + "…"


class Finding(FindingDraft):
    id: str
    """Stable hash of (reviewer, file, line, title)."""
    reviewer: Reviewer

    @classmethod
    def from_draft(cls, draft: FindingDraft, reviewer: Reviewer) -> "Finding":
        return cls(id=finding_id(reviewer, draft.file, draft.line, draft.title), reviewer=reviewer,
                   **draft.model_dump())


class VerifiedFinding(Finding):
    verdict: Verdict = "keep"
    verifier_reason: str = ""
    duplicate_of: str | None = None
    triage: Tier = "dropped"


class ReviewResult(BaseModel):
    findings: list[VerifiedFinding]
    """All findings, including dropped ones (for debugging and eval)."""
    stats: dict
    """Per-stage counts, tokens, cost, latency, cache hits, specialist failures."""

    def in_tier(self, tier: Tier) -> list[VerifiedFinding]:
        return [f for f in self.findings if f.triage == tier]


def finding_id(reviewer: str, file: str, line: int, title: str) -> str:
    return hashlib.sha256(f"{reviewer}|{file}|{line}|{title}".encode()).hexdigest()[:12]
