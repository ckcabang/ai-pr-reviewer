"""The standards specialist: violations of a specific numbered team rule (`REVIEW.md`).

It only works from `<team_rules>`; without rules there is nothing to check, so the pipeline
skips it (`needs_rules`). Its findings must cite a rule that exists (`run_specialist` drops
the rest) and quote the rule's text in `evidence`.
"""
from reviewer.agents.base import SpecialistSpec

STANDARDS = SpecialistSpec(
    name="standards",
    role="team standards specialist",
    flag=[
        "Violations of a specific numbered rule in `<team_rules>`: the changed code does what the rule's text forbids, or omits what it requires.",
    ],
    do_not_flag=[
        "Anything that no numbered rule in `<team_rules>` covers. General best practices belong to the other specialists.",
        "A rule the change does not touch, or a rule stretched to cover a neighbouring concern.",
        "Rule violations in code the change does not touch.",
        "Test code, test fixtures, seed data and setup scripts, unless the rule's own text says it covers them: team rules are written for application (production) code.",
    ],
    extra_rules=[
        "Set `rule_id` to the id of the violated rule exactly as it appears in `<team_rules>` (for example \"R3\"). A problem you cannot tie to one numbered rule is not a standards finding: do not report it.",
        "`evidence` must first quote that rule's text exactly as written in `<team_rules>`, then the offending code from the diff.",
        "Report a violation only when the changed code breaks what the rule's text actually says.",
        "Team rules govern application (production) code. Do not apply a rule to test code (test files, fixtures, conftest, seed or setup data) merely because the pattern the rule forbids appears there: a rule about how production code must behave is not a rule about how a test prepares its own data. Flag test code only when the rule's own text explicitly covers tests.",
        "If there is no `<team_rules>` section, return {\"findings\": []}.",
    ],
    needs_rules=True,
)
