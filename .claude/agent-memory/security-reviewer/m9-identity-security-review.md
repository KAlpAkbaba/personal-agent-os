---
name: m9-identity-security-review
description: Summary + pointer to full findings for the M9 owner identity/API authentication (ADR-0027), mobile push/announcer, and web sign-in review (2026-09-01)
metadata:
  type: project
---

M9 built `services/api/app/identity/**` (bearer sessions, hash-only + constant-time,
TTL/idle timeout, refresh rotation, host-side recovery CLI) and applied
`require_owner_session` across every module that M4-M8 left as standing hard-gate
debt (memory, voice/narration, self-healing, evolution, security, broker). Full
findings are in the review response delivered 2026-09-01 (not written to a repo
file per this agent's own no-report-files rule) — ask to re-derive if needed, or
check `docs/reviews/` if the team later adds an `M9_SECURITY_REVIEW.md`.

No Critical/High. Two Medium, three Low:

1. **Medium** — `app/mobile/announcer.py::_claim()` commits `tasks.announced_at`
   BEFORE `sweep_once()` calls the notifier. A crash between the two permanently
   drops that task's push notification, contradicting the migration 0010
   docstring's explicit claim ("an API restart mid-delivery retries rather than
   loses it... exactly-once per task"). Fix: stamp only after successful
   delivery, or use a claimed_at lease + reclaim sweep.
2. **Medium** — `require_scope()` (ADR-0027 judgement call #3, "scopes narrow,
   never elevate") is implemented and unit-tested but wired into ZERO shipped
   routes — every module gates on bare `require_owner_session`. A scoped session
   has full owner authority in practice. Same defect class as the M8
   `RegistryAuthorizationProvider`-never-wired finding: [[m8-security-agent-review]].
3. **Low** — `_require_loopback` (duplicated in `identity/routes.py` and
   `broker/routes.py`) fails OPEN when `request.client is None` instead of
   refusing an unknown peer. Not reachable under the current uvicorn/TCP deploy,
   but worth hardening (`if host not in _LOOPBACK_HOSTS: raise`, no `is not None`
   guard).
4. **Low** — Device revocation and its session revocation are two independent DB
   transactions (`broker/routes.py::revoke_device`) with no reconciliation sweep
   if a crash/exception lands between them.
5. **Low/informational** — a corrupt (not missing) identity-root file correctly
   fails closed (by design, tested) but surfaces as an unhandled 500 on
   bootstrap/session-exchange rather than a clean 401 — operability nit only,
   `require_owner_session`'s hot path never re-reads the root file so live
   sessions are unaffected.

Verdicts: no auth bypass found; the 5-endpoint unauthenticated surface (health,
bootstrap, sessions POST, devices/enroll POST, devices/connect WS) is exactly as
documented and enforced by a route-table sweep test that walks the live
FastAPI dependency graph (ran it — 134+47 tests green); device revocation
reliably kills sessions via its one code path; M4-M8's hard gate is genuinely
closed for authenticate-or-refuse, but the finer `require_scope` narrowing the
ADR describes is not yet load-bearing (finding #2).

Reconfirms two standing lessons for this project: (i) a component that is built
and unit-tested but never wired into the real routes/runtime changes nothing —
seen now in M8 (authorization provider) and M9 (require_scope); (ii) a
docstring's stated durability/atomicity guarantee is a claim to verify against
the actual commit ordering, not to trust — see [[m6-selfhealing-security-review]]
for the sibling lesson about trusting requester-supplied evidence.
