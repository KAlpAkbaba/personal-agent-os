---
name: feedback-owner-facing-http-exception-guard
description: A repo-wide AST guard fails the build if any route's HTTPException(detail=...) is built from a caught exception; use app.errors.owner.log_and_detail/owner_detail instead
metadata:
  type: feedback
---

`tests/unit/test_owner_error_language.py::test_no_route_answers_the_owner_with_a_python_exception`
walks every `.py` file under `app/` with an AST visitor: inside any `except X as exc:`
block, an `HTTPException(detail=...)` whose `detail` expression MENTIONS the bound
exception name (`str(exc)`, `f"...{exc}..."`, `f"{type(exc).__name__}: {exc}"`, etc.)
is a hard failure — for every module, including one added the same day. Plain string
literals (`detail="unknown subscription"`) are fine; only expressions referencing the
caught exception variable are flagged.

**Why:** found as the ONE failure in an 11,764-test full API unit-suite run while
adding B11 WebPush's `POST /v1/webpush/subscriptions` route — see
[[project_b11_webpush_status]]. B22 req 705's whole point: the owner must never read a
Python exception's text; it belongs in the log (with a `where` tag), not the response
body.

**How to apply:** in any new route's `except` block that needs to answer with a 4xx,
use `app.errors.owner.log_and_detail(error_class, exc, where="module.function_name")`
(logs the exception under `where`, returns the owner-facing body) or bare
`app.errors.owner.owner_detail(error_class, specific=...)` when there is nothing to
log. Pick `error_class` from `app.errors.catalog`'s existing keys —
`"validation_error"` for malformed input, `"constraint_violation"` for a request that
hit a security/business rule (e.g. an SSRF allowlist refusal), `"not_found"`,
`"lifecycle_violation"`, etc. — never invent new prose inline. Never grep-fix this by
wrapping the f-string in a helper that still contains `exc`; the guard's AST walk
checks whether the exception NAME appears anywhere in the `detail=` expression, not
just for `str(exc)` literally.
