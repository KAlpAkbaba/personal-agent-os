---
name: project-pagentos-m9
description: M9 (Native Mobile + owner identity/ADR-0027) verification outcome and the dev-Postgres connection-exhaustion infra defect found while chasing the integration flake
metadata:
  type: project
---

M9 verified CLOSEABLE at HEAD 610f8c4 (2026-09-01). Owner identity (ADR-0027) and
the mobile surface are solid: 1323 unit + 37/37 identity+mobile integration tests
pass repeatedly; live adversarial testing against a real running server (fresh
bootstrap, real device enrollment via `tests/integration/broker_agent.py`'s
`rest_enroll`, real device revocation killing a bound session across every
module, hash-only token storage confirmed by direct `owner_sessions` inspection,
zero token leakage in logs, no `dependency_overrides`/bypass flags in shipped
`app/`, header-spoofing/path-traversal/SQL-ish-token bypass attempts all refused)
all held up. Full live browser test (build -> serve on 127.0.0.1:3100 -> sign in
with a bootstrapped credential -> authenticated Turkish inbox renders -> PDF
download returns real 200 bytes) also passed. See [[pagentos-project-state]] for
the broader milestone timeline.

**Proven outright** (real HTTP/DB, not the reference client): bootstrap/session
exchange/revocation, device enrollment+revocation kills bound sessions
everywhere, hash-only storage, coarse-401-precise-audit split, route-table sweep
completeness (4 open HTTP + 1 open WS = the 5 documented unauthenticated
surfaces), web sign-in + authenticated render download in a real browser.

**Proven-by-proxy** (headless `clients/reference/mobile_client.py` standing in
for a native app, which the client's own docstring is honest about): "push
notification for artifact ready" — proven as an in-process delivery-log record
the client polls, not real FCM/APNs delivery; "microphone/realtime voice works
under normal mobile lifecycle" — proven only as a control-plane state machine
(`app/mobile/lifecycle.py`, tested with "no audio, no timers, no I/O" by the
test file's own docstring), not real mic/OS audio-session behavior. Both
honestly documented as such by the project already; the M9 return should keep
naming physical-device push/mic verification as a batched owner action, not
claim it.

## Standing defect: dev-Postgres connection exhaustion under subprocess-heavy E2E tests

Independently reproduced, quantified, and root-caused — **not** the M9 identity
code, and only partly explained by a second live process (a coordinator
mid-session floated "a live dev server's announcer racing the tests" as the
sole cause; I disproved that as the *sole* cause by reproducing the same "too
many clients already" failure on a **fully solo run** — my dev server confirmed
killed via `Get-CimInstance Win32_Process`/`taskkill //PID` first — going from a
10-connection baseline to hitting the ceiling and back down to only ~25-30
afterward, i.e. a real per-run leak, not just contention).

Mechanism: `tests/integration/test_research_artifact.py::_spawn_worker` (and the
analogous helpers in `test_selfhealing_e2e.py`, `test_evolution_e2e.py`,
`test_security_e2e.py`) launch real `python -m app.worker`/pipeline subprocesses
and tear them down with `Process.kill()`. Each subprocess opens its own
SQLAlchemy pool against the shared dev Postgres (`max_connections=100`); a hard
kill doesn't run the subprocess's own cleanup, so those backends linger as idle
connections until Postgres's TCP-keepalive default reaps them — which is slow
enough that back-to-back runs (or even one run with several such tests) can
transiently exceed 100 and now-idle connections accumulate run over run. The
`test_mobile_push.py` module-scoped `client` fixture already has a docstring
that names `max_connections` as a known-fragile shared resource and works
around it for that one file — the fix hasn't been generalized to the
subprocess-killing E2E files.

Reproduced on both the pre-review HEAD and the post-review HEAD (after the
coordinator added a PostgreSQL advisory lock in `conftest.py` to serialize
concurrent pytest runners, and after fixing the announcer to one transaction) —
the advisory lock only serializes two pytest invocations against each other, it
does nothing for a single run's own subprocess connection pressure. The full
`scripts/quality-gate.ps1 -E2E` run failed on this same symptom
(`test_research_workflow_survives_worker_restart`, `test_voice_persistence`)
even solo.

**The originally-named flake (`test_selfhealing_e2e.py::test_m6_selfhealing_full_story`)
did not reproduce in my ~8 runs** — the tests that failed instead were
`test_evolution_e2e.py::test_m7_self_extension_story`,
`test_research_artifact.py` (two different tests, across runs),
`test_security_e2e.py` (two different tests), `test_voice_persistence.py`
(three different tests). Given the shared root cause is a threshold/race effect
(whichever test happens to request a new connection when the pool is
saturated), this is consistent with the same underlying defect surfacing on a
different victim each run, not a different problem — but I did not personally
land on that exact test, so say that precisely rather than claim confirmation
of the named case.

**How to apply**: before trusting any "flaky test" report in this repo that
mentions `test_selfhealing_e2e`, `test_evolution_e2e`, `test_security_e2e`,
`test_research_artifact`, or `test_voice_persistence` failing with
`psycopg.OperationalError` / "too many clients already", check `docker exec
pagentos-postgres psql -U pagentos -d pagentos -c "select count(*) from
pg_stat_activity"` before and after a solo run — if it doesn't return to
baseline, this is the same defect, not a fresh one. A real fix needs either
graceful subprocess shutdown (SIGTERM + `engine.dispose()` before
`Process.kill()` as the fallback) or a raised `max_connections` / connection
cap per spawned subprocess, not another layer of test serialization.
