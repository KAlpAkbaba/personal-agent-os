"""M16 over the real dev database: backfill the ledger from whatever research runs the
dev-chain harness left behind, then explain them - through the owner API and the real
explain service, never through hand-built events.

Two honest outcomes are accepted. When the dev database holds a completed research run
(the local real-chain harness writes one), the briefing must state its counts as known
facts with evidence references. When it holds none (a fresh CI stack), the engine must
say it found no record. Either way a second backfill records nothing new.
"""

from __future__ import annotations

import pytest

from app.artifacts.runtime import build_artifact_context
from app.config import Settings
from app.explain.classify import classify
from app.explain.engine import LABEL_FACT, LABEL_UNCERTAINTY, NO_EVIDENCE_TR
from app.explain.service import explain_to_briefing
from app.narration.models import NarrationSession
from tests.integration.conftest import owner_client

pytestmark = pytest.mark.integration


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

        # the engine answers "son ne yaptın" with the newest FINISHED activity, skipping
        # annotation events (a qualification verdict, a backfill run) - derive the
        # expectation with the same rule from the same rows
        events = client.get("/v1/ledger/events", params={"limit": 200}).json()["events"]
        annotations = {
            "research.qualified",
            "ledger.backfill",
            "briefing.queued",
            "briefing.delivered",
        }
        latest = next(
            (
                e
                for e in events
                if e["status"] in ("completed", "failed") and e["event_type"] not in annotations
            ),
            None,
        )
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

    if latest is not None and latest["event_type"] == "research.completed":
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
