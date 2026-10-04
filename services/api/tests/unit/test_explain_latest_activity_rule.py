"""The "son ne yaptın" expectation of the ledger-explain integration test, against the engine.

The gate of 2026-10-04 (b98ed094) was red on one integration test only when an earlier
test had left a particular row newest on the shared dev database: the test expected
"Efendim, en son ..." (another subsystem's activity) while the engine answered with the
completed research. The test read "the newest finished row of the ledger"; the engine
reads the newest finished OWNER-RELEVANT row of the last seven days. Each case below
feeds the same rows to the real engine (through a fake EventSource) and to both rules:
the old rule disagrees with the engine, ``expected_latest_activity`` agrees.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from app.explain.classify import classify
from app.explain.engine import EventView, EvidenceSource, explain
from tests.integration.test_ledger_explain_over_real_runs import (
    answered_event_id,
    expected_latest_activity,
)

NOW = datetime(2026, 10, 4, 1, 24, tzinfo=UTC)
QUESTION = "Son yaptıklarını anlat"


class FakeEventSource:
    """The ledger as the engine reads it: newest first by occurred_at, then recorded_at."""

    def __init__(self, rows: list[tuple[EventView, datetime]]) -> None:
        self._rows = sorted(rows, key=lambda r: (r[0].occurred_at, r[1]), reverse=True)

    def events(self, *, since, subsystems, statuses, limit):
        out = [
            ev
            for ev, _ in self._rows
            if (since is None or ev.occurred_at >= since)
            and (not subsystems or ev.subsystem in subsystems)
            and (not statuses or ev.status in statuses)
        ]
        return out[:limit]

    def api_rows(self) -> list[dict[str, Any]]:
        """The same rows as GET /v1/ledger/events returns them (newest first)."""
        return [
            {
                "event_id": ev.event_id,
                "occurred_at": ev.occurred_at.isoformat(),
                "recorded_at": recorded.isoformat(),
                "event_type": ev.event_type,
                "subsystem": ev.subsystem,
                "status": ev.status,
                "severity": ev.severity,
                "factual_summary": ev.factual_summary,
            }
            for ev, recorded in self._rows
        ]

    def research_report(self, task_id: str):
        return None

    def open_incidents(self):
        return []


def _ev(event_id: str, minutes_ago: float, event_type: str, subsystem: str) -> EventView:
    return EventView(
        event_id=event_id,
        occurred_at=NOW - timedelta(minutes=minutes_ago),
        event_type=event_type,
        subsystem=subsystem,
        status="completed",
        severity="info",
        factual_summary=f"{event_type} kaydı.",
        research_job_id="2c1c0d2e-0000-4000-8000-000000000001"
        if event_type == "research.completed"
        else None,
    )


def _row(ev: EventView, recorded_after_s: float = 0.0) -> tuple[EventView, datetime]:
    return ev, ev.occurred_at + timedelta(seconds=recorded_after_s)


RESEARCH = _ev("ev-research", 30, "research.completed", "research")


def _old_rule(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The test's expectation before the fix, verbatim in substance."""
    annotations = {"research.qualified", "ledger.backfill", "briefing.queued", "briefing.delivered"}
    return next(
        (
            e
            for e in rows
            if e["status"] in ("completed", "failed") and e["event_type"] not in annotations
        ),
        None,
    )


def _engine_answer(source: FakeEventSource) -> str | None:
    briefing = explain(cast(EvidenceSource, source), QUESTION, classify(QUESTION, now=NOW), now=NOW)
    return answered_event_id(briefing)


CASES = {
    # the narration of the previous answer is newest (a voice integration test)
    "meta_newest": [_row(RESEARCH), _row(_ev("ev-voice", 1, "voice.explained", "voice"))],
    # a browser web task finished after the research (a browser integration test)
    "telemetry_newest": [
        _row(RESEARCH),
        _row(_ev("ev-web", 1, "web_task.finished", "browser")),
    ],
    # same occurred_at, the telemetry row recorded later so it is listed first
    "equal_occurred_at": [
        _row(RESEARCH),
        _row(_ev("ev-web-tie", 30, "web_task.finished", "browser"), recorded_after_s=5),
    ],
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_old_expectation_disagrees_and_the_engine_rule_agrees(case: str) -> None:
    source = FakeEventSource(CASES[case])
    rows = source.api_rows()
    engine_pick = _engine_answer(source)
    assert engine_pick == RESEARCH.event_id

    old = _old_rule(rows)
    assert old is not None and old["event_id"] != engine_pick, case

    leaders = expected_latest_activity(rows, now=NOW)
    assert [e["event_id"] for e in leaders] == [engine_pick], case


def test_a_full_tie_names_every_leader_and_the_engine_reads_one_of_them() -> None:
    """Same occurred_at AND recorded_at: the ledger orders no further, so the rule
    returns both and the integration test judges the engine by the one it read."""
    deploy = _ev("ev-deploy", 30, "deployment.cloud_core.released", "deployment")
    source = FakeEventSource([_row(RESEARCH), _row(deploy)])
    leaders = expected_latest_activity(source.api_rows(), now=NOW)
    assert {e["event_id"] for e in leaders} == {RESEARCH.event_id, deploy.event_id}
    assert _engine_answer(source) in {e["event_id"] for e in leaders}


def test_a_finished_activity_older_than_the_week_is_not_the_answer() -> None:
    """The engine reads seven days; the old test read the whole ledger."""
    old_research = _ev("ev-research-old", 8 * 24 * 60, "research.completed", "research")
    source = FakeEventSource([_row(old_research)])
    assert _engine_answer(source) is None
    assert _old_rule(source.api_rows()) is not None
    assert expected_latest_activity(source.api_rows(), now=NOW) == []


def test_only_telemetry_in_the_week_is_still_answered_from_it() -> None:
    """With nothing owner-relevant the engine falls back to the whole pool."""
    web = _ev("ev-web-only", 2, "web_task.finished", "browser")
    source = FakeEventSource([_row(web)])
    leaders = expected_latest_activity(source.api_rows(), now=NOW)
    assert [e["event_id"] for e in leaders] == [web.event_id] == [_engine_answer(source)]
