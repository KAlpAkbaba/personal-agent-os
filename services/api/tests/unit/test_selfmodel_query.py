"""Unit tests: the Self Model query layer (PHASE 6).

These are the questions the owner will eventually ask in Turkish, asked here as
structured calls. What every test is really checking is the same thing: the
answer is backed by a row, or it says it is not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.ledger.models import ActivityEventRow
from app.selfhealing.models import Incident, Release
from app.selfmodel import query
from app.selfmodel.indexer import IndexConfig, build_index
from app.selfmodel.models import TRUTH_EVIDENCE, TRUTH_INSTALLED, TRUTH_RUNTIME
from app.selfmodel.query import REQUIRED_GATES, is_stale, normalize_key
from tests.selfmodel_support import (
    FIXTURE_MODULE,
    FIXTURE_PACKAGE,
    FIXTURE_TEST_MODULE,
    make_engine,
    write_fixture_tree,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return write_fixture_tree(tmp_path / "repo")


@pytest.fixture()
def sessions():
    engine = make_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _reindex(factory, repo: Path) -> None:
    with factory() as session:
        build_index(session, config=IndexConfig(repo_root=repo))
        session.commit()


@pytest.fixture()
def indexed(sessions, repo: Path):
    _reindex(sessions, repo)
    return sessions


def _event(session: Session, **kwargs) -> uuid.UUID:
    defaults = {
        "occurred_at": datetime.now(UTC),
        "subsystem": "evolution",
        "status": "completed",
        "severity": "info",
        "action": "gate",
        "evidence_refs": [],
        "factual_summary": "Kayit.",
        "detail_json": {},
        "source": "live",
    }
    defaults.update(kwargs)
    row = ActivityEventRow(**defaults)
    session.add(row)
    session.flush()
    return row.event_id


def _pass_gates(factory, repo: Path, module_id: str, gates: tuple[str, ...]) -> None:
    with factory() as session:
        for gate in gates:
            _event(
                session,
                event_type=f"evolution.{gate}",
                related_module_id=module_id,
                source_ref=f"test:{module_id}:{gate}",
            )
        session.commit()
    _reindex(factory, repo)


# ------------------------------------------------------------- resolution


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Diagnostic Observer", "diagnostic_observer"),
        ("Diagnostic Observer'da", "diagnostic_observer"),
        ("app/observer/diagnostic_observer.py", "app/observer/diagnostic_observer"),
        ("  APP.Observer  ", "app.observer"),
        ("Ledger-Service", "ledger_service"),
    ],
)
def test_spoken_names_normalize(spoken: str, expected: str) -> None:
    assert normalize_key(spoken) == expected


def test_a_spoken_name_resolves_to_the_module_id(indexed) -> None:
    with indexed() as session:
        resolution = query.resolve_module(session, "Diagnostic Observer")
    assert resolution.found
    assert resolution.module_id == FIXTURE_MODULE
    assert resolution.matched_by == "leaf"
    assert resolution.confidence > 0.5


def test_a_two_word_name_can_also_be_a_dotted_path(indexed) -> None:
    with indexed() as session:
        resolution = query.resolve_module(session, "Observer Helpers")
    assert resolution.module_id == "app.observer.helpers"


def test_an_unknown_name_returns_not_found_with_candidates(indexed) -> None:
    with indexed() as session:
        answer = query.module_status(session, "app.observer.telemetry_sink")
    assert answer.found is False
    assert answer.confidence == 0.0
    assert answer.to_dict()["reason"] == "module_id_not_found"
    candidates = answer.to_dict()["candidates"]
    assert candidates, "a not-found answer offers what the index does have"
    assert FIXTURE_PACKAGE in candidates


def test_an_ambiguous_name_refuses_to_pick_one(sessions, repo: Path) -> None:
    twin = repo / "services/api/app/other/diagnostic_observer.py"
    twin.parent.mkdir(parents=True, exist_ok=True)
    twin.write_text('"""A second observer."""\n', encoding="utf-8")
    (twin.parent / "__init__.py").write_text('"""Other."""\n', encoding="utf-8")
    _reindex(sessions, repo)

    with sessions() as session:
        resolution = query.resolve_module(session, "Diagnostic Observer")
    assert resolution.found is False
    assert resolution.matched_by == "ambiguous"
    assert set(resolution.candidates) == {FIXTURE_MODULE, "app.other.diagnostic_observer"}


# ------------------------------------------------------- "ne durumda?"


def test_module_status_reports_what_the_module_is(indexed) -> None:
    with indexed() as session:
        answer = query.module_status(session, "Diagnostic Observer")
    facts = answer.to_dict()

    assert facts["module_id"] == FIXTURE_MODULE
    assert facts["kind"] == "module"
    assert facts["path"] == "services/api/app/observer/diagnostic_observer.py"
    assert facts["purpose"] == "Watches subsystem health and records what it saw."
    assert facts["symbol_counts"]["class"] == 1
    assert facts["symbol_counts"]["route"] == 1
    assert facts["tests"] == [FIXTURE_TEST_MODULE]
    assert facts["adr_refs"] == ["ADR-0099"]
    assert answer.evidence_refs


def test_a_source_only_module_is_not_live_and_says_why(indexed) -> None:
    """ "Bu kod canlida mi?" over a module with no deployment evidence."""
    with indexed() as session:
        answer = query.module_status(session, FIXTURE_MODULE)
    facts = answer.to_dict()

    assert facts["is_live"] is False
    assert "no_runtime_evidence" in facts["unknown"]
    # ...said once, not twice: "no_runtime_truth" would be the same fact again.
    assert "no_runtime_truth" not in facts["unknown"]
    assert "no_installed_truth" in facts["unknown"]
    assert set(facts["truths"]) == {"source"}
    assert facts["production_state"] == "source_only"
    # A source-only answer is never a confident one.
    assert answer.confidence < 0.7


def test_status_reports_live_once_a_runtime_row_exists(indexed, repo: Path) -> None:
    with indexed() as session:
        _event(
            session,
            event_type="deployment.observer.released",
            subsystem="deployment",
            related_module_id=FIXTURE_MODULE,
            version="2.0.0",
            detail_json={"module": {"package_sha256": "b" * 64}},
            source_ref="test:runtime",
        )
        session.commit()
    _reindex(indexed, repo)

    with indexed() as session:
        answer = query.module_status(session, FIXTURE_MODULE)
    facts = answer.to_dict()
    assert facts["is_live"] is True
    assert facts["truths"]["runtime"]["version"] == "2.0.0"
    assert facts["production_state"] == "running"


# --------------------------------------------------------- "sorun ne?"


def test_module_problems_returns_open_incidents(indexed) -> None:
    with indexed() as session:
        session.add(
            Incident(
                component="diagnostic_observer",
                severity="critical",
                fingerprint="f" * 32,
                status="open",
                evidence_json={"check": "health"},
                occurrence_count=3,
            )
        )
        session.add(
            Incident(
                component="diagnostic_observer",
                severity="warning",
                fingerprint="c" * 32,
                status="closed",
                evidence_json={},
            )
        )
        session.commit()

        answer = query.module_problems(session, "Diagnostic Observer")

    facts = answer.to_dict()
    assert facts["has_problems"] is True
    assert len(facts["open_incidents"]) == 1
    assert facts["open_incidents"][0]["severity"] == "critical"
    assert facts["open_incidents"][0]["occurrence_count"] == 3
    assert answer.evidence_refs


def test_module_problems_with_nothing_recorded_says_so(indexed) -> None:
    with indexed() as session:
        answer = query.module_problems(session, FIXTURE_MODULE)
    facts = answer.to_dict()
    assert facts["has_problems"] is False
    assert facts["open_incidents"] == []
    assert "no_recorded_problem_rows" in facts["unknown"]
    # Limitations are stated as facts about the index, not as problems invented
    # about the code.
    assert "no_runtime_evidence" in facts["known_limitations"]


def test_module_problems_includes_failed_test_runs(indexed) -> None:
    with indexed() as session:
        _event(
            session,
            event_type="evolution.tests_failed",
            status="failed",
            severity="warning",
            related_module_id=FIXTURE_TEST_MODULE,
            factual_summary="Birim testleri basarisiz.",
            source_ref="test:failed-run",
        )
        session.commit()
        answer = query.module_problems(session, FIXTURE_MODULE)

    facts = answer.to_dict()
    assert len(facts["failed_tests"]) == 1
    assert facts["failed_tests"][0]["event_type"] == "evolution.tests_failed"


# ------------------------------------------- "son test neden basarisiz oldu?"


def test_last_test_failure_returns_the_newest_failure(indexed) -> None:
    now = datetime.now(UTC)
    with indexed() as session:
        _event(
            session,
            occurred_at=now - timedelta(hours=2),
            event_type="evolution.tests_failed",
            status="failed",
            related_module_id=FIXTURE_TEST_MODULE,
            factual_summary="Eski hata.",
            source_ref="test:old",
        )
        _event(
            session,
            occurred_at=now,
            event_type="evolution.tests_failed",
            status="failed",
            related_module_id=FIXTURE_TEST_MODULE,
            result="AssertionError",
            factual_summary="Yeni hata.",
            source_ref="test:new",
        )
        session.commit()
        answer = query.last_test_failure(session, "Diagnostic Observer")

    facts = answer.to_dict()
    assert facts["failure"]["factual_summary"] == "Yeni hata."
    assert facts["failure"]["result"] == "AssertionError"
    assert answer.evidence_refs[0]["kind"] == "activity_event"


def test_last_test_failure_with_no_rows_is_honest(indexed) -> None:
    with indexed() as session:
        answer = query.last_test_failure(session, FIXTURE_MODULE)
    facts = answer.to_dict()
    assert facts["failure"] is None
    assert facts["unknown"] == ["no_recorded_test_failure"]


# ------------------------------------------------- "neden boyle yazildi?"


def test_why_written_cites_the_adr_and_the_docstring(indexed) -> None:
    with indexed() as session:
        answer = query.why_written(session, "Diagnostic Observer")
    facts = answer.to_dict()

    assert facts["adr_refs"] == ["ADR-0099"]
    assert facts["purpose"] == "Watches subsystem health and records what it saw."
    assert "docs/DECISIONS.md#ADR-0099" in facts["documents"]
    assert facts["unknown"] == []
    assert answer.confidence > 0.5


def test_why_written_admits_when_nothing_was_recorded(indexed) -> None:
    with indexed() as session:
        answer = query.why_written(session, "services/browser/browser_agent/worker.py")
    facts = answer.to_dict()
    assert facts["adr_refs"] == []
    assert "no_recorded_rationale" in facts["unknown"]


# ------------------------------------- "hangi surum gercekten calisiyor?"


def test_what_is_running_without_evidence_refuses_to_answer(indexed) -> None:
    with indexed() as session:
        answer = query.what_is_running(session, "cloud_core")
    facts = answer.to_dict()

    assert facts["runtime_known"] is False
    assert facts["answer"] == "no_runtime_evidence"
    assert any(u.startswith("no_runtime_evidence:") for u in facts["unknown"])
    assert answer.confidence < 0.5


def test_a_promoted_release_alone_is_reported_as_installed_not_running(
    indexed, repo: Path, monkeypatch
) -> None:
    """The browser-worker lesson, asked as a question: a release record must not
    be allowed to answer "what is running?".

    The component is mapped explicitly, because after the 2026-09-05 review a component
    that is neither mapped nor an exact module id attributes nothing at all."""
    from app.selfmodel import indexer as _indexer

    monkeypatch.setitem(_indexer.COMPONENT_MODULE_HINTS, "diagnostic_observer", (FIXTURE_MODULE,))
    with indexed() as session:
        session.add(
            Release(
                component="diagnostic_observer",
                version="1.0.0",
                manifest_digest="e" * 64,
                status="active",
                promoted_at=datetime.now(UTC),
            )
        )
        session.commit()
    _reindex(indexed, repo)

    with indexed() as session:
        answer = query.what_is_running(session, "Diagnostic Observer")
    facts = answer.to_dict()

    assert facts["runtime_known"] is False
    assert facts["answer"] == "no_runtime_evidence"
    assert facts["modules"][0]["installed"]["version"] == "1.0.0"
    assert facts["modules"][0]["runtime"] is None
    assert facts["modules"][0]["is_live"] is False


def test_what_is_running_reports_the_runtime_row_when_one_exists(indexed, repo: Path) -> None:
    with indexed() as session:
        _event(
            session,
            event_type="deployment.observer.released",
            subsystem="deployment",
            related_module_id=FIXTURE_MODULE,
            version="3.1.4",
            detail_json={"module": {"package_sha256": "9" * 64}},
            source_ref="test:runtime-report",
        )
        session.commit()
    _reindex(indexed, repo)

    with indexed() as session:
        answer = query.what_is_running(session, "Diagnostic Observer")
    facts = answer.to_dict()

    assert facts["runtime_known"] is True
    assert facts["answer"] == "running"
    assert facts["modules"][0]["runtime"]["version"] == "3.1.4"
    assert facts["modules"][0]["runtime"]["digest"] == "9" * 64
    assert answer.evidence_refs[0]["kind"] == "activity_event"


# ------------------------------------------ "canliya alinmaya hazir mi?"


def test_ready_for_production_refuses_to_say_yes_without_gate_evidence(indexed) -> None:
    with indexed() as session:
        answer = query.ready_for_production(session, "Diagnostic Observer")
    facts = answer.to_dict()

    assert facts["ready"] is False
    assert facts["gates_passed"] == []
    assert set(facts["missing"]) == set(REQUIRED_GATES)
    assert "no_gate_evidence" in facts["unknown"]


def test_ready_for_production_names_the_one_missing_gate(indexed, repo: Path) -> None:
    _pass_gates(indexed, repo, FIXTURE_MODULE, ("tests_passed", "security_review_passed"))

    with indexed() as session:
        answer = query.ready_for_production(session, FIXTURE_MODULE)
    facts = answer.to_dict()

    assert facts["ready"] is False
    assert facts["missing"] == ["shadow_ready"]
    assert set(facts["gates_passed"]) == {"tests_passed", "security_review_passed"}


def test_ready_for_production_says_yes_only_with_every_gate(indexed, repo: Path) -> None:
    _pass_gates(indexed, repo, FIXTURE_MODULE, REQUIRED_GATES)

    with indexed() as session:
        answer = query.ready_for_production(session, FIXTURE_MODULE)
    facts = answer.to_dict()

    assert facts["ready"] is True
    assert facts["missing"] == []
    assert facts["production_state"] == "shadow"
    assert answer.evidence_refs
    assert all(ref["kind"] == "activity_event" for ref in answer.evidence_refs)


def test_an_open_incident_blocks_readiness_even_with_every_gate(indexed, repo: Path) -> None:
    _pass_gates(indexed, repo, FIXTURE_MODULE, REQUIRED_GATES)
    with indexed() as session:
        session.add(
            Incident(
                component="diagnostic_observer",
                severity="critical",
                fingerprint="a" * 32,
                status="open",
                evidence_json={},
            )
        )
        session.commit()

        answer = query.ready_for_production(session, FIXTURE_MODULE)

    facts = answer.to_dict()
    assert facts["ready"] is False
    assert facts["missing"] == ["open_incidents"]
    assert facts["open_incident_count"] == 1


def test_readiness_of_an_unknown_module_is_a_not_found(indexed) -> None:
    """A not-found answer carries no ``ready`` field at all -- there is nothing
    to be ready, and a ``False`` here would read as "not ready yet"."""
    with indexed() as session:
        answer = query.ready_for_production(session, "app.nowhere.at.all")
    facts = answer.to_dict()

    assert answer.found is False
    assert "ready" not in facts
    assert facts["reason"] == "module_id_not_found"
    assert facts["candidates"]


# --------------------------------------------------------------- search


def test_search_finds_modules_and_symbols(indexed) -> None:
    with indexed() as session:
        result = query.search(session, "observ")
    module_ids = {m["module_id"] for m in result["modules"]}
    symbol_names = {s["name"] for s in result["symbols"]}

    assert FIXTURE_MODULE in module_ids
    assert "observe" in symbol_names


def test_list_modules_can_filter_by_kind(indexed) -> None:
    with indexed() as session:
        packages = query.list_modules(session, kind="package")
        scripts = query.list_modules(session, kind="script")

    assert FIXTURE_PACKAGE in {m["module_id"] for m in packages}
    assert {m["module_id"] for m in scripts} == {"scripts/Deploy-Observer.ps1"}


# ------------------------------- defects found by independent review (2026-09-05)


def test_turkish_names_fold_to_the_identifier_a_developer_would_have_typed() -> None:
    """Turkish is first-class (CLAUDE.md). Diacritics used to be DELETED, so the owner
    asking about "Günlük Servisi" produced `gnlk_servisi`, matched nothing, and offered
    no candidates either."""
    from app.selfmodel.query import normalize_key

    assert normalize_key("Günlük Servisi") == "gunluk_servisi"
    assert normalize_key("İzleyici") == "izleyici"
    assert normalize_key("Şüpheli Modül") == "supheli_modul"
    assert normalize_key("Çağrı Yönlendirici") == "cagri_yonlendirici"
    # the existing shapes still hold
    assert normalize_key("Diagnostic Observer") == "diagnostic_observer"
    assert normalize_key("Diagnostic Observer'da") == "diagnostic_observer"
    assert normalize_key("app/selfmodel/query.py") == "app/selfmodel/query"


# ------------------------------------------------------------------- staleness


def test_runtime_truth_is_reported_stale_once_it_is_old_enough() -> None:
    """An index that has not looked in an hour must not say "it is running"."""
    seen = datetime(2026, 9, 5, 6, 0, tzinfo=UTC)
    assert not is_stale(TRUTH_RUNTIME, seen, now=seen + timedelta(minutes=5))
    assert is_stale(TRUTH_RUNTIME, seen, now=seen + timedelta(hours=1))
    # installed truth survives until the next deployment; evidence never ages
    assert not is_stale(TRUTH_INSTALLED, seen, now=seen + timedelta(days=1))
    assert is_stale(TRUTH_INSTALLED, seen, now=seen + timedelta(days=30))
    assert not is_stale(TRUTH_EVIDENCE, seen, now=seen + timedelta(days=900))


def test_a_naive_timestamp_is_treated_as_utc_rather_than_crashing() -> None:
    """SQLite hands back naive datetimes; a staleness check must not raise on one."""
    naive = datetime(2026, 9, 5, 6, 0)
    assert is_stale(TRUTH_RUNTIME, naive, now=datetime(2026, 9, 5, 9, 0, tzinfo=UTC))


def test_an_unknown_truth_kind_is_never_guessed_stale() -> None:
    assert not is_stale("something_else", datetime(2020, 1, 1, tzinfo=UTC))
    assert not is_stale(TRUTH_RUNTIME, None)
