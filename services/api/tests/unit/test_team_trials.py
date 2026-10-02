"""The owner's third gate: the trials he makes on a real device (owner-trials-api).

A released task carries ``owner_trials`` - the sentence the owner says or does, the machine,
what he must see or hear. ``GET /v1/team/approvals`` lists the open ones under ``trials``;
``POST /v1/team/trials/decision`` records Oldu / Olmadı. "Oldu" never claims PROVEN_REAL (the
lead writes that row, quoting the owner) and never moves the task; "Olmadı" opens a fix task
with the owner's words and the released sha, without an area (the lead gives it one).

The ledger's vocabulary is closed and the two event types are the lead's to add at merge, so
the tests add them the way ``test_allowlist_editor.py`` does. The same calls go to real
PostgreSQL in ``tests/integration/test_team_trials_postgres.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.ledger import vocabulary
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.team import store as team_store
from app.team import trials
from app.team.models import TeamStateRow
from tests.identity_support import authenticate, install_identity

REPO = Path(__file__).resolve().parents[4]
APPROVALS = "/v1/team/approvals"
DECISION = "/v1/team/trials/decision"
SEEN = "2026-10-02T00:00:00Z"
SHA = "65cd94fffcaaa1028cdfc751729a9238b69102b9"


def _trial(trial_id: str, sentence: str, machine: str = "MAIL", **extra: Any) -> dict[str, Any]:
    trial: dict[str, Any] = {
        "id": trial_id,
        "sentence": sentence,
        "machine": machine,
        "expect": "Hesap makinesi açılır",
        "verdict": None,
        "said": None,
        "at": None,
    }
    trial.update(extra)
    return trial


def _task(task_id: str, state: str, **extra: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "title": f"Görev {task_id}",
        "roadmap_row": "38.4 Uzak masaüstü",
        "state": state,
        "area": ["services/api/app/x"],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": SEEN,
        "updated_at": SEEN,
    }
    task.update(extra)
    return task


TWO_TRIALS = [
    _trial("hesap", "Hesap makinesini aç"),
    _trial("ofis-hesap", "Ofis bilgisayarımdan hesap makinesini aç", "MAIL -> ofis"),
]


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(eng)
    TeamStateRow.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def team_root(tmp_path: Path) -> Path:
    root = tmp_path / "team"
    root.mkdir()
    (root / "queue.json").write_text('{"version": 1, "tasks": []}\n', encoding="utf-8")
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    return root


@pytest.fixture()
def wired_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vocabulary,
        "EVENT_TYPES",
        (*vocabulary.EVENT_TYPES, trials.EVENT_TRIAL_PASSED, trials.EVENT_TRIAL_FAILED),
    )


def _wired(kind: str, engine, team_root: Path, *, owner: bool = True):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    app.state.team_root = team_root
    if kind == "db":
        store: team_store.TeamStore = team_store.DbStore(
            sessionmaker(bind=engine, expire_on_commit=False)
        )
    else:
        store = team_store.FileStore(team_root)
    app.state.team_store = store
    for task in (
        _task("uzak-hesap", "released", sha=SHA, owner_trials=TWO_TRIALS),
        # The old form: a plain sentence, no id to decide on - not listed, still valid.
        _task("eski-bicim", "awaiting_real_evidence", owner_trials=["eski düz cümle"]),
        _task("bitti-a", "done", sha=SHA, owner_trials=[_trial("x", "bitti")]),
        _task("calisan-b", "in_progress", owner_trials=[_trial("y", "çalışıyor")]),
    ):
        store.put_task(task, None)
    client = TestClient(app)
    if owner:
        authenticate(app, client, settings=settings)
    return client, store


@pytest.fixture(params=["file", "db"])
def both(request, engine, team_root, wired_vocabulary):
    return _wired(request.param, engine, team_root)


@pytest.fixture()
def db(engine, team_root, wired_vocabulary):
    return _wired("db", engine, team_root)


def _tasks(store) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in store.read_queue()["tasks"]}


def _events(engine) -> list[ActivityEventRow]:
    with sessionmaker(bind=engine)() as session:
        return list(session.execute(select(ActivityEventRow)).scalars())


# ------------------------------------------------------------------ the listing


def test_a_released_task_with_two_open_trials_is_listed_with_both(both):
    client, _ = both
    listing = client.get(APPROVALS).json()
    assert listing["trials"] == [
        {"task_id": "uzak-hesap", "title": "Görev uzak-hesap", "sha": SHA, "trial": t}
        for t in TWO_TRIALS
    ]


def test_a_decided_trial_leaves_the_listing(both):
    client, _ = both
    assert (
        client.post(
            DECISION, json={"task_id": "uzak-hesap", "trial_id": "hesap", "verdict": "oldu"}
        ).status_code
        == 200
    )
    listed = [t["trial"]["id"] for t in client.get(APPROVALS).json()["trials"]]
    assert listed == ["ofis-hesap"]


# ------------------------------------------------------------------ Oldu


def test_oldu_records_the_verdict_the_words_and_the_time_and_leaves_the_state(both, engine):
    client, store = both
    answer = client.post(
        DECISION,
        json={"task_id": "uzak-hesap", "trial_id": "hesap", "verdict": "oldu", "said": "açıldı"},
    )
    assert answer.status_code == 200, answer.text
    task = _tasks(store)["uzak-hesap"]
    first = task["owner_trials"][0]
    assert (first["verdict"], first["said"]) == ("oldu", "açıldı")
    at = datetime.strptime(first["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert abs((datetime.now(UTC) - at).total_seconds()) < 120
    assert task["owner_trials"][1] == TWO_TRIALS[1]
    assert task["state"] == "released"
    assert "reason" not in task  # one trial is still open: nothing to say yet
    assert [e.event_type for e in _events(engine)] == [trials.EVENT_TRIAL_PASSED]


def test_oldu_on_the_last_open_trial_sets_the_reason_line_and_claims_nothing(both):
    client, store = both
    for trial_id in ("hesap", "ofis-hesap"):
        answer = client.post(
            DECISION, json={"task_id": "uzak-hesap", "trial_id": trial_id, "verdict": "oldu"}
        )
        assert answer.status_code == 200, answer.text
    task = _tasks(store)["uzak-hesap"]
    at = task["owner_trials"][1]["at"]
    assert task["reason"] == f"sahip denedi: oldu ({at}) - PROVEN_REAL satırını lead yazar"
    assert task["state"] == "released"  # never done: PROVEN_REAL is the lead's
    assert len(_tasks(store)) == 4  # no task was opened


# ------------------------------------------------------------------ Olmadı


def test_olmadi_without_the_owners_words_is_422_and_changes_nothing(both, engine):
    client, store = both
    for said in (None, "", "   "):
        body: dict[str, Any] = {"task_id": "uzak-hesap", "trial_id": "hesap", "verdict": "olmadi"}
        if said is not None:
            body["said"] = said
        refused = client.post(DECISION, json=body)
        assert refused.status_code == 422, (said, refused.text)
    assert _tasks(store)["uzak-hesap"]["owner_trials"] == TWO_TRIALS
    assert _events(engine) == []


def test_olmadi_words_are_at_most_500_characters(both):
    client, _ = both
    refused = client.post(
        DECISION,
        json={"task_id": "uzak-hesap", "trial_id": "hesap", "verdict": "olmadi", "said": "a" * 501},
    )
    assert refused.status_code == 422


def test_olmadi_records_the_verdict_and_opens_exactly_one_fix_task(both, engine):
    client, store = both
    answer = client.post(
        DECISION,
        json={
            "task_id": "uzak-hesap",
            "trial_id": "ofis-hesap",
            "verdict": "olmadi",
            "said": "açamadım, ofiste hiçbir şey olmadı",
        },
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["fix_task_id"] == "fix-uzak-hesap-1"
    tasks = _tasks(store)
    trial = tasks["uzak-hesap"]["owner_trials"][1]
    assert (trial["verdict"], trial["said"]) == ("olmadi", "açamadım, ofiste hiçbir şey olmadı")
    assert tasks["uzak-hesap"]["state"] == "released"
    fixes = [t for t in tasks.values() if t["id"].startswith("fix-")]
    assert len(fixes) == 1
    fix = fixes[0]
    assert fix["id"] == "fix-uzak-hesap-1"
    assert fix["state"] == "approved"
    assert fix["area"] == []
    assert fix["reason"] == "alan: lead belirler"
    assert fix["roadmap_row"] == "38.4 Uzak masaüstü"
    assert "Ofis bilgisayarımdan hesap makinesini aç" in fix["title"]
    assert '"Ofis bilgisayarımdan hesap makinesini aç"' in fix["goal"]
    assert '"açamadım, ofiste hiçbir şey olmadı"' in fix["goal"]
    assert SHA in fix["goal"]
    assert "MAIL -> ofis" in fix["goal"]
    assert team_store.task_problems(fix) == []
    assert [e.event_type for e in _events(engine)] == [trials.EVENT_TRIAL_FAILED]


_WINDOWS_POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)
POWERSHELL = str(_WINDOWS_POWERSHELL) if _WINDOWS_POWERSHELL.is_file() else shutil.which("pwsh")


def test_the_fix_task_waits_for_the_leads_split_and_is_never_run_without_an_area(both, tmp_path):
    """Regression (inspector, d20261003): an ``approved`` task with no area and no ``proposal``
    is moved to ``assigned`` by the cycle and breaks the queue for every later cycle
    (``team-feed.tests.ps1``). The fix task carries the trial as its proposal (prose: the split
    card prints it whole, whichever store keeps the queue), and the cycle's OWN functions -
    ``scripts/lib/TeamQueue.ps1`` - judge it a split candidate that is not run."""
    client, store = both
    body = {"task_id": "uzak-hesap", "trial_id": "ofis-hesap", "verdict": "olmadi", "said": "yok"}
    assert client.post(DECISION, json=body).status_code == 200
    fix = _tasks(store)["fix-uzak-hesap-1"]
    assert fix["area"] == []
    assert "Ofis bilgisayarımdan hesap makinesini aç" in fix["proposal"]
    assert SHA in fix["proposal"]
    assert not fix["proposal"].startswith("team/")  # prose, not a file the cycle cannot find
    if POWERSHELL is None:
        pytest.skip("no PowerShell: the cycle's half is not run here")
    task_file = tmp_path / "fix.json"
    task_file.write_bytes(json.dumps(fix, ensure_ascii=False).encode("utf-8"))
    script = (
        f". '{REPO / 'scripts' / 'lib' / 'TeamQueue.ps1'}'; "
        f"$t = [System.IO.File]::ReadAllText('{task_file}', [System.Text.Encoding]::UTF8)"
        " | ConvertFrom-Json; "
        "Write-Output ('split=' + (Test-TeamSplitCandidate -Task $t)); "
        "Write-Output ('next=' + (Get-TeamNextRole -Task $t).Kind)"
    )
    done = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "split=True" in done.stdout, done.stdout + done.stderr
    assert "next=rest" in done.stdout, done.stdout + done.stderr


def test_a_second_decision_on_the_same_trial_is_409_and_opens_nothing(both, engine):
    client, store = both
    body = {"task_id": "uzak-hesap", "trial_id": "hesap", "verdict": "olmadi", "said": "olmadı"}
    assert client.post(DECISION, json=body).status_code == 200
    again = client.post(DECISION, json=body)
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["code"] == "already_decided"
    flipped = client.post(DECISION, json={**body, "verdict": "oldu"})
    assert flipped.status_code == 409
    assert [t for t in _tasks(store) if t.startswith("fix-")] == ["fix-uzak-hesap-1"]
    assert len(_events(engine)) == 1


def test_a_second_failed_trial_of_the_same_task_opens_the_next_fix_number(both):
    client, store = both
    for trial_id in ("hesap", "ofis-hesap"):
        answer = client.post(
            DECISION,
            json={"task_id": "uzak-hesap", "trial_id": trial_id, "verdict": "olmadi", "said": "x"},
        )
        assert answer.status_code == 200, answer.text
    assert sorted(t for t in _tasks(store) if t.startswith("fix-")) == [
        "fix-uzak-hesap-1",
        "fix-uzak-hesap-2",
    ]
    assert "reason" not in _tasks(store)["uzak-hesap"]  # not "oldu": nothing claimed


def test_a_fix_id_for_a_long_task_id_still_fits_the_schema():
    long_id = "a" * 64
    fix_id = trials.fix_task_id(long_id, {"version": 1, "tasks": []})
    assert fix_id.endswith("-1") and len(fix_id) <= 64
    assert team_store.task_problems(_task(fix_id, "approved")) == []


# ------------------------------------------------------------------ refusals


def test_an_unknown_trial_or_task_is_404(both):
    client, _ = both
    unknown_trial = client.post(
        DECISION, json={"task_id": "uzak-hesap", "trial_id": "yok", "verdict": "oldu"}
    )
    assert unknown_trial.status_code == 404
    assert unknown_trial.json()["detail"]["code"] == "unknown_trial"
    unknown_task = client.post(
        DECISION, json={"task_id": "yok-boyle", "trial_id": "hesap", "verdict": "oldu"}
    )
    assert unknown_task.status_code == 404
    assert unknown_task.json()["detail"]["code"] == "unknown_task"


def test_a_task_that_is_not_released_takes_no_trial_decision(both):
    client, _ = both
    for task_id, trial_id in (("bitti-a", "x"), ("calisan-b", "y")):
        refused = client.post(
            DECISION, json={"task_id": task_id, "trial_id": trial_id, "verdict": "oldu"}
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "not_on_trial"


def test_a_decision_is_taken_while_a_cycle_holds_the_lock_on_the_database_store(db, engine):
    client, store = db
    taken = store.acquire_lock(machine="MAIL", cycle_id="d20261003", pid=4242)
    assert taken["acquired"] is True
    assert client.get(APPROVALS).json()["cycle_running"] is True
    answer = client.post(
        DECISION,
        json={"task_id": "uzak-hesap", "trial_id": "hesap", "verdict": "olmadi", "said": "yok"},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["cycle_running"] is True
    assert "fix-uzak-hesap-1" in _tasks(store)
    lock = store.read_lock()
    assert (lock["held"], lock["pid"]) == (True, 4242)


def test_a_ledger_that_refuses_the_event_leaves_the_queue_untouched(engine, team_root):
    client, store = _wired("db", engine, team_root)  # the vocabulary as it is today
    refused = client.post(
        DECISION,
        json={"task_id": "uzak-hesap", "trial_id": "hesap", "verdict": "olmadi", "said": "yok"},
    )
    assert refused.status_code == 503, refused.text
    assert refused.json()["detail"]["code"] == "ledger_refused"
    assert _tasks(store)["uzak-hesap"]["owner_trials"] == TWO_TRIALS
    assert not [t for t in _tasks(store) if t.startswith("fix-")]


def test_without_an_owner_session_the_routes_are_401(engine, team_root):
    anonymous, _ = _wired("file", engine, team_root, owner=False)
    assert (
        anonymous.post(
            DECISION, json={"task_id": "uzak-hesap", "trial_id": "hesap", "verdict": "oldu"}
        ).status_code
        == 401
    )
    assert anonymous.get(APPROVALS).status_code == 401


# ------------------------------------------------------------------ the schema, both copies


def test_the_two_schema_copies_are_byte_identical():
    served = (REPO / "services" / "api" / "app" / "team" / "queue.schema.json").read_bytes()
    assert served == (REPO / "team" / "queue.schema.json").read_bytes()


def test_the_schema_accepts_the_object_form_and_the_old_string_form():
    as_objects = _task("yeni-bicim", "released", owner_trials=TWO_TRIALS)
    decided = _task(
        "karar",
        "awaiting_real_evidence",
        owner_trials=[_trial("a", "b", verdict="olmadi", said="olmadı", at=SEEN)],
    )
    as_strings = _task("eski-bicim", "awaiting_real_evidence", owner_trials=["Türkçe oku"])
    for task in (as_objects, decided, as_strings):
        assert team_store.task_problems(task) == [], task["id"]


def test_the_schema_refuses_a_malformed_trial_object():
    for bad in (
        {k: v for k, v in TWO_TRIALS[0].items() if k != "sentence"},
        {**TWO_TRIALS[0], "verdict": "belki"},
        {**TWO_TRIALS[0], "at": "dün"},
        {**TWO_TRIALS[0], "extra": 1},
        {**TWO_TRIALS[0], "sentence": ""},
    ):
        assert team_store.task_problems(_task("kotu", "released", owner_trials=[bad])) != [], bad


def test_the_schema_test_validator_knows_every_keyword_the_trial_uses():
    from tests.unit import test_team_queue_schema as schema_test

    schema = json.loads((REPO / "team" / "queue.schema.json").read_text(encoding="utf-8"))
    task = _task("yeni-bicim", "released", owner_trials=[*TWO_TRIALS, "eski"])
    assert schema_test.problems(task, schema["$defs"]["task"], schema) == []
