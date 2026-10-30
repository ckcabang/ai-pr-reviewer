# AI PR Reviewer

A code review pipeline that reviews a Python change with retrieved context, specialist reviewers, a verifier and triage. It is measured against a single-prompt baseline on a labeled set of PRs.

## Reviewing

**Finding**:
A single problem a reviewer reports about a change, tied to a file and line, with evidence.
_Avoid_: comment, issue, bug

**Specialist**:
One of the three focused LLM reviewers in the pipeline: correctness, security or standards.
_Avoid_: agent, sub-reviewer

**Verifier**:
The pipeline stage that skeptically re-checks every finding against the source and decides keep or drop, re-scoring confidence and severity.
_Avoid_: grader, filter

**Triage tier**:
The outcome bucket a finding lands in after verification: action required, review recommended, or dropped.
_Avoid_: priority, level

**Severity**:
How bad a finding's problem would be if real: critical, high, medium or low. Independent of the triage tier.

**Confidence**:
How sure a reviewer is that a finding is real, from 0 to 1.

**Anchor**:
The line a finding is attached to: a new-side line inside the diff that causes the problem, even when the breakage shows up elsewhere.
_Avoid_: location, comment line

**Context bundle**:
The line-numbered code (changed functions, callers, callees, similar code) and rules a specialist sees alongside the diff.
_Avoid_: prompt context, retrieved context

**Changed function**:
A function, method or class whose source overlaps a line the change added or deleted.

## Team rules

**Rule**:
A numbered, team-specific review standard from `REVIEW.md` (e.g. R2: parameterized SQL only).
_Avoid_: guideline, policy

**Don't-flag list**:
The `REVIEW.md` list of things no reviewer may report.
_Avoid_: ignore list, exclusions

## Evaluation

**Arm**:
One reviewer configuration under evaluation: baseline, pipeline, or an ablation.
_Avoid_: variant, mode

**Baseline**:
The single-prompt, diff-only reviewer, used only as the comparison arm.
_Avoid_: control, naive reviewer

**Dataset PR**:
A seeded change to the demo service, with a realistic title and description and a ground-truth list of expected issues.
_Avoid_: test PR, labeled PR, sample

**Expected issue**:
A ground-truth problem planted in a dataset PR.
_Avoid_: label, bug, finding

**Clean PR**:
A dataset PR with zero expected issues.

**Area**:
Which specialist's territory an expected issue belongs to: correctness, security or standards.
_Avoid_: category (that word is a finding's fine-grained kind)

**Required context**:
The minimum context a reviewer needs to catch an expected issue: the diff alone, cross-file code, or the team rules.

**Judge**:
The LLM that decides which findings match which expected issues, one-to-one.
_Avoid_: scorer, grader

**Run**:
One full pass of an arm over the dataset; metrics are reported as mean ± std over runs.
