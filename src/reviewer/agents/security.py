"""The security specialist: vulnerabilities the change introduces."""
from reviewer.agents.base import SpecialistSpec

SECURITY = SpecialistSpec(
    name="security",
    role="security specialist",
    flag=[
        "Injection: SQL built from strings, command injection, path traversal through user-supplied names.",
        "Missing authentication, authorization or ownership checks on endpoints that change or read user data.",
        "Secrets in code: hardcoded keys, tokens or passwords.",
        "Sensitive data in logs or responses: passwords, tokens, card numbers, exception text returned to clients.",
        "Missing webhook signature checks, or signatures compared without a constant-time compare.",
        "Unsafe deserialization, SSRF and open redirects.",
    ],
    do_not_flag=[
        "Theoretical risks that need unlikely preconditions.",
        "Defense-in-depth suggestions when the primary defense already exists.",
        "\"Use library X\" suggestions.",
    ],
)
