"""The Onay Merkezi's Cloud Core half: ``/v1/team/approvals`` (docs/TEAM_PROTOCOL.md section 3).

The owner speaks at three gates. Two of them are decided here: a task at ``awaiting_owner``
(the idea) and a task at ``awaiting_release`` (the release). The decision is written into
``team/queue.json`` - where the NEXT cycle reads it - and into the ledger. It is never applied
to a run in progress, and approving a release never starts one.

The ledger's vocabulary is closed and the lead wires the two new event types at merge time
(app/ledger/vocabulary.py is outside this task's area), so the fixture below adds them for
the tests that are about something else and ``test_a_decision_the_ledger_vocabulary_refuses...``
runs without them.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.broker.runtime import BrokerRuntime
from app.config import Settings
from app.ledger import vocabulary
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.team import approvals
from app.team.routes import router as team_router
from tests.identity_support import authenticate, install_identity

REPO = Path(__file__).resolve().parents[4]
BASE = "/v1/team/approvals"


def _task(task_id: str, state: str, **extra: Any) -> dict[str, Any]:
    task = {
        "id": task_id,
        "title": f"Görev {task_id}",
        "roadmap_row": "row",
        "state": state,
        "area": ["services/api/app/x"],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": "2026-09-30T00:00:00Z",
    }
    task.update(extra)
    return task


def _write_queue(root: Path, tasks: list[dict[str, Any]]) -> None:
    (root / "queue.json").write_text(
        json.dumps({"version": 1, "tasks": tasks}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _queue(root: Path) -> dict[str, Any]:
    return json.loads((root / "queue.json").read_text(encoding="utf-8"))


def _state(root: Path, task_id: str) -> dict[str, Any]:
    return next(t for t in _queue(root)["tasks"] if t["id"] == task_id)


def _digest(root: Path) -> str:
    return hashlib.sha256((root / "queue.json").read_bytes()).hexdigest()


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture()
def team_root(tmp_path: Path) -> Path:
    root = tmp_path / "team"
    (root / "proposals").mkdir(parents=True)
    (root / "reports").mkdir()
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    (root / "proposals" / "fikir-a.md").write_text("# Fikir A\nSesli onay.\n", encoding="utf-8")
    (root / "reports" / "pilot-01.md").write_text(
        "# Döngü raporu\nhepsi yolunda\n", encoding="utf-8"
    )
    _write_queue(
        root,
        [
            _task("fikir-a", "awaiting_owner", proposal="team/proposals/fikir-a.md"),
            _task(
                "yayin-b",
                "awaiting_release",
                sha="a" * 40,
                reports=[
                    {
                        "cycle": "pilot-01",
                        "role": "inspector",
                        "at": "2026-09-30T01:00:00Z",
                        "file": "team/reports/pilot-01.md",
                        "summary": ["APPROVE"],
                    }
                ],
            ),
            _task("calisan-c", "in_progress"),
            _task("cihaz-d", "awaiting_real_evidence"),
            _task("bitti-e", "done"),
        ],
    )
    return root


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def wired_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vocabulary, "SUBSYSTEMS", (*vocabulary.SUBSYSTEMS, approvals.SUBSYSTEM_TEAM)
    )
    monkeypatch.setattr(
        vocabulary,
        "EVENT_TYPES",
        (*vocabulary.EVENT_TYPES, approvals.EVENT_TASK_APPROVED, approvals.EVENT_TASK_REJECTED),
    )


@pytest.fixture()
def app_and_client(engine, team_root: Path):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    broker = BrokerRuntime(settings)
    broker._engine = engine
    broker._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.broker = broker
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    app.state.team_root = team_root
    app.include_router(team_router)
    return app, TestClient(app)


@pytest.fixture()
def client(app_and_client, wired_vocabulary) -> TestClient:
    app, test_client = app_and_client
    authenticate(app, test_client, settings=Settings(_env_file=None))
    return test_client


def _events(engine) -> list[ActivityEventRow]:
    with sessionmaker(bind=engine)() as session:
        return list(session.execute(select(ActivityEventRow)).scalars())


def _decide(client: TestClient, **body: Any):
    return client.post(f"{BASE}/decision", json=body)


# ------------------------------------------------------------------- listing


def test_a_task_at_each_of_the_two_gates_is_listed_with_its_proposal_or_report(client, team_root):
    body = client.get(BASE).json()
    by_id = {a["task_id"]: a for a in body["approvals"]}
    assert set(by_id) == {"fikir-a", "yayin-b"}
    assert by_id["fikir-a"]["gate"] == "fikir"
    assert "Sesli onay." in by_id["fikir-a"]["proposal_text"]
    assert by_id["yayin-b"]["gate"] == "yayin"
    assert by_id["yayin-b"]["sha"] == "a" * 40
    assert by_id["yayin-b"]["reports"][0]["summary"] == ["APPROVE"]


def test_a_task_that_is_not_at_one_of_the_two_gates_is_not_listed(client):
    # in_progress, done, and awaiting_real_evidence (the third gate: real-device proof is
    # not decided by a tap) - each a near miss beside the two that are listed.
    listed = {a["task_id"] for a in client.get(BASE).json()["approvals"]}
    assert not listed & {"calisan-c", "bitti-e", "cihaz-d"}


def test_the_newest_cycle_report_is_returned_beside_the_list(client, team_root):
    (team_root / "reports" / "pilot-02.md").write_text("# İkinci rapor\n", encoding="utf-8")
    report = client.get(BASE).json()["cycle_report"]
    assert report["file"] == "pilot-02.md"
    assert "İkinci rapor" in report["text"]


def test_a_cycle_report_that_does_not_exist_is_null_not_an_error(client, team_root):
    for f in (team_root / "reports").iterdir():
        f.unlink()
    response = client.get(BASE)
    assert response.status_code == 200
    assert response.json()["cycle_report"] is None


def test_a_proposal_path_that_leaves_the_team_folder_is_not_read(client, team_root):
    secret = team_root.parent / "secret.txt"
    secret.write_text("gizli", encoding="utf-8")
    tasks = _queue(team_root)["tasks"]
    tasks[0]["proposal"] = "team/../secret.txt"
    _write_queue(team_root, tasks)
    item = next(a for a in client.get(BASE).json()["approvals"] if a["task_id"] == "fikir-a")
    assert item["proposal_text"] is None
    assert item["proposal"] == "team/../secret.txt"  # said verbatim, never opened


def test_a_proposal_that_is_prose_not_a_path_is_shown_as_it_is(client, team_root):
    tasks = _queue(team_root)["tasks"]
    tasks[0]["proposal"] = "Kısa öneri metni"
    _write_queue(team_root, tasks)
    item = next(a for a in client.get(BASE).json()["approvals"] if a["task_id"] == "fikir-a")
    assert item["proposal_text"] == "Kısa öneri metni"


def test_the_listing_says_when_a_cycle_holds_the_queue(client, team_root):
    assert client.get(BASE).json()["cycle_running"] is False
    _hold_lock(team_root, hours_ago=1)
    assert client.get(BASE).json()["cycle_running"] is True


def test_the_real_queue_of_this_repository_is_listed_without_error():
    # The contract's other half: the file the cycle scripts write, read by this code.
    queue = approvals.load_queue(REPO / "team" / "queue.json")
    assert isinstance(approvals.list_pending(queue, REPO / "team"), list)


def test_the_approvals_routes_refuse_a_caller_without_an_owner_session(app_and_client):
    _, anonymous = app_and_client
    assert anonymous.get(BASE).status_code in (401, 403)
    assert anonymous.post(f"{BASE}/decision", json={}).status_code in (401, 403)


# ----------------------------------------------------------------- deciding


def test_onayla_moves_an_idea_to_approved_and_leaves_the_other_tasks_as_they_were(
    client, team_root
):
    before = {t["id"]: copy.deepcopy(t) for t in _queue(team_root)["tasks"]}
    response = _decide(client, task_id="fikir-a", decision="approve")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "approved"
    after = {t["id"]: t for t in _queue(team_root)["tasks"]}
    assert after["fikir-a"]["state"] == "approved"
    assert after["fikir-a"]["updated_at"] != before["fikir-a"]["updated_at"]
    for other in set(before) - {"fikir-a"}:
        assert after[other] == before[other]


def test_reddet_moves_a_task_to_stopped_with_the_owners_reason(client, team_root):
    response = _decide(
        client, task_id="fikir-a", decision="reject", reason="Şimdi değil, maliyet yüksek"
    )
    assert response.status_code == 200, response.text
    task = _state(team_root, "fikir-a")
    assert task["state"] == "stopped"
    assert task["reason"] == "Şimdi değil, maliyet yüksek"


def test_reddet_without_a_reason_is_refused_and_the_queue_is_untouched(client, team_root):
    before = _digest(team_root)
    assert _decide(client, task_id="fikir-a", decision="reject").status_code == 422
    assert _decide(client, task_id="fikir-a", decision="reject", reason="   ").status_code == 422
    assert _digest(team_root) == before


def test_a_decision_word_that_is_neither_approve_nor_reject_is_refused(client, team_root):
    before = _digest(team_root)
    assert _decide(client, task_id="fikir-a", decision="maybe").status_code == 422
    assert _digest(team_root) == before


def test_a_task_that_is_not_at_a_gate_cannot_be_decided(client, team_root):
    before = _digest(team_root)
    for task_id in ("calisan-c", "bitti-e", "cihaz-d"):
        response = _decide(client, task_id=task_id, decision="approve")
        assert response.status_code == 409, task_id
        assert response.json()["detail"]["code"] == "not_at_a_gate"
    assert _digest(team_root) == before


def test_an_unknown_task_is_a_404_and_nothing_is_written(client, team_root):
    before = _digest(team_root)
    assert _decide(client, task_id="yok-mu", decision="approve").status_code == 404
    assert _digest(team_root) == before


def test_a_second_decision_on_the_same_task_is_refused(client, team_root):
    assert _decide(client, task_id="fikir-a", decision="approve").status_code == 200
    again = _decide(client, task_id="fikir-a", decision="reject", reason="vazgeçtim")
    assert again.status_code == 409
    assert _state(team_root, "fikir-a")["state"] == "approved"


def test_a_shell_decision_naming_the_wrong_gate_is_refused(client, team_root):
    # A page that was open while the task moved on must not approve the new gate by accident.
    before = _digest(team_root)
    response = _decide(client, task_id="fikir-a", decision="approve", gate="yayin")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "gate_mismatch"
    assert _digest(team_root) == before
    assert _decide(client, task_id="fikir-a", decision="approve", gate="fikir").status_code == 200


def test_a_rewrite_keeps_turkish_letters_the_final_newline_and_no_byte_order_mark(
    client, team_root
):
    _decide(client, task_id="fikir-a", decision="approve")
    raw = (team_root / "queue.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert "Görev fikir-a".encode() in raw  # not ö


# -------------------------------------------------------------------- voice


def test_a_voice_approval_that_names_no_gate_is_refused(client, team_root):
    before = _digest(team_root)
    response = _decide(client, decision="approve", channel="voice")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "gate_required"
    assert _digest(team_root) == before


def test_a_voice_approval_that_names_a_word_that_is_not_a_gate_is_refused(client, team_root):
    before = _digest(team_root)
    for word in ("hepsi", "gerçek cihaz", "onay", ""):
        response = _decide(client, decision="approve", channel="voice", gate=word)
        assert response.status_code == 422, word
    assert _digest(team_root) == before


def test_a_voice_approval_naming_fikir_resolves_the_one_task_at_that_gate(client, team_root):
    response = _decide(client, decision="approve", channel="voice", gate="fikir")
    assert response.status_code == 200, response.text
    assert response.json()["task_id"] == "fikir-a"
    assert _state(team_root, "fikir-a")["state"] == "approved"
    assert _state(team_root, "yayin-b")["state"] == "awaiting_release"


def test_a_voice_approval_accepts_the_gate_spelled_with_or_without_the_dotless_i(client, team_root):
    assert _decide(client, decision="approve", channel="voice", gate="yayın").status_code == 200
    assert _state(team_root, "yayin-b")["state"] == "approved"


def test_a_voice_approval_is_refused_when_two_tasks_wait_at_the_gate(client, team_root):
    tasks = _queue(team_root)["tasks"] + [_task("fikir-f", "awaiting_owner")]
    _write_queue(team_root, tasks)
    before = _digest(team_root)
    response = _decide(client, decision="approve", channel="voice", gate="fikir")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ambiguous"
    assert sorted(response.json()["detail"]["candidates"]) == ["fikir-a", "fikir-f"]
    assert _digest(team_root) == before


def test_a_voice_approval_is_refused_when_no_task_waits_at_the_gate(client, team_root):
    _decide(client, task_id="fikir-a", decision="approve")
    before = _digest(team_root)
    response = _decide(client, decision="approve", channel="voice", gate="fikir")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "nothing_waiting"
    assert _digest(team_root) == before


def test_a_voice_approval_naming_a_task_from_the_other_gate_is_refused(client, team_root):
    before = _digest(team_root)
    response = _decide(client, task_id="yayin-b", decision="approve", channel="voice", gate="fikir")
    assert response.status_code == 409
    assert _digest(team_root) == before


def test_a_voice_rejection_needs_a_reason_like_any_other(client, team_root):
    assert _decide(client, decision="reject", channel="voice", gate="fikir").status_code == 422
    assert _state(team_root, "fikir-a")["state"] == "awaiting_owner"


# ------------------------------------------------------------ release gate


def test_approving_a_release_changes_the_queue_file_and_nothing_else_on_disk(client, team_root):
    before = _tree(team_root)
    response = _decide(client, task_id="yayin-b", decision="approve")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "yayin"
    assert body["applied"] == "next_cycle"
    changed = {k for k, v in _tree(team_root).items() if before.get(k) != v}
    assert changed == {"queue.json"}


# ------------------------------------------------------------- a running cycle


def _hold_lock(root: Path, *, hours_ago: float) -> None:
    at = (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (root / "lock.json").write_text(
        json.dumps(
            {"held": True, "machine": "MAIL", "cycle_id": "c1", "pid": 1, "acquired_at": at}
        ),
        encoding="utf-8",
    )


def test_a_decision_is_refused_while_a_cycle_holds_the_queue(client, team_root):
    _hold_lock(team_root, hours_ago=1)
    before = _digest(team_root)
    response = _decide(client, task_id="fikir-a", decision="approve")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "cycle_running"
    assert _digest(team_root) == before


def test_a_decision_is_taken_when_the_lock_is_older_than_six_hours(client, team_root):
    _hold_lock(team_root, hours_ago=7)
    assert _decide(client, task_id="fikir-a", decision="approve").status_code == 200


def test_a_lock_that_is_not_held_does_not_stop_a_decision(client, team_root):
    (team_root / "lock.json").write_text('{"held": false, "acquired_at": "2026-09-30T00:00:00Z"}')
    assert _decide(client, task_id="fikir-a", decision="approve").status_code == 200


def test_a_lock_file_that_is_missing_does_not_stop_a_decision(client, team_root):
    (team_root / "lock.json").unlink()
    assert _decide(client, task_id="fikir-a", decision="approve").status_code == 200


# -------------------------------------------------------------------- ledger


def test_every_decision_is_one_ledger_event_carrying_who_what_and_the_channel(client, engine):
    _decide(client, task_id="fikir-a", decision="approve")
    _decide(client, task_id="yayin-b", decision="reject", reason="yanlış sürüm", channel="shell")
    events = {e.detail_json["task_id"]: e for e in _events(engine)}
    assert set(events) == {"fikir-a", "yayin-b"}
    assert events["fikir-a"].event_type == approvals.EVENT_TASK_APPROVED
    assert events["fikir-a"].subsystem == approvals.SUBSYSTEM_TEAM
    assert events["fikir-a"].detail_json["gate"] == "fikir"
    assert events["fikir-a"].detail_json["to_state"] == "approved"
    assert events["yayin-b"].event_type == approvals.EVENT_TASK_REJECTED
    assert events["yayin-b"].detail_json["reason"] == "yanlış sürüm"
    assert events["yayin-b"].detail_json["channel"] == "shell"


def test_a_refused_decision_leaves_no_ledger_event(client, engine):
    _decide(client, task_id="calisan-c", decision="approve")
    _decide(client, decision="approve", channel="voice")
    _decide(client, task_id="fikir-a", decision="reject")
    assert _events(engine) == []


def test_a_decision_the_ledger_vocabulary_refuses_is_not_written_to_the_queue(
    app_and_client, team_root, engine
):
    # No wired_vocabulary here: today's closed vocabulary does not know the team's events.
    app, test_client = app_and_client
    authenticate(app, test_client, settings=Settings(_env_file=None))
    before = _digest(team_root)
    response = _decide(test_client, task_id="fikir-a", decision="approve")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ledger_refused"
    assert _digest(team_root) == before
    assert _events(engine) == []
