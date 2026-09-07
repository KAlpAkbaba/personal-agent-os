---
name: feedback-temporal-activity-idempotency
description: In app/research (and any Temporal-backed pipeline here), device-command idempotency keys must be unique per LOGICAL dispatch, not reused for a genuinely new attempt after a failure — reusing a key replays the broker's stored terminal ack instead of dispatching again.
metadata:
  type: feedback
---

`app.broker.service.create_command` dedups strictly on `(device_id, idempotency_key)`: creating
a command with a key that already has a row returns the EXISTING row unchanged, even if its
status is a terminal FAILURE — it never re-dispatches. This is correct for true retries (a
Temporal activity replay of the same logical step should get the same terminal outcome), but
it is a bug magnet whenever code reuses a key across what is actually a NEW dispatch:

- `DeviceBrowserGateway`'s "unknown session -> reopen once and retry" logic must use a
  DIFFERENT idempotency key for the retried command (I append `:session-retry`), not the
  original key, or the retry just replays the original failure forever.
- Session reopen after invalidation uses a per-attempt suffix
  (`session_open:{attempt}`, `self._session_open_attempt` incremented each real dispatch),
  never a fixed key, for the same reason.
- A polling/wait loop that calls the same capability repeatedly (e.g.
  `browser.wait for=verification_cleared` across multiple budget slices) must key each call by
  its own loop iteration (`wait_verification:{iteration}`) — it is not a retry of the same wait,
  it is a new logical wait each time.

**Why:** discovered while implementing M13_RESEARCH_SPEC.md §5a's owner-handoff retry (2026-09-03).
Getting this wrong doesn't fail loudly — it just silently replays a stale outcome and the
feature quietly does nothing on retry, which is much harder to catch than a crash.

**How to apply:** before adding ANY retry/reopen/re-poll path that calls
`DeviceCommandClient.run(...)` a second time for what is conceptually a new attempt, check
whether the idempotency_key differs from the first call. If it doesn't, it's a bug.
[[project-pagentos-overview]]
