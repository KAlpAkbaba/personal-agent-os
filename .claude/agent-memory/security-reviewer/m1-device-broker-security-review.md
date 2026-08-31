---
name: m1-device-broker-security-review
description: Summary of the M1 device broker + Windows agent security review findings (2026-08-31) and where the writeup lives
metadata:
  type: project
---

Reviewed M1 additions (services/api/app/broker, devices/windows-agent, packages/protocol) at git HEAD
(commit 7a17578, after "chore(m0): close M0 milestone"). Full findings were returned directly in the review
response, not saved as a repo file — this memory is the pointer/summary so future reviews don't re-derive
the same ground.

**No Critical/High findings.** Design quality is strong: single-use token consumption via atomic
`UPDATE ... WHERE used_at IS NULL` (no TOCTOU), indistinguishable auth rejection actually tested
(`test_wrong_signature_rejected_identically`, `test_unknown_device_rejected_identically` in
`services/api/tests/integration/test_broker_ws.py`), command idempotency dedup via DB unique constraint +
IntegrityError fallback, monotonic ack state machine (`services/api/app/broker/state.py`), agent-side 1 MiB
inbound frame cap, `_require_loopback` correctly checks `request.client.host` (TCP peer, not a spoofable
header).

Key findings worth remembering:
1. **[[project-m1-pipe-identity-gap]]** (Medium, forward-looking) — named-pipe ACL/naming assumes Device
   Service and Session Companion share one Windows identity; breaks or invites ACL-loosening once the
   Service installs as a real Windows Service (SYSTEM/Session 0) per the documented architecture.
2. (Low) No `ws_max_size` configured for the broker's WebSocket endpoint — uvicorn's websockets default is
   16 MiB, applies even pre-auth (hello frame), vs. the agent's own 1 MiB self-imposed cap. Recommend
   capping it explicitly (e.g. 64 KiB) once the endpoint is anything other than loopback/Tailscale-only.
3. (Low) `CreateCommandRequest.payload` (services/api/app/broker/routes.py) has no size bound — unbounded
   JSON body accepted into a DB row. Low risk today (dev REST surface is loopback-only/unauthenticated by
   documented design, tracked for owner-auth in a later milestone same as M0 finding #3), but should get a
   cap (mirroring the agent's 1 MiB frame limit) before any non-loopback exposure.
4. (Low) `TraceIdMiddleware` (services/api/app/middleware.py) accepts a client-supplied `X-Trace-Id` header
   with no length/charset validation, and it flows straight into `DeviceCommand.trace_id` /
   `AuditEvent.trace_id` (`String(128)` columns) — an oversized header can 500 the request. Truncate/validate
   in the middleware.

Disposition style to match (see `docs/reviews/M0_SECURITY_REVIEW.md`): a findings table with Severity /
Finding / Disposition, plus a "Clean areas" paragraph. Keep that format for the M1 writeup if asked to
produce `docs/reviews/M1_SECURITY_REVIEW.md`.
