"""Unit tests: voice routing qualification as a durable, owner-facing state (ADR-0080).

The suite's report is posted; ONE ledger row records it; the state is derived from rows
alone; a regression opens one EvolutionOpportunity per case, idempotently; and the healthy
word a client posts never outranks the failing numbers it posts with it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.models import Artifact, Task, TaskRun
from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.evolution.models import Capability, CapabilityGap, EvolutionOpportunity, SkillVersion
from app.evolution.runtime import EvolutionRuntime
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import EVENT_TYPE_VOICE_QUALIFICATION, EVENT_TYPES
from app.main import create_app
from app.voice.qualification import service as qualification_service
from tests.identity_support import authenticate

TABLES = [
    ActivityEventRow.__table__,
    Task.__table__,
    Artifact.__table__,
    TaskRun.__table__,
    Capability.__table__,
    SkillVersion.__table__,
    CapabilityGap.__table__,
    EvolutionOpportunity.__table__,
]


def _healthy(**overrides) -> dict:
    return {
        "suite": "OwnerUtteranceSuite",
        "corpus_version": 1,
        "generated_at": "2026-09-07T20:00:00Z",
        "total_cases": 345,
        "passed": 345,
        "clarification": 0,
        "failed_routing": 0,
        "forbidden_side_effects": 0,
        "summary": "HEALTHY",
        "confusion": [],
        **overrides,
    }


def _regression() -> dict:
    return _healthy(
        generated_at="2026-09-08T02:00:00Z",
        passed=343,
        failed_routing=2,
        summary="REGRESSION_FOUND",
        confusion=[
            {
                "case_id": "d.wake.3",
                "utterance": "Ekranı uyandır.",
                "expected": "display_wake",
                "resolved": "alarm_create",
                "problems": ["intent 'alarm_create' != expected 'display_wake'"],
            },
            {
                "case_id": "a.stop.4",
                "utterance": "Sustur.",
                "expected": "alarm_stop",
                "resolved": "none",
                "problems": ["intent 'none' != expected 'alarm_stop'"],
            },
        ],
    )


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("PAGENTOS_EVOLUTION_SKILLS_ROOT", str(tmp_path / "skills"))
    monkeypatch.setenv("PAGENTOS_EVOLUTION_WORK_ROOT", str(tmp_path / "work"))
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    app.state.evolution = EvolutionRuntime(settings, engine=engine)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    try:
        yield client, artifacts
    finally:
        client.close()


def test_the_event_type_is_part_of_the_ledger_vocabulary() -> None:
    assert EVENT_TYPE_VOICE_QUALIFICATION in EVENT_TYPES


def test_the_state_before_any_run_is_not_yet_run(wired) -> None:
    client, _ = wired
    body = client.get("/v1/voice/qualification").json()
    assert body["state"] == qualification_service.STATE_NOT_YET_RUN
    assert body["latest_synthetic_run"] is None
    assert body["owner_audio_qualified"] is False


def test_a_healthy_run_is_one_ledger_row_and_owner_audio_remains_required(wired) -> None:
    client, artifacts = wired
    posted = client.post("/v1/voice/qualification", json=_healthy())
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["routing_state"] == qualification_service.STATE_HEALTHY
    assert body["state"] == qualification_service.STATE_OWNER_AUDIO_TEST_REQUIRED
    assert body["opportunities_opened"] == []
    # Posting the same run twice records once.
    client.post("/v1/voice/qualification", json=_healthy())
    with artifacts.session() as session:
        rows = (
            session.execute(
                select(ActivityEventRow).where(
                    ActivityEventRow.event_type == EVENT_TYPE_VOICE_QUALIFICATION
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].subsystem == "voice"
    assert rows[0].status == "completed"
    assert "345 cümle" in rows[0].factual_summary
    assert rows[0].detail_json["passed"] == 345


def test_a_regression_opens_one_opportunity_per_case_and_reads_as_self_healing(wired) -> None:
    client, artifacts = wired
    first = client.post("/v1/voice/qualification", json=_regression()).json()
    assert first["routing_state"] == qualification_service.STATE_SELF_HEALING
    assert first["state"] == qualification_service.STATE_SELF_HEALING
    assert len(first["opportunities_opened"]) == 2
    assert first["open_opportunities"] == 2
    # The same regression the next night: the same two opportunities, not four.
    again = client.post(
        "/v1/voice/qualification",
        json=_regression() | {"generated_at": "2026-09-09T02:00:00Z"},
    ).json()
    assert again["open_opportunities"] == 2
    with artifacts.session() as session:
        opportunities = session.execute(select(EvolutionOpportunity)).scalars().all()
    assert sorted(o.source_ref for o in opportunities) == [
        "voice_corpus:a.stop.4",
        "voice_corpus:d.wake.3",
    ]
    assert all(o.source == "voice_corpus" for o in opportunities)
    assert all("Ekranı uyandır" in o.statement or "Sustur" in o.statement for o in opportunities)
    listed = client.get("/v1/evolution/opportunities?limit=10").json()
    assert len(listed["opportunities"]) == 2

    state = client.get("/v1/voice/qualification").json()
    assert state["latest_synthetic_run"]["failed_routing"] == 2
    assert [c["case_id"] for c in state["latest_synthetic_run"]["confusion"]] == [
        "d.wake.3",
        "a.stop.4",
    ]


def test_a_regression_with_no_evolution_runtime_reads_as_regression_found(wired) -> None:
    client, _ = wired
    client.app.state.evolution = None
    body = client.post("/v1/voice/qualification", json=_regression()).json()
    assert body["state"] == qualification_service.STATE_REGRESSION_FOUND
    assert body["opportunities_opened"] == []


def test_the_healthy_word_never_outranks_failing_numbers(wired) -> None:
    client, _ = wired
    lying = _healthy(failed_routing=3, summary="HEALTHY")
    body = client.post("/v1/voice/qualification", json=lying).json()
    assert body["latest_synthetic_run"]["summary"] == "REGRESSION_FOUND"
    assert body["state"] in (
        qualification_service.STATE_REGRESSION_FOUND,
        qualification_service.STATE_SELF_HEALING,
    )


def test_a_later_healthy_run_clears_the_regression_state(wired) -> None:
    client, _ = wired
    client.post("/v1/voice/qualification", json=_regression())
    body = client.post(
        "/v1/voice/qualification", json=_healthy(generated_at="2026-09-09T02:00:00Z")
    ).json()
    assert body["routing_state"] == qualification_service.STATE_HEALTHY
    assert body["state"] == qualification_service.STATE_OWNER_AUDIO_TEST_REQUIRED
    # The opportunities it opened are still listed, truthfully, as open.
    assert body["open_opportunities"] == 2


def test_an_owner_audio_qualification_completes_the_picture(wired) -> None:
    client, _ = wired
    client.post("/v1/voice/qualification", json=_healthy())
    body = client.post(
        "/v1/voice/qualification",
        json=_healthy(suite="OwnerAudioSuite", total_cases=4, passed=4, source="owner_audio"),
    ).json()
    assert body["owner_audio_qualified"] is True
    assert body["state"] == qualification_service.STATE_HEALTHY
    assert body["latest_owner_audio_run"]["total_cases"] == 4


def test_the_surface_is_owner_gated(wired) -> None:
    client, _ = wired
    anonymous = TestClient(client.app)
    assert anonymous.get("/v1/voice/qualification").status_code == 401
    assert anonymous.post("/v1/voice/qualification", json=_healthy()).status_code == 401


def test_the_report_is_bounded_on_the_way_in(wired) -> None:
    client, _ = wired
    too_many = _regression() | {
        "confusion": [
            {"case_id": f"x.{i}", "utterance": "u", "expected": "a", "resolved": "b"}
            for i in range(60)
        ]
    }
    assert client.post("/v1/voice/qualification", json=too_many).status_code == 422
    normalised = qualification_service.normalise_report(
        _regression() | {"confusion": [{"case_id": "c"}] * 40}
    )
    assert len(normalised["confusion"]) == qualification_service.MAX_CONFUSION_ROWS
