"""The İlerleme strip's proof (proof-from-test-rounds-and-trials): the owner, 2026-10-06, "kanıt
kısmı neden ilerlemiyor, o da mı her döngüde gelişiyor". The v1.0 matrix's PROOF column was last
written on 2026-09-19, so its share never moved. The JARVIS rows now carry two proofs that move
with real evidence: a test round that passed on staging on the current release ("staging'de
kanıtlı") and an owner trial that passed on production ("gerçekte kanıtlı").
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.team import progress
from app.team import store as team_store
from tests.identity_support import authenticate, install_identity

REPO = Path(__file__).resolve().parents[4]
SHA = "a5e68d92d9271ececec51da01b713e43a394e28c"
OLD = "1111111111111111111111111111111111111111"

JARVIS = """\
### What JARVIS does, and where this system stands (2026-09-27)

| JARVIS | PersonalAgentOS today | State |
|---|---|---|
| Talks | voice | **HAVE** — quality work remains |
| Everywhere | two PCs | **PARTIAL** (2026-09-29) — two PCs |
| Runs the house: lights, doors | Home Assistant | **MISSING** — adopt |
| **Records everything** — "her şeyi" | ledger | **PARTIAL** — its own line |
| Flies the suit | — | **NEVER / HARDWARE** — see the limits |

### The limits, stated once
"""


def _jarvis():
    return progress.parse_jarvis(JARVIS)


def _round(name, sha, at, rows):
    return {
        "round": name,
        "staging_sha": sha,
        "at": at,
        "rows": [{"row": r, "passed": p, "failed": f} for r, p, f in rows],
    }


def _trial_task(row, verdict, at, n=1):
    return {
        "id": f"task-{n}",
        "roadmap_row": row,
        "state": "awaiting_real_evidence",
        "owner_trials": [
            "an old plain sentence",
            {
                "id": f"deneme-{n}",
                "sentence": "s",
                "machine": "m",
                "expect": "e",
                "verdict": verdict,
                "said": None,
                "at": at,
            },
        ],
    }


def _row(answer, name):
    return next(r for r in answer["rows"] if r["name"] == name)


def _real_jarvis():
    return progress.parse_jarvis((REPO / "docs" / "ROADMAP.md").read_bytes().decode("utf-8"))


def _real_name(leading):
    """The repository roadmap's JARVIS row that starts with ``leading``."""
    return next(r["name"] for r in _real_jarvis()["rows"] if r["name"].startswith(leading))


# ------------------------------------------------------------------ the computation


def test_a_row_whose_latest_round_on_the_current_release_passed_is_staging_proven():
    rounds = [_round("t1", SHA, "2026-10-06T20:00:00Z", [("Talks", 3, 0)])]
    answer = progress.proof(_jarvis(), rounds, {"tasks": []}, release=SHA)
    talks = _row(answer, "Talks")
    assert talks["staging_proven"] is True
    assert talks["staging"]["round"] == "t1"
    assert talks["real_proven"] is False
    assert answer["counted"] == 4  # the NEVER row is not a goal
    assert answer["staging_proven"] == 1
    assert answer["percent_staging"] == 25
    assert answer["percent_real"] == 0


def test_a_row_whose_latest_round_failed_is_not_staging_proven():
    rounds = [
        _round("t1", SHA, "2026-10-06T20:00:00Z", [("Talks", 3, 0)]),
        _round("t2", SHA, "2026-10-06T22:00:00Z", [("Talks", 2, 1)]),
    ]
    answer = progress.proof(_jarvis(), rounds, {"tasks": []}, release=SHA)
    talks = _row(answer, "Talks")
    assert talks["staging_proven"] is False
    assert talks["staging"]["round"] == "t2"
    assert answer["percent_staging"] == 0


def test_a_round_on_another_release_does_not_count_and_a_short_sha_matches():
    rounds = [
        _round("t1", OLD, "2026-10-06T23:00:00Z", [("Everywhere", 1, 0)]),
        _round("t2", SHA[:12], "2026-10-06T20:00:00Z", [("Talks", 1, 0)]),
    ]
    answer = progress.proof(_jarvis(), rounds, {"tasks": []}, release=SHA)
    assert _row(answer, "Everywhere")["staging_proven"] is False
    assert _row(answer, "Everywhere")["staging"] is None
    assert _row(answer, "Talks")["staging_proven"] is True


def test_a_round_reaches_its_row_by_the_row_name_or_its_leading_words():
    rounds = [
        _round(
            "t1",
            SHA,
            "2026-10-06T20:00:00Z",
            [("records everything", 1, 0), ("Runs the house", 2, 0), ("Talk", 1, 0)],
        ),
    ]
    answer = progress.proof(_jarvis(), rounds, {"tasks": []}, release=SHA)
    assert _row(answer, 'Records everything — "her şeyi"')["staging_proven"] is True
    assert _row(answer, "Runs the house: lights, doors")["staging_proven"] is True
    # "Talk" is not a word of "Talks": no row is proven by half a word.
    assert _row(answer, "Talks")["staging_proven"] is False


def test_one_or_two_leading_words_name_no_row_three_do():
    """The return of 2026-10-06 (d): "The" or "Records" is the start of many sentences."""
    names = [r["name"] for r in _real_jarvis()["rows"] if r["state"] != "never"]
    for ref in ("The", "Records", "Records everything and", "Runs", "Runs the", "Knows"):
        if ref == "Records everything and":
            assert progress.resolve_row(ref, names) == _real_name("Records everything and tells")
        else:
            assert progress.resolve_row(ref, names) is None, ref
    rounds = [_round("t1", SHA, "2026-10-06T20:00:00Z", [("The", 1, 0), ("Runs", 1, 0)])]
    answer = progress.proof(_real_jarvis(), rounds, {"tasks": []}, release=SHA)
    assert answer["staging_proven"] == 0
    assert answer["outside"] == {"count": 2, "unknown": ["Runs", "The"]}


@pytest.mark.parametrize(
    ("ref", "leading"),
    [
        ("browser-use, anywhere (order 2b, ADR-0213)", "Researches anything"),
        ("Records everything and tells him, whenever he asks (order 2c)", "Records everything"),
        ("Records everything (order 2c)", "Records everything"),
        ("Repairs and improves itself (kept controlled)", "Repairs and improves itself"),
        ("Always-listening natural conversation, interruptible, in the owner's language", "Always"),
        ("The same JARVIS in the house, the car, the suit, the phone", "The same JARVIS"),
    ],
)
def test_the_queues_wordings_reach_their_jarvis_row_through_the_table(ref, leading):
    names = [r["name"] for r in _real_jarvis()["rows"] if r["state"] != "never"]
    assert progress.resolve_row(ref, names) == _real_name(leading)
    assert not progress.is_outside(ref)


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "How it is built from here",
        "How it is built from here (TEAM_PROTOCOL 3a)",
        "How it is built from here (TEAM_PROTOCOL 3a.5)",
        "TEAM_PROTOCOL 3a",
        "Runs the workshop by voice: machines, files, fabrication",
    ],
)
def test_a_wording_that_is_no_jarvis_row_is_left_out_and_counted(ref):
    names = [r["name"] for r in _real_jarvis()["rows"] if r["state"] != "never"]
    assert progress.resolve_row(ref, names) is None
    assert progress.is_outside(ref)
    queue = {"tasks": [_trial_task(ref, "oldu", "2026-10-06T18:00:00Z")]}
    answer = progress.proof(_real_jarvis(), [], queue, release=SHA)
    assert answer["real_proven"] == 0
    assert answer["outside"] == {"count": 1, "unknown": []}


def test_every_roadmap_row_in_the_queue_names_a_row_or_is_declared_outside():
    """A card's wording the table does not know would fall out of the proof unseen."""
    names = [r["name"] for r in _real_jarvis()["rows"] if r["state"] != "never"]
    queue = json.loads((REPO / "team" / "queue.json").read_bytes().decode("utf-8"))
    wordings = {str(t.get("roadmap_row", "")) for t in queue["tasks"]}
    unknown = sorted(
        w for w in wordings if progress.resolve_row(w, names) is None and not progress.is_outside(w)
    )
    assert unknown == []


def test_an_owner_trial_that_passed_makes_its_row_real_proven():
    queue = {"tasks": [_trial_task("Everywhere - the second PC", "oldu", "2026-10-06T18:00:00Z")]}
    answer = progress.proof(_jarvis(), [], queue, release=SHA)
    everywhere = _row(answer, "Everywhere")
    assert everywhere["real_proven"] is True
    assert everywhere["trial"]["task_id"] == "task-1"
    assert everywhere["staging_proven"] is False
    assert answer["real_proven"] == 1
    assert answer["percent_real"] == 25


def test_a_later_failed_trial_takes_the_real_proof_back_and_an_open_one_is_not_a_proof():
    queue = {
        "tasks": [
            _trial_task("Everywhere", "oldu", "2026-10-06T18:00:00Z", n=1),
            _trial_task("Everywhere", "olmadi", "2026-10-06T19:00:00Z", n=2),
            _trial_task("Talks", None, None, n=3),
        ]
    }
    answer = progress.proof(_jarvis(), [], queue, release=SHA)
    assert _row(answer, "Everywhere")["real_proven"] is False
    assert _row(answer, "Talks")["real_proven"] is False
    assert answer["real_proven"] == 0


def test_without_a_release_the_latest_round_counts_and_no_table_is_no_proof():
    rounds = [_round("t1", OLD, "2026-10-06T20:00:00Z", [("Talks", 1, 0)])]
    assert _row(progress.proof(_jarvis(), rounds, {}, release=None), "Talks")["staging_proven"]
    assert progress.proof(None, rounds, {}, release=SHA) is None


def test_progress_carries_the_proof_when_rounds_and_queue_are_given(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ROADMAP.md").write_text(JARVIS, encoding="utf-8")
    rounds = [_round("t1", SHA, "2026-10-06T20:00:00Z", [("Talks", 1, 0)])]
    answer = progress.progress(tmp_path, as_of=SHA, rounds=rounds, queue={"tasks": []})
    assert answer["proof"]["staging_proven"] == 1
    assert progress.progress(tmp_path, as_of=SHA)["proof"]["staging_proven"] == 0


# ------------------------------------------------------------------ what a round may post


def test_a_well_formed_round_has_no_problems():
    doc = _round("t202610062130", SHA, "2026-10-06T21:30:00Z", [("Talks", 3, 0)])
    assert progress.round_problems(doc) == []


@pytest.mark.parametrize(
    "change",
    [
        {"round": "Bad Round"},
        {"staging_sha": "not-a-sha"},
        {"staging_sha": ""},
        {"at": "yesterday"},
        {"rows": []},
        {"rows": [{"row": "", "passed": 1, "failed": 0}]},
        {"rows": [{"row": "Talks", "passed": 0, "failed": 0}]},
        {"rows": [{"row": "Talks", "passed": -1, "failed": 2}]},
        {"rows": [{"row": "Talks", "passed": True, "failed": 0}]},
        {"extra": 1},
    ],
)
def test_a_malformed_round_is_refused(change):
    doc = _round("t1", SHA, "2026-10-06T21:30:00Z", [("Talks", 3, 0)]) | change
    assert progress.round_problems(doc) != []


# ------------------------------------------------------------------ the round posts it


class _FakeApi(BaseHTTPRequestHandler):
    received: list[tuple[str, str, str, dict]] = []

    def do_POST(self):  # noqa: N802 - the http.server name
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        _FakeApi.received.append(("POST", self.path, self.headers.get("Authorization", ""), body))
        out = json.dumps({"stored": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):  # quiet
        pass


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    candidate = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return str(candidate) if candidate.is_file() else None


def _write_json(path: Path, document) -> None:
    path.write_bytes(json.dumps(document, ensure_ascii=False).encode("utf-8"))


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is not on this machine")
def test_the_round_posts_each_rows_passed_and_failed_scenarios_to_the_api(tmp_path):
    out_root = tmp_path / "testteam"
    round_dir = out_root / "t202610062130"
    round_dir.mkdir(parents=True)
    plan = round_dir / "plan.json"
    _write_json(
        plan,
        {
            "jobs": [
                {"family": "nobet", "scenario": "s/watches.json", "roadmap_row": "Talks"},
                {"family": "saglik", "scenario": "s/health.json", "roadmap_row": "Talks"},
                {"family": "ev", "scenario": "s/house.json", "roadmap_row": "Runs the house"},
                {"family": "serbest", "scenario": "s/free.json"},
                {"family": "neden", "scenario": "s/why.json", "why": "Repairs and improves itself"},
                {"family": "bekleyen", "scenario": "s/wait.json", "roadmap_row": "Everywhere"},
            ]
        },
    )
    cards = [
        {"id": "c1", "family": "nobet", "state": "passed"},
        {"id": "c2", "family": "saglik", "state": "passed"},
        {"id": "c3", "family": "ev", "state": "broke"},
        {"id": "c4", "family": "serbest", "state": "passed"},
        {"id": "c5", "family": "bekleyen", "state": "planned"},
        {"id": "c6", "family": "neden", "state": "failed"},
    ]
    _write_json(
        round_dir / "cards.json", {"round": "t202610062130", "plan": str(plan), "cards": cards}
    )
    for card in cards[:4] + cards[5:]:
        _write_json(
            round_dir / f"{card['id']}.result.json", {"state": card["state"], "staging_sha": SHA}
        )
    token = tmp_path / "token.txt"
    token.write_text("fake-queue-token", encoding="utf-8")

    _FakeApi.received = []
    server = HTTPServer(("127.0.0.1", 0), _FakeApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        done = subprocess.run(
            [
                str(_powershell()),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(REPO / "scripts" / "testteam" / "test-round.ps1"),
                "-PostProof",
                "-Round",
                "t202610062130",
                "-OutRoot",
                str(out_root),
                "-NoBoard",
                "-QueueUrl",
                f"http://127.0.0.1:{server.server_port}",
                "-QueueToken",
                str(token),
            ],
            capture_output=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
    finally:
        server.shutdown()
    output = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    assert done.returncode == 0, output
    assert len(_FakeApi.received) == 1, output
    method, path, auth, body = _FakeApi.received[0]
    assert (method, path, auth) == ("POST", "/v1/team/queue/proof", "Bearer fake-queue-token")
    assert body["round"] == "t202610062130"
    assert body["staging_sha"] == SHA
    assert progress.round_problems(body) == []
    rows = {r["row"]: (r["passed"], r["failed"]) for r in body["rows"]}
    # A job with no row sends its why (or its family) for the Core to resolve; a card that
    # never ran is neither passed nor failed.
    assert rows == {
        "Talks": (2, 0),
        "Runs the house": (0, 1),
        "serbest": (1, 0),
        "Repairs and improves itself": (0, 1),
    }


# ------------------------------------------------------------------ the office answer


@pytest.fixture()
def owner(tmp_path):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    root = tmp_path / "team"
    (root / "reports").mkdir(parents=True)
    (root / "queue.json").write_text('{"version": 1, "tasks": []}\n', encoding="utf-8")
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    app.state.team_store = team_store.FileStore(root)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    return client


def test_a_posted_round_reaches_the_office_strip(owner):
    """The wiring: POST /v1/team/queue/proof keeps the round in the store and the office's
    progress reads it back (routes.py + store.py - outside this card's area)."""
    doc = _round("t1", SHA, "2026-10-06T20:00:00Z", [("Repairs and improves itself", 2, 0)])
    stored = owner.post("/v1/team/queue/proof", json=doc)
    assert stored.status_code == 200, stored.text
    proof = owner.get("/v1/team/office").json()["progress"]["proof"]
    assert _row(proof, "Repairs and improves itself")["staging_proven"] is True
    refused = owner.post("/v1/team/queue/proof", json=doc | {"staging_sha": "x"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "invalid"
    assert owner.post("/v1/team/queue/proof", json=[doc]).status_code == 422


def test_the_office_strip_reads_the_queues_trials_and_counts_the_wordings_left_out(owner):
    task = _trial_task(
        "Repairs and improves itself (kept controlled)", "oldu", "2026-10-06T18:00:00Z"
    )
    task |= {"title": "t", "state": "done", "updated_at": "2026-10-06T18:00:00Z"}
    queue = owner.app.state.team_store.read_queue()
    queue["tasks"] = [
        task,
        _trial_task("How it is built from here (TEAM_PROTOCOL 3a)", None, None, n=2),
    ]
    owner.app.state.team_store._write(owner.app.state.team_store.root / "queue.json", queue)
    proof = owner.get("/v1/team/office").json()["progress"]["proof"]
    assert _row(proof, "Repairs and improves itself")["real_proven"] is True
    assert proof["outside"] == {"count": 1, "unknown": []}


def test_a_proof_without_the_store_method_leaves_the_office_open(owner):
    class _Older(team_store.FileStore):
        read_proofs = None

    owner.app.state.team_store = _Older(owner.app.state.team_store.root)
    answer = owner.get("/v1/team/office")
    assert answer.status_code == 200
    assert answer.json()["progress"]["proof"]["staging_proven"] == 0


# ------------------------------------------------------------------ the file store


def test_the_file_store_keeps_a_round_replaces_it_and_reads_the_newest_first(tmp_path):
    store = team_store.FileStore(tmp_path)
    assert store.read_proofs() == []
    first = _round("t1", SHA, "2026-10-06T20:00:00Z", [("Talks", 1, 0)])
    store.put_proof(first)
    store.put_proof(_round("t2", SHA, "2026-10-06T21:00:00Z", [("Talks", 0, 1)]))
    store.put_proof(first | {"rows": [{"row": "Talks", "passed": 4, "failed": 0}]})
    proofs = store.read_proofs()
    assert [p["round"] for p in proofs] == ["t2", "t1"]
    assert proofs[1]["rows"][0]["passed"] == 4


@pytest.mark.parametrize(
    "change",
    [
        {"round": "con"},
        {"rows": [{"row": "Talks\x00", "passed": 1, "failed": 0}]},
        {"rows": [{"row": "Talks", "passed": 1, "failed": 0, "families": ["a\x00"]}]},
        {"staging_sha": "x"},
    ],
)
def test_the_file_store_refuses_what_a_store_cannot_keep(tmp_path, change):
    store = team_store.FileStore(tmp_path)
    with pytest.raises(team_store.Invalid):
        store.put_proof(_round("t1", SHA, "2026-10-06T20:00:00Z", [("Talks", 1, 0)]) | change)
    assert store.read_proofs() == []
