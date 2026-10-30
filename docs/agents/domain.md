# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout

Single-context repo:

```
/
├── GLOSSARY.md      ← domain vocabulary
├── DECISIONS.md     ← the decision log (this repo's ADRs)
├── docs/plan.md     ← the original build spec
└── src/reviewer/
```

This repo has **no `docs/adr/`**. Decisions live in `DECISIONS.md` as numbered entries (`D1`, `D2`, …), each a short paragraph with its reason (see D9). Where `DECISIONS.md` contradicts `docs/plan.md`, `DECISIONS.md` wins.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root.
- **`DECISIONS.md`** at the repo root: read the entries that touch the area you're about to work in.

If either file doesn't exist, **proceed silently**. Don't flag its absence or suggest creating it upfront.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary lists under _Avoid_ (e.g. say "finding", not "comment"; "specialist", not "agent"; "expected issue", not "label").

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Recording new decisions

When `/domain-modeling` or another skill would write an ADR, append a new numbered entry to `DECISIONS.md` instead, in the same short form as the existing entries.

## Flag decision conflicts

If your output contradicts an existing entry in `DECISIONS.md`, surface it explicitly rather than silently overriding:

> _Contradicts D2 (findings sit on the changed line), but worth reopening because…_
