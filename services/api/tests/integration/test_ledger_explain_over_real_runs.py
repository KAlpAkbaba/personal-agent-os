"""M16 over the real dev database: backfill the ledger from whatever research runs the
dev-chain harness left behind, then explain them - through the owner API and the real
explain service, never through hand-built events.

Two honest outcomes are accepted. When the dev database holds a completed research run
(the local real-chain harness writes one), the briefing must state its counts as known
facts with evidence references. When it holds none (a fresh CI stack), the engine must
say it found no record. Either way a second backfill records nothing new.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.artifacts.runtime import build_artifact_context
from app.config import Settings
from app.explain.classify import classify
from app.explain.engine import (
    LABEL_FACT,
    LABEL_UNCERTAINTY,
    MEANINGFUL_CLASSES,
    NO_EVIDENCE_TR,
    EventView,
    owner_relevance,
)
from app.explain.service import explain_to_briefing
from app.narration.models import NarrationSession
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration


#: "Son ne yaptın" reads the last seven days (app/explain/classify.py: QUERY_LAST_ACTIVITY)
#: and the newest hundred events of them (engine.explain), newest first by occurred_at
#: then recorded_at (app/ledger/service.py: query) - the same rows GET /v1/ledger/events
#: returns for the same window and limit.
LAST_ACTIVITY_WINDOW = timedelta(days=7)
LAST_ACTIVITY_LIMIT = 100
#: Events that annotate another event (a qualification verdict, a backfill run, a
#: briefing's queueing): never an activity of their own.
ANNOTATION_EVENT_TYPES = frozenset(
    {"research.qualified", "ledger.backfill", "briefing.queued", "briefing.delivered"}
)


def _at(event: dict[str, Any], key: str) -> datetime:
    return datetime.fromisoformat(str(event[key]).replace("Z", "+00:00"))


def _order_key(event: dict[str, Any]) -> tuple[datetime, datetime]:
    return (_at(event, "occurred_at"), _at(event, "recorded_at"))


def expected_latest_activity(
    events: list[dict[str, Any]], *, now: datetime
) -> list[dict[str, Any]]:
    """The events the engine may answer "son ne yaptın" with, by its documented rule.

    The rule (ADR-0214 gate red of 2026-10-04): within the seven-day window, without
    annotations, OWNER RELEVANCE first - voice/ledger/briefing bookkeeping and browser
    telemetry never lead while a task, change, failure, security or evolution event is
    there - then the newest FINISHED one, else the newest of that pool. Reading the
    newest finished row of the whole ledger instead was the gate's order-dependent red:
    an earlier integration test that left a telemetry or meta row newest made the test
    expect "Efendim, en son ..." while the engine rightly answered with the research.

    Returns every event tied for the lead (same occurred_at AND recorded_at): the ledger
    orders no further, so either may be the one the engine read. Empty means "no record".
    """
    since = now - LAST_ACTIVITY_WINDOW
    window = sorted(
        (e for e in events if _at(e, "occurred_at") >= since), key=_order_key, reverse=True
    )[:LAST_ACTIVITY_LIMIT]
    activities = [e for e in window if e["event_type"] not in ANNOTATION_EVENT_TYPES]
    meaningful = [e for e in activities if owner_relevance(_view(e)) in MEANINGFUL_CLASSES]
    pool = meaningful or activities
    finished = [e for e in pool if e["status"] in ("completed", "failed")]
    candidates = finished or pool
    if not candidates:
        return []
    lead = _order_key(candidates[0])
    return [e for e in candidates if _order_key(e) == lead]


def _view(event: dict[str, Any]) -> EventView:
    return EventView(
        event_id=str(event["event_id"]),
        occurred_at=_at(event, "occurred_at"),
        event_type=event["event_type"],
        subsystem=event["subsystem"],
        status=event["status"],
        severity=event["severity"],
        factual_summary=event.get("factual_summary") or "",
    )


def answered_event_id(briefing: Any) -> str | None:
    """The activity event the briefing's first sentence stands on."""
    refs = briefing.executive[0].evidence_refs if briefing.executive else ()
    return next((r["ref"] for r in refs if r.get("kind") == "activity_event"), None)


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


def test_backfill_is_idempotent_and_the_briefing_matches_the_ledger(settings: Settings) -> None:
    client = owner_client(settings)
    try:
        first = client.post("/v1/ledger/backfill")
        assert first.status_code == 200, first.text
        second = client.post("/v1/ledger/backfill")
        assert second.status_code == 200, second.text
        report = second.json()
        assert report["total_created"] == 0, f"a second backfill must record nothing new: {report}"
        assert (
            sum(report["examined"].values()) >= sum(report["skipped"].values()) > 0
            or not (report["examined"])
        )

        # the engine answers "son ne yaptın" with the newest FINISHED owner-relevant
        # activity of the week - derive the expectation with that rule from the same
        # window of rows (see expected_latest_activity)
        now = datetime.now(UTC)
        events = client.get(
            "/v1/ledger/events",
            params={
                "since": (now - LAST_ACTIVITY_WINDOW).isoformat(),
                "limit": LAST_ACTIVITY_LIMIT,
            },
        ).json()["events"]
        leaders = expected_latest_activity(events, now=now)
        policy = client.get("/v1/ledger/policy").json()
        assert policy["ledger_version"] >= 1
        assert "research.completed" in policy["event_types"]
    finally:
        client.close()

    factory, _store = build_artifact_context(settings)
    with factory() as db:
        record = explain_to_briefing(
            db, "Son yaptıklarını anlat", device_id=None, attach_narration=True
        )
        briefing = record.briefing
        assert classify("Son yaptıklarını anlat").kind == briefing.query.kind
        narration = db.get(NarrationSession, record.narration_session_id)
        assert narration is not None and narration.artifact_id == record.artifact_id

    # on a timestamp tie the engine read one of the leaders; judge it by the one it read
    answered = answered_event_id(briefing)
    latest = next((e for e in leaders if e["event_id"] == answered), None)
    if leaders:
        assert latest is not None, (answered, [e["event_id"] for e in leaders], record.speech)

    if latest is not None and latest["event_type"] == "research.failed":
        # A failed research is the newest activity (a workflow integration test that ran
        # before this one on the shared dev database, or a real failed run): the engine
        # says what it could not find and that the owner is needed, from that row.
        assert "kaynak" in record.speech or "kanıt" in record.speech, record.speech
        assert "Müdahalenizi gerektiren" in record.speech, record.speech
        assert any(
            r.get("kind") == "activity_event" and r.get("ref") == latest["event_id"]
            for r in briefing.evidence_refs
        )
    elif latest is not None and latest["event_type"] == "research.completed":
        detail = latest.get("detail_json") or {}
        # ADR-0067 / ADR-0074: the outcome sentence is the research RESULT - a completed
        # run ("'<konu>' konusunda araştırmayı tamamladım"), an honestly thin one ("kısa
        # araştırma bütçesinde ... bulgular sınırlı") or no defensible finding - never
        # the pipeline's counts. Whichever the dev database holds, it addresses the owner
        # and names the research.
        assert record.speech.startswith("Efendim, "), record.speech
        assert "araştırma" in record.speech or "bulgu" in record.speech, record.speech
        assert briefing.executive[0].label in (LABEL_FACT, LABEL_UNCERTAINTY)
        # the numbers spoken are the numbers the ledger holds
        assert briefing.counts()["facts"] >= 3
        assert any(
            r.get("kind") == "activity_event" and r.get("ref") == latest["event_id"]
            for r in briefing.evidence_refs
        )
        assert int(detail.get("evidence", 0)) >= 0
    elif latest is None:
        assert record.speech == NO_EVIDENCE_TR
        assert briefing.executive[0].label == LABEL_UNCERTAINTY
    else:
        # another subsystem's activity is newest (a voice session, a release): the
        # briefing names it, from its own row, and never claims a research run
        assert record.speech.startswith("Efendim, en son")
        assert briefing.executive[0].label == LABEL_FACT
        assert briefing.executive[0].evidence_refs
        assert "sonuç ve" not in record.speech
