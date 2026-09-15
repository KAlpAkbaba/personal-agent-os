"""B19 req 63-66, 74, 76-80: the self-model learns what is running, and answers about it.

The index knows 443 modules and recorded every one as `source_only`. That was not a bug in
`_apply_production_states`, which is ordered correctly by evidence strength — it is what
happens when the only producers of runtime truth are HISTORICAL (a `Release` row, a
completed `deployment.*` event) and this deployment has had neither. The most direct
evidence there is, this process answering for itself, was never collected.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.models import Device
from app.selfmodel import query as selfmodel_query
from app.selfmodel.diagnosis import (
    health_sentence,
    last_defect,
    not_working,
    stuck_now,
    summary,
)
from app.selfmodel.models import (
    MODULE_KIND_MODULE,
    PRODUCTION_STATE_RUNNING,
    PRODUCTION_STATE_SOURCE_ONLY,
    TRUTH_RUNTIME,
    CodeModule,
    ModuleProvenance,
)
from app.selfmodel.runtime_truth import (
    DIRECT_OBSERVATION_CONFIDENCE,
    device_rows,
    observe,
)

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

RELEASE = {"version": "714ff2b", "build_id": "19f079c4fda2c3c7", "app_version": "0.1.0"}


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in (
        CodeModule.__table__,
        ModuleProvenance.__table__,
        Device.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _module(db: Session, module_id: str) -> CodeModule:
    row = CodeModule(
        module_id=module_id,
        kind=MODULE_KIND_MODULE,
        path=f"{module_id.replace('.', '/')}.py",
        language="python",
    )
    db.add(row)
    db.commit()
    return row


def _enrolled_device(db: Session, *, build_id: str, capabilities: int) -> Device:
    row = Device(
        id=uuid.uuid4(),
        name="ev-pc",
        platform="windows",
        public_key_spki_b64="x",
        capabilities_json=[f"cap.{i}" for i in range(capabilities)],
        software_version="0.6.0",
        build_id=build_id,
    )
    db.add(row)
    db.commit()
    return row


# ----------------------------------------------------- req 63/64: the Cloud Core's own build


def test_the_self_model_learns_the_build_this_process_is_running(db):
    """req 64. The deployed SHA has been in the health answer all along and the self-model
    had no way to know it: it could only learn a runtime truth from a deployment event
    about the past."""
    _module(db, "app.alarms.service")

    report = observe(db, release=RELEASE, now=NOW)

    assert report.cloud_core_modules == 1
    truth = db.execute(
        select(ModuleProvenance).where(ModuleProvenance.truth_kind == TRUTH_RUNTIME)
    ).scalar_one()
    assert truth.version == "714ff2b"
    assert truth.digest == "19f079c4fda2c3c7"
    assert truth.confidence == DIRECT_OBSERVATION_CONFIDENCE


def test_a_module_with_a_runtime_truth_stops_reading_source_only(db):
    """req 63. The whole point of the four truth kinds, finally exercised: a module the
    process is running must not read the same as one that only exists in the checkout."""
    from app.selfmodel.indexer import _apply_production_states

    module = _module(db, "app.alarms.service")
    _apply_production_states(db, {module.module_id})
    db.commit()
    assert db.get(CodeModule, module.module_id).production_state == PRODUCTION_STATE_SOURCE_ONLY

    observe(db, release=RELEASE, now=NOW)
    _apply_production_states(db, {module.module_id})
    db.commit()

    assert db.get(CodeModule, module.module_id).production_state == PRODUCTION_STATE_RUNNING


def test_no_release_identity_writes_nothing_rather_than_unknown(db):
    """A process that cannot say what it is running says nothing. A row reading
    `version: unknown` would make every module "running" on the strength of an admission
    of ignorance."""
    _module(db, "app.alarms.service")

    report = observe(db, release=None, now=NOW)

    assert report.cloud_core_modules == 0
    assert db.execute(select(ModuleProvenance)).scalars().all() == []


def test_only_modules_the_index_knows_get_a_runtime_truth(db):
    """The observation is about "this process", which is a set of modules the checkout
    defines. A provenance row for a module that does not exist would be a self-model
    describing code nobody can open."""
    _module(db, "app.alarms.service")

    observe(db, release=RELEASE, now=NOW)

    rows = db.execute(select(ModuleProvenance)).scalars().all()
    assert [r.module_id for r in rows] == ["app.alarms.service"]


# ------------------------------------------------------ req 65/66: the device's own build


def test_the_self_model_learns_the_device_build_and_capabilities(db):
    """req 65/66. `Device.build_id` (ADR-0118) and the capability manifest are refreshed
    from every hello, and the self-model has never read the row that decides them."""
    _module(db, "app.devices.status")
    _enrolled_device(db, build_id="19f079c4fda2c3c7", capabilities=85)

    report = observe(db, release=RELEASE, devices=device_rows(db), now=NOW)

    assert report.devices_seen == 1
    assert report.device_modules == 1
    truth = db.execute(
        select(ModuleProvenance).where(ModuleProvenance.module_id == "app.devices.status")
    ).scalar_one()
    assert truth.digest == "19f079c4fda2c3c7"
    assert truth.evidence_refs[0]["capabilities"] == 85


def test_device_identity_comes_from_the_enrolment_row_not_the_status_registry(db):
    """The status registry carries what a device is DOING - idle seconds, display state, a
    ringing alarm - and refreshes six times a minute. Identity belongs to the enrolment
    row, which changes when the agent changes."""
    import ast
    import pathlib

    source = pathlib.Path("app/selfmodel/runtime_truth.py").read_text(encoding="utf-8")
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "app.broker.models" in imported, "identity is read from the enrolment registry"
    # An IMPORT check, not a substring one: the first draft grepped for the string and
    # matched the docstring that explains why the status registry is NOT used.
    assert not any(m.startswith("app.devices") for m in imported)


# --------------------------------------------------------- req 80: observed now goes stale


def test_a_runtime_truth_observed_now_is_stale_within_the_hour(db):
    """req 80, and the companion the observation REQUIRES. `STALE_AFTER` gives a runtime
    truth fifteen minutes, which is right for an observation of this kind: a row written
    when the process started is not evidence about the process an hour later."""
    _module(db, "app.alarms.service")
    observe(db, release=RELEASE, now=NOW)

    fresh = selfmodel_query.is_stale(TRUTH_RUNTIME, NOW, now=NOW + timedelta(minutes=5))
    aged = selfmodel_query.is_stale(TRUTH_RUNTIME, NOW, now=NOW + timedelta(hours=1))

    assert not fresh
    assert aged


# ---------------------------------------------------------------- the four owner questions


def test_what_is_not_working_comes_from_runtime_and_separates_advisory(db):
    """req 78. "çalışma zamanından" is in the requirement for a reason: reading it out of
    the feature matrix would make the answer right exactly as often as a hand-maintained
    document is."""
    answer = not_working(
        {
            "db": {"status": "ok"},
            "temporal": {"status": "fail", "error": "closed port"},
            "redis": {"status": "fail", "required": False},
        }
    )

    assert "çalışmayan: temporal" in answer.speech
    assert "ikincil ve şu an kapalı: redis" in answer.speech
    assert [f.what for f in answer.findings] == ["temporal", "redis"]


def test_everything_working_says_so_with_the_count(db):
    assert "Bütün bileşenler çalışıyor" in health_sentence({"db": {"status": "ok"}})


def test_no_health_measurement_is_not_a_clean_bill_of_health(db):
    answer = not_working({})

    assert answer.grounded is False
    assert "yok" in answer.speech


def test_what_is_stuck_is_read_from_the_world_model(db):
    """req 76. A stuck task raises no incident and logs no error, which is why the question
    needs its own answer: a system that is stuck looks, to every other surface, like a
    system that is idle."""
    answer = stuck_now({"facts": [{"key": "tasks.stuck_count", "value": 3, "stale": False}]}, [])

    assert "üç görev takılı" in answer.speech


def test_a_stale_observation_says_it_does_not_know_rather_than_nothing_is_stuck(db):
    """The one that matters. "Nothing is stuck" is the reassuring sentence, and producing it
    from data nobody refreshed is exactly what this subsystem's staleness rules exist to
    prevent. The first draft of `_metric` returned 0 for a stale fact and said precisely
    that."""
    answer = stuck_now({"facts": [{"key": "tasks.stuck_count", "value": 3, "stale": True}]}, [])

    assert answer.grounded is False
    assert "bilmiyorum" in answer.speech
    assert "yok" not in answer.speech


def test_the_last_bug_is_answered_only_from_recorded_defect_work(db):
    """req 77. "The last bug" is a claim about something that was DIAGNOSED. A system that
    answered it from the most recent exception it happened to see would be telling the
    owner about noise."""
    empty = last_defect([])
    assert empty.grounded is False

    answered = last_defect(
        [
            {"id": "d1", "title": "Eski hata", "created_at": "2026-09-01T00:00:00Z"},
            {"id": "d2", "title": "Alarm iki kez çaldı", "status": "fixed",
             "created_at": "2026-09-12T00:00:00Z"},
        ]
    )
    assert "Alarm iki kez çaldı" in answered.speech
    assert answered.findings[0].evidence["defect_id"] == "d2"


def test_the_summary_leads_with_what_is_broken(db):
    """req 79. A summary that led with the build id would be written for the person who
    built the system rather than for the person relying on it."""
    answer = summary(
        checks={"db": {"status": "ok"}, "temporal": {"status": "fail"}},
        world={"facts": [{"key": "tasks.stuck_count", "value": 0, "stale": False}]},
        runtime=RELEASE,
    )

    assert answer.speech.startswith("Efendim, çalışmayan: temporal")
    assert "714ff2b" in answer.speech


# -------------------------------------------------------------------------- the wiring


def test_the_refresher_observes_runtime_truth_on_every_pass():
    """The guard. A runtime observation nothing calls leaves the map reading `source_only`
    for ever, which is exactly the state this batch found."""
    import inspect

    from app.selfmodel import refresh

    source = inspect.getsource(refresh.SelfModelRefresher.refresh_once)

    assert "_observe_runtime" in source


def test_the_application_gives_the_refresher_its_release_identity():
    """The other half, and the one a unit test cannot fake: `create_app` must hand it a way
    to answer for itself, or the observation writes nothing and says nothing."""
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))

    assert app.state.selfmodel_refresher._release is not None
    release = app.state.selfmodel_refresher._release()
    assert release["build_id"]


# ------------------------------------- the four questions reach an answer, through the door


class _Source:
    """The evidence source's shape, with only what these four questions read."""

    def __init__(self, *, world=None, incidents=None, recent=None) -> None:
        self._world = world or {}
        self._incidents = incidents or []
        self._recent = recent or []

    def events(self, *, since=None, subsystems=None, statuses=None, limit=100):
        return []

    def research_report(self, task_id):
        return None

    def open_incidents(self):
        return list(self._incidents)

    def recent_incidents(self, *, limit=10):
        return list(self._recent)[:limit]

    def world_state(self):
        return dict(self._world)


def _ask(question: str, source: _Source) -> str:
    from app.explain.classify import classify
    from app.explain.engine import explain

    briefing = explain(source, question, classify(question))
    return " ".join(s.text for s in briefing.executive)


WORLD = {
    "facts": [
        {"key": "tasks.stuck_count", "value": 2, "stale": False, "category": "tasks"},
        {
            "key": "dependencies.temporal",
            "value": {"status": "fail"},
            "stale": False,
            "category": "dependencies",
        },
        {
            "key": "dependencies.db",
            "value": {"status": "ok"},
            "stale": False,
            "category": "dependencies",
        },
        {"key": "release.version", "value": "714ff2b", "stale": False, "category": "release"},
    ]
}


def test_nerede_takildin_is_answered_from_the_world_model():
    answer = _ask("Nerede takıldın?", _Source(world=WORLD))

    assert "iki görev takılı" in answer


def test_hangi_ozelliklerin_calismiyor_is_answered_from_runtime():
    answer = _ask("Hangi özelliklerin çalışmıyor?", _Source(world=WORLD))

    assert "temporal" in answer
    assert "db" not in answer.replace("değil", "")


def test_son_bug_neydi_is_answered_from_the_incident_store():
    source = _Source(
        world=WORLD,
        recent=[
            {
                "id": "i1",
                "title": "app.alarms: greeting_twice",
                "status": "fixed",
                "created_at": "2026-09-12T00:00:00Z",
            }
        ],
    )

    answer = _ask("Son bug neydi?", source)

    assert "greeting_twice" in answer


def test_the_summary_answers_all_of_it_at_once():
    answer = _ask("Genel durumun nasıl?", _Source(world=WORLD))

    assert "temporal" in answer
    assert "takılı" in answer
    assert "714ff2b" in answer


def test_a_question_with_nothing_recorded_says_so_rather_than_all_is_well():
    answer = _ask("Nerede takıldın?", _Source(world={"facts": []}))

    assert "bilmiyorum" in answer
