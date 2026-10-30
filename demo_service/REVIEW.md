# Review rules

## R1: Authenticated ownership
Every endpoint that reads or mutates user-owned data must depend on `get_current_user` and verify ownership via `auth.require_owner`.

## R2: Parameterized SQL only
Use parameterized SQL only, via `db.execute(sql, params)`. No f-strings, `%` or `+` in SQL.

## R3: Money is Decimal
Money is `Decimal` (dollars) in services; never `float`.

## R4: External calls go through clients
External calls go through `clients/` wrappers (timeouts, retries). No raw `requests` or `httpx` in routes or services.

## R5: Idempotent capture and refund
Capture and refund endpoints require an `Idempotency-Key` header and call `idempotency.check_and_store`.

## R6: Errors are logged, not returned
Errors: `logger.exception(...)` plus a generic message. Never return exception text to clients.

## R7: No sensitive data in logs
Never log passwords, tokens, secrets or full card numbers. Use the redaction helper.

## R8: Secrets come from settings
Secrets and config come from `settings`. No hardcoded credentials.

## R9: Order state via transition
Order state changes go only through `order_state.transition()`.

# Do not flag
- Naming, formatting, import order, docstrings, missing type hints
- "Consider adding logging/tests" suggestions
- Performance micro-optimizations outside hot paths
- Issues in unchanged code
