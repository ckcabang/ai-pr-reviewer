"""Load dataset PRs from `evals/dataset/prs/<id>_<slug>/{meta.yaml, change.patch, labels.yaml}`."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DATASET_ROOT = Path(__file__).parent / "prs"

AREAS = ("correctness", "security", "standards")  # D4
SEVERITIES = ("critical", "high", "medium", "low")
REQUIRES = ("diff", "rules", "cross_file")  # D5: the minimum context needed to catch the issue


class DatasetError(ValueError):
    """A dataset PR is malformed or its patch cannot be applied."""


@dataclass(frozen=True)
class Location:
    file: str
    line_hint: int


@dataclass(frozen=True)
class ExpectedIssue:
    id: str
    file: str
    line_hint: int  # approximate new-side line (the anchor, D2)
    area: str
    severity: str
    requires: str
    description: str
    rule_id: str | None = None
    related: tuple[Location, ...] = ()  # other places where a match counts (D2)


@dataclass(frozen=True)
class DatasetPR:
    id: str  # "pr09"
    title: str
    description: str  # hint-free, as a developer would write it
    patch: str  # unified diff, paths relative to demo_service/
    expected_issues: tuple[ExpectedIssue, ...]
    path: Path

    @property
    def is_clean(self) -> bool:
        return not self.expected_issues


def _choice(value, allowed, field, where):
    if value not in allowed:
        raise DatasetError(f"{where}: {field} must be one of {', '.join(allowed)} (got {value!r})")
    return value


def _line(value, field, where):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DatasetError(f"{where}: {field} must be a positive integer (got {value!r})")
    return value


def _text(mapping, key, where):
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DatasetError(f"{where}: {key} is required")
    return value.strip()


def _parse_issue(raw, pr_id: str, where: str) -> ExpectedIssue:
    if not isinstance(raw, dict):
        raise DatasetError(f"{where}: each issue must be a mapping")
    issue_id = _text(raw, "id", where)
    where = f"{where} {issue_id}"
    if not issue_id.startswith(f"{pr_id}-"):
        raise DatasetError(f"{where}: id must start with '{pr_id}-'")
    rule_id = raw.get("rule_id")
    if rule_id is not None and not re.fullmatch(r"R\d+", str(rule_id)):
        raise DatasetError(f"{where}: rule_id must look like R3 (got {rule_id!r})")
    related = tuple(
        Location(_text(r, "file", where), _line(r.get("line_hint"), "related line_hint", where))
        for r in raw.get("related") or []
    )
    return ExpectedIssue(
        id=issue_id,
        file=_text(raw, "file", where),
        line_hint=_line(raw.get("line_hint"), "line_hint", where),
        area=_choice(raw.get("area"), AREAS, "area", where),
        severity=_choice(raw.get("severity"), SEVERITIES, "severity", where),
        requires=_choice(raw.get("requires"), REQUIRES, "requires", where),
        description=_text(raw, "description", where),
        rule_id=str(rule_id) if rule_id else None,
        related=related,
    )


def _read_yaml(path: Path):
    if not path.is_file():
        raise DatasetError(f"{path}: missing")
    return yaml.safe_load(path.read_text()) or {}


def _load_dir(d: Path) -> DatasetPR:
    meta = _read_yaml(d / "meta.yaml")
    labels = _read_yaml(d / "labels.yaml")
    patch_file = d / "change.patch"
    if not patch_file.is_file():
        raise DatasetError(f"{patch_file}: missing")
    pr_id = _text(meta, "id", str(d / "meta.yaml"))
    if d.name != pr_id and not d.name.startswith(f"{pr_id}_"):
        raise DatasetError(f"{d}: meta id {pr_id!r} does not match the directory name")
    issues = tuple(_parse_issue(r, pr_id, f"{pr_id} labels") for r in labels.get("issues") or [])
    ids = [i.id for i in issues]
    if len(set(ids)) != len(ids):
        raise DatasetError(f"{pr_id}: duplicate issue ids in labels.yaml")
    return DatasetPR(
        id=pr_id,
        title=_text(meta, "title", f"{pr_id} meta"),
        description=_text(meta, "description", f"{pr_id} meta"),
        patch=patch_file.read_text(),
        expected_issues=issues,
        path=d,
    )


def load_all(root: Path = DATASET_ROOT) -> list[DatasetPR]:
    """Every dataset PR under `root`, sorted by id."""
    prs = [_load_dir(d) for d in sorted(Path(root).iterdir()) if d.is_dir()]
    return sorted(prs, key=lambda p: p.id)


def load_pr(pr_id: str, root: Path = DATASET_ROOT) -> DatasetPR:
    """One dataset PR by id ("pr09") or directory name ("pr09_order_totals_in_cents")."""
    for d in sorted(Path(root).iterdir()):
        if d.is_dir() and (d.name == pr_id or d.name.startswith(f"{pr_id}_")):
            return _load_dir(d)
    raise DatasetError(f"no dataset PR {pr_id!r} under {root}")
