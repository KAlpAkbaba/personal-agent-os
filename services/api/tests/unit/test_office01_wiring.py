"""cycle-2026-10-01 + office-01: the lead's wiring of fifteen worker packages into the shared files.

TEAM_PROTOCOL section 4: the lead writes the shared files at merge time. Each test here holds
one piece of that wiring - a route that is really mounted, a vocabulary that really knows an
event, a call that is really made - so that a later merge cannot quietly unwire it.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.execution import allowlist_store
from app.explain import service as explain_service
from app.explain.classify import classify
from app.ledger import vocabulary as ledger_vocabulary
from app.main import create_app
from app.narrative.intent import recognise
from app.voice.understanding import normalize as understanding_normalize
from tests.unit.test_narrative_collector import FAIL_MAIL, FAIL_RESEARCH, NOW, make_session, seed

REPO = Path(__file__).resolve().parents[4]
API = REPO / "services" / "api"


def test_the_allow_list_editor_and_the_office_are_really_mounted() -> None:
    app = create_app(Settings())
    with TestClient(app) as client:
        # Mounted, and the owner's: without a session each is refused - not "not found",
        # which is what an unmounted router answers.
        for method, path in (
            ("GET", "/v1/team/allowlist"),
            ("POST", "/v1/team/allowlist"),
            ("DELETE", "/v1/team/allowlist/example.org"),
            ("GET", "/v1/team/office"),
            ("GET", "/v1/team/queue/status"),
            ("PUT", "/v1/team/queue/status"),
        ):
            assert client.request(method, path, json={}).status_code in (401, 403), (method, path)
        assert client.get("/v1/team/nothing-here").status_code == 404


def test_the_ledger_knows_the_allow_list_editors_events() -> None:
    assert ledger_vocabulary.EVENT_TYPE_ALLOWLIST_SITE_ADDED == allowlist_store.EVENT_SITE_ADDED
    assert ledger_vocabulary.EVENT_TYPE_ALLOWLIST_SITE_REMOVED == allowlist_store.EVENT_SITE_REMOVED
    known = set(ledger_vocabulary.EVENT_TYPES)
    assert {allowlist_store.EVENT_SITE_ADDED, allowlist_store.EVENT_SITE_REMOVED} <= known


def test_the_allow_list_store_is_bound_while_the_app_runs_not_at_first_use() -> None:
    """A site the owner added must be allowed after a restart without opening the editor."""
    allowlist_store.unbind()
    app = create_app(Settings())
    assert allowlist_store._FACTORY is None, "an app merely constructed binds nothing"
    with TestClient(app):
        assert allowlist_store._FACTORY is not None, "the running process reads the rows"
    assert allowlist_store._FACTORY is None, "and lets go when it stops"


def test_every_hello_syncs_the_registry_facts() -> None:
    """The call the task `cloud-device-registry` named: after apply_hello, in the hello path."""
    source = (API / "app" / "broker" / "ws.py").read_text("utf-8")
    hello = source.index("service.apply_hello(")
    sync = source.index("cloud_registry.sync_registry_facts(db, device)")
    returned = source.index("return row.id", hello)
    assert hello < sync < returned, "the sync runs inside the hello's own transaction"


def test_the_production_evidence_source_tells_the_narrative_and_names_every_failure() -> None:
    session = make_session()
    try:
        seed(session)
        ask = recognise("bu hafta ne oldu")
        assert ask is not None
        text = explain_service.LedgerEvidenceSource(session).narrative(ask, now=NOW)
        assert FAIL_RESEARCH in text and FAIL_MAIL in text
    finally:
        session.close()


def test_a_what_happened_question_is_asked_of_the_narrative_before_the_classifier() -> None:
    query = explain_service.query_for("bu hafta ne oldu", now=NOW)
    assert query.kind == "narrative"
    assert classify("bu hafta ne oldu", now=NOW).kind != "narrative", (
        "the classifier alone does not know the sentence - that is why query_for exists"
    )
    ordinary = "dün hangi araştırmalar başarısız oldu"
    assert explain_service.query_for(ordinary, now=NOW) == classify(ordinary, now=NOW)
    body = (API / "app" / "explain" / "service.py").read_text("utf-8")
    inside = body.split("def explain_to_briefing(", 1)[1].split("\ndef ", 1)[0]
    assert "query = query_for(question, now=now)" in inside
    assert not re.search(r"query = classify\(", inside)


def test_the_stt_confusion_list_ships_beside_its_one_reader() -> None:
    beside = Path(understanding_normalize.__file__).with_name("stt-confusions.json")
    assert beside.is_file()
    assert not (REPO / "packages" / "protocol" / "stt-confusions.json").exists(), (
        "one reader: it is package data, not a protocol file (the falsification registry's rule)"
    )
    assert understanding_normalize.load_confusions()["ofisü"] == "ofis"
