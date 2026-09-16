---
name: feedback-health-check-status-field-required
description: Every entry added to /v1/system/health's checks dict MUST carry a "status" key or app.health.is_degraded silently marks the whole process degraded
metadata:
  type: feedback
---

`app/health.py::is_degraded` treats ANY check dict with no `"status"` key as unhealthy
(`entry.get("status") in HEALTHY_STATUSES` is False for a missing key), unless the
check name is in `ADVISORY_CHECKS` or the dict carries `"required": False`. A brand new
check added to `app/main.py`'s `system_health()` with fields like
`{"configured": bool, ...}` and no `"status"` therefore makes `/v1/system/health`
report `degraded` for the WHOLE process the instant that check exists — in every test
and every fresh deployment, since a not-yet-configured feature is exactly the common
case.

**Why:** found via `tests/unit/test_health_endpoint.py` (4 failures at once:
`test_health_ok_shape`, `test_redis_down_is_reported_but_degrades_nothing`,
`test_health_temporal_worker_skipped_when_worker_mode_off`,
`test_the_body_opens_the_way_the_reconcile_reads_it`) while adding the B11 WebPush
`"webpush"` check — see [[project_b11_webpush_status]]. The test file's `ALL_CHECKS`
set must also be updated with the new check's name, or `test_health_ok_shape`'s
`set(body["checks"].keys()) == ALL_CHECKS` fails independently.

**How to apply:** any new health-check dict for an owner-provisioned-but-optional
credential/provider (mirrors `research`/`voice_realtime`/`mobile`'s own posture) should
report `"status": "ok"` even when unconfigured — "not configured yet" is an owner
action pending, not a degraded PROCESS. Give it `"latency_ms": 0.0` too if it does no
I/O, matching the uniform check shape (`app.memory.runtime.health_check` is the
reference example). Only use `"status": "fail"` for an actual failure of something this
process is supposed to be doing right now, and add the check name to
`ADVISORY_CHECKS` only if a real failure of it should never fail the whole gate.
