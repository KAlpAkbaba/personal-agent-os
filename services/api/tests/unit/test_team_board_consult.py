"""The board's consultation ('danisma') and the test queue's notes (owner, 2026-10-03).

"bir ajan diğerine desin ki: ben de şu anda şu iş var, ama şöyle mi ilerlesem sence yoksa şöyle
mi daha doğru olur - ki karşısındaki ajan da vereceği cevapta işi bilerek cevap versin."

A danisma carries the asker's situation, two or three named options, the asker's own lean and
at most five files; ``to`` is a seat or ``auto``. ``auto`` is routed by the server: the RUNNING
seat whose task's area shares files with the asker's, else the inspector for a test question,
else the lead - never the asker, never a seat that is not running. A cevap to a danisma names
the option it chose. A test-queue note (kind ``bilgi``) carries a ``slot`` snapshot of the line,
which the Ofis draws (``apps/web/app/core/office/officeBoard.ts``).

The PostgreSQL half is ``tests/integration/test_team_board_consult_pg.py``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.team import board
from app.team.models import TeamStateRow
from tests.identity_support import authenticate, install_identity

REPO = Path(__file__).resolve().parents[4]
NOTES = "/v1/team/board/notes"
NOW = datetime(2026, 10, 4, 9, 0, 0, tzinfo=UTC)
TOKEN_SHAPED = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"

ASKER_AREA = ["services/api/app/team/board.py", "scripts/lib/TeamBoard.ps1"]
SHARING_AREA = ["scripts/lib/TeamBoard.ps1", "scripts/team/feed.ps1"]
OTHER_AREA = ["apps/web/app/core/office/"]


def _consult(**fields: object) -> dict[str, object]:
    return {
        "seat": "worker-1",
        "task": "consult-asker",
        "kind": "danisma",
        "to": "auto",
        "situation": "board.py'ye danışma notunu ekliyorum; seçenekleri nerede doğrulayacağıma karar veriyorum.",
        "options": ["A: board.py içinde, saf fonksiyonla", "B: routes_board.py'de pydantic modeliyle"],
        "my_lean": "A: iki depo aynı kuralı kullanır",
        "files": ["services/api/app/team/board.py", "scripts/lib/TeamBoard.ps1", "scripts/team/board.ps1"],
        **fields,
    }


def _bilgi(seat: str, task: str, text: str = "başlıyorum") -> dict[str, object]:
    return {"seat": seat, "task": task, "kind": "bilgi", "text": text}


def _running(*runs: tuple[str, str], live: bool = True) -> board.Routing:
    """A routing context: (role, task) runs in start order over the three tasks' areas."""
    return board.Routing(
        live=live,
        runs=[{"role": role, "task": task} for role, task in runs],
        areas={
            "consult-asker": ASKER_AREA,
            "consult-sharer": SHARING_AREA,
            "consult-other": OTHER_AREA,
        },
    )


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    TeamStateRow.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture(params=["db", "file"])
def store(request, engine, tmp_path: Path) -> board.Board:
    if request.param == "db":
        return board.DbBoard(sessionmaker(bind=engine, expire_on_commit=False))
    return board.FileBoard(tmp_path / "team")


def _seat_the_runs(store: board.Board) -> None:
    """Each worker run says hello first: that note is how the board knows a run's seat."""
    store.post(_bilgi("worker-1", "consult-asker"), now=NOW - timedelta(minutes=9))
    store.post(_bilgi("worker-2", "consult-other"), now=NOW - timedelta(minutes=8))
    store.post(_bilgi("worker-3", "consult-sharer"), now=NOW - timedelta(minutes=7))


def _refused(call) -> board.Refused:
    with pytest.raises(board.Refused) as caught:
        call()
    return caught.value


# ------------------------------------------------------------------ the danisma's fields


def test_a_danisma_is_kept_with_its_situation_options_lean_and_files(store) -> None:
    note = store.post(_consult(to="worker-2"), now=NOW)
    assert note["kind"] == "danisma" and note["to"] == "worker-2"
    assert note["options"] == ["A: board.py içinde, saf fonksiyonla", "B: routes_board.py'de pydantic modeliyle"]
    assert note["my_lean"].startswith("A:")
    assert note["files"][0] == "services/api/app/team/board.py"
    assert note["text"] == note["situation"]  # the line every reader already prints
    assert store.read(now=NOW) == [note]


def test_options_are_named_A_B_C_by_their_place(store) -> None:
    note = store.post(_consult(to="worker-2", options=["tek tablo", "B: iki tablo", "üç tablo"]), now=NOW)
    assert note["options"] == ["A: tek tablo", "B: iki tablo", "C: üç tablo"]


@pytest.mark.parametrize(
    "fields",
    [
        {"options": ["A: yalnız bir seçenek"]},
        {"options": ["A: bir", "B: iki", "C: üç", "D: dört"]},
        {"options": []},
        {"options": "A: liste değil"},
        {"options": ["A: " + "x" * 198]},
        {"options": ["A: bir", "B: " + "y" * 198]},
        {"options": ["A: bir", "C: sırası yanlış"]},
        {"situation": "s" * 401},
        {"situation": "  "},
        {"my_lean": "l" * 201},
        {"files": ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"]},
        {"files": ["../dışarı.py"]},
        {"files": ["C:/Windows/x.ps1"]},
        {"files": ["/etc/passwd"]},
        {"files": "a.py"},
        {"to": "herkes"},
        {"to": "patron"},
        {"to": "worker-1"},  # the asker itself
        {"topic": "emir"},
        {"text": "x" * 281},
        {"choice": "A"},
        {"situation": f"anahtar {TOKEN_SHAPED}"},
        {"options": ["A: bir", f"B: {TOKEN_SHAPED}"]},
    ],
)
def test_a_danisma_that_breaks_a_rule_is_a_422(store, fields) -> None:
    refused = _refused(lambda: store.post(_consult(**fields), now=NOW))
    assert refused.status == 422, refused.problems
    assert store.read(now=NOW) == []


def test_two_and_three_options_pass_one_and_four_do_not(store) -> None:
    assert len(store.post(_consult(to="worker-2"), now=NOW)["options"]) == 2
    three = _consult(to="worker-2", options=["A: bir", "B: iki", "C: üç"])
    assert len(store.post(three, now=NOW + timedelta(seconds=1))["options"]) == 3
    for options in (["A: bir"], ["A: bir", "B: iki", "C: üç", "D: dört"]):
        refused = _refused(lambda o=options: store.post(_consult(options=o), now=NOW))
        assert refused.status == 422 and any("2 or 3 options" in p for p in refused.problems)


def test_auto_and_the_consult_fields_are_a_danismas_only(store) -> None:
    plain = {"seat": "worker-1", "task": "consult-asker", "kind": "soru", "text": "?"}
    for fields in ({"to": "auto"}, {"options": ["A: x", "B: y"]}, {"situation": "s"}, {"files": ["a.py"]}):
        refused = _refused(lambda f=fields: store.post({**plain, **f}, now=NOW))
        assert refused.status == 422, (fields, refused.problems)


# ------------------------------------------------------------------ routing -To auto


def test_auto_goes_to_the_running_seat_whose_area_shares_a_file(store) -> None:
    _seat_the_runs(store)
    routing = _running(("worker", "consult-asker"), ("worker", "consult-other"), ("worker", "consult-sharer"))
    note = store.post(_consult(), now=NOW, routing=routing)
    assert note["to"] == "worker-3", note
    assert "scripts/lib/teamboard.ps1" in note["route"].lower()


def test_auto_never_picks_the_asker_even_when_its_own_area_matches_best(store) -> None:
    _seat_the_runs(store)
    # Two runs on the asker's own task (a re-run): the asker is never its own answerer.
    routing = _running(("worker", "consult-asker"), ("worker", "consult-other"))
    note = store.post(_consult(), now=NOW, routing=routing)
    assert note["to"] == "lead" and note["to"] != "worker-1"


def test_auto_never_picks_a_seat_that_is_not_running(store) -> None:
    _seat_the_runs(store)  # worker-3 said hello on consult-sharer ...
    routing = _running(("worker", "consult-asker"), ("worker", "consult-other"))  # ... and has ended
    note = store.post(_consult(), now=NOW, routing=routing)
    assert note["to"] == "lead"
    # no live cycle at all: nobody runs, not even the lead - the note goes to everyone
    idle = store.post(_consult(), now=NOW + timedelta(seconds=1), routing=_running(live=False))
    assert idle["to"] == "herkes" and "çalışan" in idle["route"]


def test_a_test_question_goes_to_the_running_inspector_when_no_area_is_shared(store) -> None:
    _seat_the_runs(store)
    routing = _running(("worker", "consult-asker"), ("worker", "consult-other"), ("inspector", "consult-other"))
    note = store.post(_consult(topic="test", files=["services/api/app/team/board.py"]), now=NOW, routing=routing)
    assert note["to"] == "inspector"
    no_inspector = _running(("worker", "consult-asker"), ("worker", "consult-other"))
    later = store.post(_consult(topic="test"), now=NOW + timedelta(seconds=1), routing=no_inspector)
    assert later["to"] == "lead"


def test_a_question_on_the_owners_rules_or_outside_both_areas_goes_to_the_lead(store) -> None:
    _seat_the_runs(store)
    routing = _running(("worker", "consult-asker"), ("worker", "consult-sharer"))
    rule = store.post(_consult(topic="kural"), now=NOW, routing=routing)
    assert rule["to"] == "lead"
    outside = store.post(
        _consult(files=["docs/HANDOFF.md"]), now=NOW + timedelta(seconds=1), routing=routing
    )
    assert outside["to"] == "lead" and "alan" in outside["route"]


def test_the_pure_router_on_its_own() -> None:
    seats = [
        board.RunningSeat("worker-1", "consult-asker", ASKER_AREA),
        board.RunningSeat("worker-2", "consult-other", OTHER_AREA),
        board.RunningSeat("worker-3", "consult-sharer", SHARING_AREA),
        board.RunningSeat("lead", "", []),
    ]
    seat, _ = board.route_auto("worker-1", ASKER_AREA, ["scripts/lib/TeamBoard.ps1"], "kod", seats)
    assert seat == "worker-3"
    # a directory in an area holds the files under it
    seat, _ = board.route_auto("worker-1", ["apps/web/app/core/office/officeBoard.ts"], [], "kod", seats)
    assert seat == "worker-2"
    seat, _ = board.route_auto("worker-3", SHARING_AREA, [], "kod", seats)
    assert seat == "worker-1"
    seat, _ = board.route_auto("lead", [], [], "kod", seats)
    assert seat == "herkes"  # the lead asking finds no one to send itself to


# ------------------------------------------------------------------ the cevap


def test_a_cevap_to_a_danisma_names_an_option_and_a_reason(store) -> None:
    asked = store.post(_consult(to="worker-2"), now=NOW)
    answer = store.post(
        {
            "seat": "worker-2",
            "task": "consult-other",
            "kind": "cevap",
            "to": "worker-1",
            "reply_to": asked["id"],
            "choice": "B",
            "text": "B: pydantic hatası 422'yi kendisi verir, iki depo aynı modeli okur.",
        },
        now=NOW + timedelta(seconds=5),
    )
    assert answer["choice"] == "B" and answer["reply_to"] == asked["id"]
    other = store.post(
        {
            "seat": "worker-3",
            "task": "consult-sharer",
            "kind": "cevap",
            "reply_to": asked["id"],
            "choice": "başka: ikisi de değil, şema dosyası",
            "text": "Şema dosyası iki tarafa da yeter.",
        },
        now=NOW + timedelta(seconds=6),
    )
    assert other["choice"].startswith("başka:")


@pytest.mark.parametrize("choice", ["", "C", "D", "a", "başka:", "başka:   ", "x" * 10])
def test_a_cevap_to_a_danisma_with_no_or_a_wrong_choice_is_a_422(store, choice) -> None:
    asked = store.post(_consult(to="worker-2"), now=NOW)
    body = {
        "seat": "worker-2",
        "task": "consult-other",
        "kind": "cevap",
        "reply_to": asked["id"],
        "text": "neden",
    }
    if choice:
        body["choice"] = choice
    refused = _refused(lambda: store.post(body, now=NOW + timedelta(seconds=1)))
    assert refused.status == 422, refused.problems


def test_a_choice_only_answers_a_danisma(store) -> None:
    asked = store.post({"seat": "worker-1", "task": "consult-asker", "kind": "soru", "text": "?"}, now=NOW)
    body = {"seat": "worker-2", "task": "consult-other", "kind": "cevap", "reply_to": asked["id"], "choice": "A", "text": "x"}
    assert _refused(lambda: store.post(body, now=NOW + timedelta(seconds=1))).status == 422


def test_read_can_ask_for_the_answers_to_one_note(store) -> None:
    asked = store.post(_consult(to="worker-2"), now=NOW)
    store.post(_bilgi("worker-3", "consult-sharer"), now=NOW + timedelta(seconds=1))
    answer = store.post(
        {"seat": "worker-2", "task": "consult-other", "kind": "cevap", "reply_to": asked["id"], "choice": "A", "text": "A."},
        now=NOW + timedelta(seconds=2),
    )
    assert store.read(reply_to=asked["id"]) == [answer]
    assert _refused(lambda: store.read(reply_to="bir şey")).status == 422


# ------------------------------------------------------------------ the test queue's snapshot


def _slot(**fields: object) -> dict[str, object]:
    return {"state": "take", "kinds": ["heavy"], "holders": ["worker-2"], "waiting": ["worker-3", "gate"], **fields}


def test_a_test_queue_note_carries_the_lines_snapshot(store) -> None:
    note = store.post({**_bilgi("worker-2", "consult-other", "Çalışan 2: birim testlerini başlatıyorum (ağır), tahmini 6 dk"), "slot": _slot(estimate_min=6)}, now=NOW)
    assert note["slot"]["holders"] == ["worker-2"] and note["slot"]["waiting"] == ["worker-3", "gate"]
    assert note["slot"]["estimate_min"] == 6


@pytest.mark.parametrize(
    "slot",
    [
        "take",
        _slot(state="dans"),
        _slot(kinds=["gpu"]),
        _slot(holders=["Robert'); DROP"]),
        _slot(waiting=[f"worker-{n % 9 + 1}" for n in range(21)]),
        _slot(estimate_min=-1),
        _slot(extra=1),
    ],
)
def test_a_slot_that_breaks_a_rule_is_a_422(store, slot) -> None:
    body = {**_bilgi("worker-2", "consult-other"), "slot": slot}
    assert _refused(lambda: store.post(body, now=NOW)).status == 422


def test_a_slot_is_a_bilgis_only(store) -> None:
    body = {"seat": "worker-2", "task": "consult-other", "kind": "soru", "text": "?", "slot": _slot()}
    assert _refused(lambda: store.post(body, now=NOW)).status == 422


# ------------------------------------------------------------------ the routes


@pytest.fixture()
def team_root(tmp_path: Path) -> Path:
    root = tmp_path / "team"
    root.mkdir()
    tasks = [
        {
            "id": "consult-asker",
            "title": "Danışma notu",
            "state": "in_progress",
            "area": ASKER_AREA,
            "branch": "team/d20261004/worker-consult-asker",
            "goal": "İlk satır: ajanlar birbirine danışır.\nİkinci satır: bağlamla.\nÜçüncü satır.",
            "acceptance": "Kabul 1: 422 bir ya da dört seçenek.\nKabul 2: auto yönlendirme.",
        },
        {"id": "consult-sharer", "title": "Paylaşan", "state": "in_progress", "area": SHARING_AREA},
        {"id": "consult-other", "title": "Öteki", "state": "in_progress", "area": OTHER_AREA},
    ]
    (root / "queue.json").write_text(json.dumps({"version": 1, "tasks": tasks}), encoding="utf-8")
    stamp = board.stamp(board.utcnow())
    (root / "lock.json").write_text(
        json.dumps({"held": True, "machine": "PC", "cycle_id": "c1", "pid": 1, "acquired_at": stamp}),
        encoding="utf-8",
    )
    (root / "status.json").write_text(
        json.dumps(
            {
                "cycle_id": "c1",
                "runs": [
                    {"task": "consult-asker", "role": "worker", "started_at": stamp},
                    {"task": "consult-other", "role": "worker", "started_at": stamp},
                    {"task": "consult-sharer", "role": "worker", "started_at": stamp},
                ],
                "updated_at": stamp,
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def client(team_root: Path) -> TestClient:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_root = team_root
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    return test_client


def test_the_route_routes_auto_from_the_live_status_and_the_queues_areas(client: TestClient) -> None:
    for seat, task in (("worker-1", "consult-asker"), ("worker-2", "consult-other"), ("worker-3", "consult-sharer")):
        assert client.post(NOTES, json=_bilgi(seat, task)).status_code == 200
    posted = client.post(NOTES, json=_consult())
    assert posted.status_code == 200, posted.text
    assert posted.json()["note"]["to"] == "worker-3"


@pytest.mark.parametrize("count", [1, 4])
def test_the_route_answers_422_for_one_or_four_options(client: TestClient, count: int) -> None:
    options = [f"{letter}: seçenek" for letter in "ABCD"[:count]]
    answer = client.post(NOTES, json=_consult(options=options))
    assert answer.status_code == 422, answer.text
    assert answer.json()["detail"]["code"] == "invalid"


def test_read_carries_the_askers_card_for_every_danisma(client: TestClient) -> None:
    client.post(NOTES, json=_bilgi("worker-2", "consult-other"))
    asked = client.post(NOTES, json=_consult(to="worker-2")).json()["note"]
    read = client.get(NOTES).json()
    card = read["cards"]["consult-asker"]
    assert card["title"] == "Danışma notu"
    assert card["goal"] == ["İlk satır: ajanlar birbirine danışır.", "İkinci satır: bağlamla."]
    assert card["acceptance"][0].startswith("Kabul 1")
    assert "consult-other" not in read["cards"]  # only the askers' cards
    answers = client.get(NOTES, params={"reply_to": asked["id"]}).json()["notes"]
    assert answers == []


def test_context_gives_the_note_the_askers_card_branch_and_answers(client: TestClient) -> None:
    asked = client.post(NOTES, json=_consult(to="worker-2")).json()["note"]
    client.post(
        NOTES,
        json={"seat": "worker-2", "task": "consult-other", "kind": "cevap", "reply_to": asked["id"], "choice": "B", "text": "B daha iyi."},
    )
    context = client.get(f"{NOTES}/{asked['id']}/context")
    assert context.status_code == 200, context.text
    body = context.json()
    assert body["note"] == asked
    assert body["card"]["branch"] == "team/d20261004/worker-consult-asker"
    assert body["card"]["area"] == ASKER_AREA
    assert [a["choice"] for a in body["answers"]] == ["B"]
    assert client.get(f"{NOTES}/n-20261004T090000000000Z-00000000/context").status_code == 404
    assert client.get(f"{NOTES}/not-an-id/context").status_code == 422


def test_routing_degrades_to_everyone_when_the_store_cannot_be_read(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_root = tmp_path / "no-team"  # no queue, no status, no lock
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    posted = client.post(NOTES, json=_consult())
    assert posted.status_code == 200, posted.text
    assert posted.json()["note"]["to"] == "herkes"


# ------------------------------------------------------------------ the other half: the client


def _client_source() -> str:
    return (REPO / "scripts" / "lib" / "TeamBoard.ps1").read_text(encoding="utf-8")


def test_the_powershell_client_knows_the_consult_bounds() -> None:
    source = _client_source()
    for name, value in (
        ("TeamBoardSituationMax", board.SITUATION_MAX_CHARS),
        ("TeamBoardOptionMax", board.OPTION_MAX_CHARS),
        ("TeamBoardLeanMax", board.LEAN_MAX_CHARS),
        ("TeamBoardFilesMax", board.FILES_MAX),
        ("TeamBoardWaitMinutesMax", board.WAIT_MINUTES_MAX),
    ):
        found = re.search(rf"\$script:{name}\s*=\s*(\d+)", source)
        assert found is not None and int(found.group(1)) == value, name
    topics = re.search(r"\$script:TeamBoardTopics\s*=\s*@\(([^)]*)\)", source)
    assert topics is not None and tuple(re.findall(r'"([^"]+)"', topics.group(1))) == board.TOPICS
    for field in ("situation", "options", "my_lean", "files", "topic", "choice", "slot"):
        assert re.search(rf"\b{field}\s*=", source), field
