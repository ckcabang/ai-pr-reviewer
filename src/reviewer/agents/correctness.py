"""The correctness specialist: logic and behavioural bugs introduced by the change."""
from reviewer.agents.base import SpecialistSpec

CORRECTNESS = SpecialistSpec(
    name="correctness",
    role="correctness and bug-finding specialist",
    flag=[
        "Logic errors: wrong conditions, off-by-one errors, inverted checks.",
        "Broken callers: a signature or return-type/unit change that code calling it would not survive.",
        "Unhandled error paths that return success, swallowed exceptions that hide failures.",
        "Race conditions and check-then-act sequences that are not atomic.",
        "State-machine bypass: changing state without going through the allowed transitions.",
        "Money and precision errors: floats for money, rounding, units (dollars vs cents).",
        "Resource leaks: connections, files or locks that are not released on every path.",
        "Safeguards removed by the change (a deleted check, validation or guard) that the code still depends on.",
    ],
    do_not_flag=[
        "Style, naming, formatting, import order.",
        "Missing docstrings or type hints.",
        "\"Consider adding tests\" or \"consider adding logging\" suggestions.",
        "Performance micro-optimizations.",
        "Speculative issues that need code you were not shown.",
        "Problems in code the change does not touch.",
    ],
)
