# ADR (draft, lead numbers it): the "son ne yaptın" integration test derives its expectation with the engine's documented rule

Date: 2026-10-04 · Task: ledger-explain-test-order · Related: ADR-0214 (a gate-only red is a product or test race)

## Context

The gate of 2026-10-04 01:24 (b98ed094) was red on one test only:
`tests/integration/test_ledger_explain_over_real_runs.py::test_backfill_is_idempotent_and_the_briefing_matches_the_ledger`.
The test took as "latest" the newest finished, non-annotation row of the WHOLE ledger
(limit 200). The engine (`app/explain/engine.py`, last_activity branch) reads the
classifier's seven-day window (newest 100 rows), narrows to owner-relevant events
(`owner_relevance` in `MEANINGFUL_CLASSES`) and then takes the newest finished one.
Both sides order the same way (`occurred_at desc, recorded_at desc`, `ledger/service.query`),
so ordering was not the difference.

What changes between runs is the browser web-task integration test. It leaves a
`web_task.finished` row newest on the shared dev DB. When that row is `failed`, it is
`failure`, which is owner-relevant, so test and engine agree. When it is `completed`,
it is `telemetry`: the test expected "Efendim, en son ..." and the engine (correctly)
answered with the research. The two other differences (the seven-day window, and
everything tied on both timestamps) never broke a gate, but they can break one the
same way.

## Decision

The test restates the rule in `expected_latest_activity` (window, annotations, owner
relevance, then the newest finished one, else the newest of the pool). It returns every
event tied for the lead, and judges the engine by the tied event it actually cited. It
imports only the public relevance classifier (`owner_relevance`, `MEANINGFUL_CLASSES`,
guarded by its own unit test). The selection itself is written out separately, not
imported. The engine is unchanged. `tests/unit/test_explain_latest_activity_rule.py`
runs the real engine over a fake EventSource for meta-newest, telemetry-newest,
equal-occurred_at, full tie, outside-window and telemetry-only. In each case the old
rule disagrees with the engine and the new one agrees.

## Consequences

If the engine's selection rule changes, the unit test goes red and points at this
helper. It does not leave the integration test red depending on order.
