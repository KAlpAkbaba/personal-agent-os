---
name: redaction-not-automatic
description: app/security/redaction.py is this repo's canonical secret-redaction utility, but it is opt-in per call site, not enforced framework-wide -- confirmed missed once already in app/selfmodel/indexer.py.
metadata:
  type: project
---

`app/security/redaction.py` (`find_secret`, `contains_secret`, `redact_text`, `redact_value`,
`assert_redacted`) is the shared, name-and-shape-based secret detector used by
`app/security/checks.py`, `assessments.py`, `registry.py`, and `artifacts.py`. It is NOT
automatically applied anywhere else in the codebase -- each subsystem that persists or
returns source-derived text has to import and call it itself.

Confirmed gap (2026-09-10 review, uncommitted diff at the time): `app/selfmodel/indexer.py`
added a new path that writes module-level `NAME = "value"` string constants into
`CodeSymbol.signature` (`_string_constants` ~L559, `_constant_signature` ~L825) and returns
them verbatim over `GET /v1/selfmodel/search` and `/v1/selfmodel/modules/{key}`
(`app/selfmodel/query.py` `search()`/`module_status()`). The file already has its OWN
narrower redaction (`_SECRET_PARAM_RE` / `_redact_secret_defaults`, ~L494-521) for function
*parameter defaults*, but the new constant path bypasses both that and
`app.security.redaction` entirely -- zero name-based filtering, only a length cap. A
`DEFAULT_TOKEN = "sk-live-..."` module constant would round-trip in cleartext through the
API. See `docs/DECISIONS.md` / ADR-0111 diff for the feature that introduced this; the
finding was reported but the fix (reuse `_SECRET_PARAM_RE` or `app.security.redaction` on
the constant NAME before its value is embedded) was not yet applied as of the review.

**Why:** two independent, narrower redaction implementations already coexist in this repo
(`app/security/redaction.py`'s config-shaped patterns vs. `indexer.py`'s parameter-name
regex) and neither is wired to the other, so a new write path can easily fall through both.

**How to apply:** on every future review, grep any new code path that persists or serves
source-derived strings (constants, defaults, env dumps, config blobs, docstrings) for a call
into `app.security.redaction` or an equivalent name/shape check. Absence is not proof of
safety -- confirm by reading the actual write site, not by assuming redaction is ambient.
