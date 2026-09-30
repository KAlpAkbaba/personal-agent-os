"""pilot-01: the lead's wiring of three worker packages into the shared files.

TEAM_PROTOCOL section 4: a worker never writes a shared file; the lead does, at merge time,
from the worker's report. This file holds what the lead wired: the ledger vocabulary knows
the events the new packages emit (and the two copies of each string are equal - the packages
import nothing of the ledger's vocabulary, so a test has to hold them together), and the
Onay Merkezi's router is mounted in the real application.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.execution import vocabulary as execution_vocabulary
from app.ledger import vocabulary as ledger_vocabulary
from app.main import create_app
from app.team import approvals


def test_the_execution_events_are_registered_under_the_same_strings() -> None:
    assert (
        ledger_vocabulary.EVENT_TYPE_EXECUTION_SELECTED == execution_vocabulary.EXECUTION_SELECTED
    )
    assert (
        ledger_vocabulary.EVENT_TYPE_EXECUTION_FALLBACK == execution_vocabulary.EXECUTION_FALLBACK
    )
    assert ledger_vocabulary.EVENT_TYPE_EXECUTION_REFUSED == execution_vocabulary.EXECUTION_REFUSED
    for name in execution_vocabulary.EXECUTION_EVENT_TYPES:
        assert name in ledger_vocabulary.EVENT_TYPES, name


def test_the_teams_subsystem_and_events_are_registered_under_the_same_strings() -> None:
    assert ledger_vocabulary.SUBSYSTEM_TEAM == approvals.SUBSYSTEM_TEAM
    assert ledger_vocabulary.SUBSYSTEM_TEAM in ledger_vocabulary.SUBSYSTEMS
    assert ledger_vocabulary.EVENT_TYPE_TEAM_TASK_APPROVED == approvals.EVENT_TASK_APPROVED
    assert ledger_vocabulary.EVENT_TYPE_TEAM_TASK_REJECTED == approvals.EVENT_TASK_REJECTED
    for name in (approvals.EVENT_TASK_APPROVED, approvals.EVENT_TASK_REJECTED):
        assert name in ledger_vocabulary.EVENT_TYPES, name


def test_the_onay_merkezi_is_mounted_in_the_real_application() -> None:
    app = create_app(Settings())
    with TestClient(app) as client:
        # Mounted, and the owner's: without a session the list is refused, not served -
        # and not "not found", which is what an unmounted router answers.
        assert client.get("/v1/team/approvals").status_code in (401, 403)
        assert client.post("/v1/team/approvals/decision", json={}).status_code in (401, 403)
        assert client.get("/v1/team/nothing-here").status_code == 404
