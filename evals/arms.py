"""Arms: the reviewer configurations the eval compares (glossary: Arm).

An arm is a name plus a function with the same signature as `reviewer.pipeline.review`
(Seam 1): `(review_input, *, provider, config) -> ReviewResult`. So `review` itself is a valid
arm, and an ablation is a function that calls `review` with a modified copy of `config`.

To add an arm, register it in `ARMS`:

    ARMS["pipeline_no_verifier"] = Arm("pipeline_no_verifier", my_ablation_fn, "...")          # #14

`pipeline` (registered below) is `reviewer.pipeline.review` itself, the CLI's entry point (#12).

Whatever an arm returns is scored as follows: findings whose triage is `dropped` are kept in
the results file for debugging but are not judged; everything else is.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from reviewer.agents.baseline import review_baseline
from reviewer.config import Config
from reviewer.llm import Provider
from reviewer.models import ReviewInput, ReviewResult
from reviewer.pipeline import review

ArmFn = Callable[..., ReviewResult]
"""Called as `fn(review_input, provider=provider, config=config)`."""


class UnknownArm(ValueError):
    """An arm name that is not registered."""


@dataclass(frozen=True)
class Arm:
    name: str
    review: ArmFn
    description: str = ""

    def run(self, review_input: ReviewInput, *, provider: Provider, config: Config) -> ReviewResult:
        return self.review(review_input, provider=provider, config=config)


ARMS: dict[str, Arm] = {
    "baseline": Arm("baseline", review_baseline, "one diff-only prompt, merged checklist, scored raw (D6, D7)"),
    # The product itself: the same entry point the CLI calls (Seam 1), so the eval measures what ships.
    "pipeline": Arm("pipeline", review, "context + three specialists + verifier + triage; non-dropped findings scored"),
}


def resolve(names: Sequence[str], registry: Mapping[str, Arm] | None = None) -> list[Arm]:
    """The arms for `names`, in order. Unknown names raise `UnknownArm` listing the known ones."""
    registry = ARMS if registry is None else registry
    missing = [n for n in names if n not in registry]
    if missing:
        raise UnknownArm(f"unknown arm(s): {', '.join(missing)} (known: {', '.join(registry)})")
    if len(set(names)) != len(names):
        raise UnknownArm(f"duplicate arm(s) in {', '.join(names)}")
    return [registry[n] for n in names]
