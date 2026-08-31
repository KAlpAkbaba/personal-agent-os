# M0 Security Review — 2026-08-31

Independent review by the security-reviewer agent against git HEAD at M0 completion. Full scope: tracked-secret scan, network exposure, API injection/deserialization/error-leak surface, web client, CI, scripts, ignore/example hygiene.

**Overall: no High/Critical findings.** M0 is infrastructure scaffolding (one read-only health endpoint, no auth or write paths yet).

## Findings and dispositions

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Medium | Quality-gate secret check was filename-only; pasted token in a tracked file would pass | **Fixed at M0**: content-pattern scan (AWS/GitHub/OpenAI/Slack tokens, private-key headers) added to `scripts/quality-gate.ps1` Secret hygiene step. Consider gitleaks/trufflehog in CI when real provider keys start existing (M1+). |
| 2 | Medium | Authorized Asset Registry / security-testing scope gate is spec-only; no enforcement code exists | **Tracked, by design at M0**: enforcement must exist before any milestone ships autonomous security-testing capability. Gate check owner: M8 entry criteria (revisit at M6/M7 when evolution agents gain tools). |
| 3 | Low | No CORSMiddleware/security headers on API | **Deferred deliberately**: current dev is same-host loopback. When web moves cross-origin, add explicit scoped allow-list (never `*`). Noted for M1. |
| 4 | Low | ObjectStore accepted raw keys without validation | **Fixed at M0**: `validate_object_key` (charset, length, traversal rejection) baked into both implementations + unit tests. |
| 5 | Low | `pyproject.toml` uses `>=` specifiers | **Accepted**: builds are hash-pinned via `uv.lock` and CI uses `--frozen`. `uv lock --upgrade` is a deliberate, reviewed action, not routine. |

## Clean areas

Tracked-secret content scan clean (only ADR-0011 dev-only loopback credentials, clearly marked); all compose ports bind 127.0.0.1 only; no raw SQL, no unsafe deserialization, no debug endpoints, error responses leak no internals; single non-secret `NEXT_PUBLIC_*` var in web; CI has no secret usage and actions are version-pinned; scripts have no destructive defaults or elevation; `.gitignore`/`.env.example` consistent.
