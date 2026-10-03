"""The team's board (``/v1/team/board/notes``): short Turkish notes between the seats of a cycle.

The owner's idea of 2026-10-03: the runs of a cycle trade a word while they work, as people in
an office do. A note is information, never an instruction; the server bounds it - 280
characters, a seat the team knows, one of four kinds, twenty notes per task per hour, no
token-shaped text - and the board keeps the last 500 notes of the last seven days, pruned on
the write that goes past either bound.

Both stores are held to one set of rules: the database (``team_state`` rows of kind ``note``;
the PostgreSQL half is ``tests/integration/test_team_board_pg.py``) and the file next to the
queue. ``scripts/lib/TeamBoard.ps1`` is the other half of the contract; the last tests read it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.team import board
from app.team.models import TeamStateRow
from app.team.routes_board import router as board_router
from app.team.store import DbStore
from tests.identity_support import authenticate, install_identity

REPO = Path(__file__).resolve().parents[4]
NOTES = "/v1/team/board/notes"
NOW = datetime(2026, 10, 3, 9, 0, 0, tzinfo=UTC)
#: A GitHub token's shape, built so that this file holds none.
TOKEN_SHAPED = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
#: This system's own session token (the team token) and owner credential, built the same way:
#: ``app/identity/tokens.py`` prefix + ``secrets.token_urlsafe(32)`` (43 characters).
SESSION_SHAPED = "pagentos" + "_st_" + "Zq3-Xv9_Lm2Pk7Rt5Wn8Yb4Hc6Jd1Fg0Ks2Qw3Ee4Rr5T"
OWNER_SHAPED = "pagentos" + "_ok_" + "Ab1_Cd2-Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3Ab4C"


def _note(**fields: object) -> dict[str, object]:
    return {
        "seat": "worker-1",
        "task": "team-board",
        "kind": "bilgi",
        "text": "team-board üzerinde çalışıyorum: board.py ve TeamBoard.ps1.",
        **fields,
    }


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


def _refused(call) -> board.Refused:
    with pytest.raises(board.Refused) as caught:
        call()
    return caught.value


# ------------------------------------------------------------------ the rules, both stores


def test_a_note_one_seat_posts_another_seat_reads(store: board.Board) -> None:
    posted = store.post(_note(), now=NOW)
    asked = store.post(
        _note(seat="inspector", kind="soru", to="worker-2", text="Dev stack açık mı?"),
        now=NOW + timedelta(seconds=1),
    )
    notes = store.read(now=NOW + timedelta(seconds=2))
    assert [n["id"] for n in notes] == [posted["id"], asked["id"]]  # newest last
    assert notes[0] == posted
    assert posted["at"] == "2026-10-03T09:00:00Z"
    assert posted["to"] == "herkes" and posted["reply_to"] == ""
    assert notes[1]["to"] == "worker-2" and notes[1]["kind"] == "soru"
    answer = store.post(
        _note(seat="worker-2", kind="cevap", to="inspector", reply_to=asked["id"], text="Açık."),
        now=NOW + timedelta(seconds=3),
    )
    assert store.read(since="2026-10-03T09:00:03Z")[-1] == answer
    assert len(store.read(limit=1)) == 1


def test_text_of_280_characters_is_kept_and_281_is_refused(store: board.Board) -> None:
    assert len(store.post(_note(text="ş" * 280), now=NOW)["text"]) == 280
    refused = _refused(lambda: store.post(_note(text="ş" * 281), now=NOW))
    assert (refused.status, refused.code) == (422, "invalid")
    assert any("280" in p for p in refused.problems)
    assert len(store.read(now=NOW)) == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"seat": "owner"},
        {"seat": "worker-0"},
        {"seat": "Worker-1"},
        {"seat": "worker-1\n"},
        {"kind": "emir"},
        {"kind": "Bilgi"},
        {"to": "patron"},
        {"text": ""},
        {"text": "   "},
        {"text": "a\x00b"},
        {"task": "Team Board!"},
        {"task": ""},
        {"seat": 7},
        {"text": ["liste"]},
        {"reply_to": 12},
        {"reply_to": "yok-boyle-bir-not"},
        {"extra": "alan"},
    ],
)
def test_a_field_that_breaks_the_rules_is_a_422(store: board.Board, fields) -> None:
    refused = _refused(lambda: store.post(_note(**fields), now=NOW))
    assert refused.status == 422, refused.problems
    assert store.read(now=NOW) == []


def test_a_note_missing_a_field_is_a_422(store: board.Board) -> None:
    body = _note()
    del body["kind"]
    assert _refused(lambda: store.post(body, now=NOW)).status == 422
    assert _refused(lambda: store.post(["not", "a", "note"], now=NOW)).status == 422  # type: ignore[arg-type]


def test_a_token_shaped_text_is_refused_and_not_stored(store: board.Board) -> None:
    for text in (
        f"anahtar {TOKEN_SHAPED}",
        "sk-" + "a" * 24,
        "-----BEGIN RSA " + "PRIVATE KEY-----",
    ):
        refused = _refused(lambda t=text: store.post(_note(text=t), now=NOW))
        assert (refused.status, refused.code) == (422, "secret_like")
        assert TOKEN_SHAPED not in str(refused.detail())  # the refusal does not echo it
    assert store.read(now=NOW) == []


def test_this_systems_own_tokens_are_refused_and_not_stored(store: board.Board) -> None:
    # The team token IS a session token: a note carrying it would hand it to every seat.
    for token in (SESSION_SHAPED, OWNER_SHAPED):
        refused = _refused(lambda t=token: store.post(_note(text=f"ekip anahtarı: {t}"), now=NOW))
        assert (refused.status, refused.code) == (422, "secret_like")
        assert token not in str(refused.detail())
    assert store.read(now=NOW) == []
    # naming the prefix in prose is not a token
    store.post(_note(text="pagentos_st_ önekli belirteçler panoya yazılmaz."), now=NOW)
    assert len(store.read(now=NOW)) == 1


def test_the_twenty_first_note_of_a_task_in_an_hour_is_a_429(store: board.Board) -> None:
    for minute in range(20):
        store.post(_note(text=f"not {minute}"), now=NOW + timedelta(minutes=minute))
    late = NOW + timedelta(minutes=59)
    refused = _refused(lambda: store.post(_note(text="yirmi birinci"), now=late))
    assert (refused.status, refused.code) == (429, "rate_limited")
    # another task is not held by this one's count
    store.post(_note(task="another-task", text="başka görev"), now=late)
    # an hour after the first note, one slot is free again
    store.post(_note(text="saat doldu"), now=NOW + timedelta(minutes=60, seconds=1))
    assert len(store.read(now=late, limit=500)) == 22


def test_pruning_keeps_the_last_500_and_a_second_prune_changes_nothing(
    store: board.Board,
) -> None:
    # 26 tasks so that the hourly bound never refuses one of these
    for index in range(520):
        store.post(
            _note(task=f"task-{index % 26:02d}", text=f"not {index}"),
            now=NOW + timedelta(seconds=index * 10),
        )
    after = NOW + timedelta(seconds=5300)
    notes = store.read(now=after, limit=500)
    assert len(notes) == 500
    assert notes[0]["text"] == "not 20" and notes[-1]["text"] == "not 519"
    assert store.prune(now=after) == 0
    assert store.read(now=after, limit=500) == notes


def test_pruning_drops_what_is_older_than_seven_days(store: board.Board) -> None:
    store.post(_note(text="eski"), now=NOW)
    store.post(_note(text="yeni"), now=NOW + timedelta(days=6))
    week = NOW + timedelta(days=7, seconds=1)
    assert store.prune(now=week) == 1
    assert [n["text"] for n in store.read(now=week)] == ["yeni"]
    assert store.prune(now=week) == 0
    # a write prunes too: no sweep anywhere else
    store.post(_note(text="sonraki"), now=NOW + timedelta(days=13, seconds=1))
    assert [n["text"] for n in store.read(now=week)] == ["sonraki"]


def test_read_refuses_a_since_that_is_not_a_time(store: board.Board) -> None:
    assert _refused(lambda: store.read(since="dün")).status == 422


@pytest.mark.parametrize("since", ["9999-12-31T23:59:59-23:59", "0001-01-01T00:00:00+23:59"])
def test_read_refuses_a_since_that_overflows_the_calendar(store: board.Board, since: str) -> None:
    # the inspector's finding (team-board-inspector-3): a valid ISO time at the calendar's edge
    # overflowed in astimezone(UTC) and the route answered 500 - the board says 4xx, never 500
    assert _refused(lambda: store.read(since=since)).status == 422


def test_the_pure_prune_is_idempotent() -> None:
    notes = [
        {
            "id": board.note_id(NOW + timedelta(seconds=i), f"{i:08x}"),
            "at": board.stamp(NOW + timedelta(seconds=i)),
        }
        for i in range(600)
    ]
    kept, dropped = board.prune_notes(notes, NOW + timedelta(seconds=600))
    assert len(kept) == board.KEEP_NOTES and len(dropped) == 100
    again, none = board.prune_notes(kept, NOW + timedelta(seconds=600))
    assert again == kept and none == []


# ------------------------------------------------------------------ the routes


@pytest.fixture()
def client(engine) -> TestClient:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_store = DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    app.include_router(board_router)
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    return test_client


def test_the_routes_post_and_read_through_the_team_store(client: TestClient, engine) -> None:
    posted = client.post(NOTES, json=_note(seat="inspector", kind="soru", to="worker-2"))
    assert posted.status_code == 200, posted.text
    note = posted.json()["note"]
    read = client.get(NOTES)
    assert read.status_code == 200
    assert read.json()["notes"] == [note]
    with sessionmaker(bind=engine)() as session:
        row = session.execute(select(TeamStateRow).where(TeamStateRow.kind == "note")).scalar_one()
        assert row.key == note["id"] and row.doc == note and row.updated_at == note["at"]
    assert client.get(NOTES, params={"since": "2999-01-01T00:00:00Z"}).json()["notes"] == []


@pytest.mark.parametrize(
    "body, status",
    [
        (_note(text="x" * 281), 422),
        (_note(seat="stranger"), 422),
        (_note(kind="emir"), 422),
        (_note(text=f"token {TOKEN_SHAPED}"), 422),
        (_note(text=f"ekip anahtarı {SESSION_SHAPED}"), 422),
        (_note(text=f"sahip anahtarı {OWNER_SHAPED}"), 422),
        ([1, 2], 422),
        ("metin", 422),
    ],
)
def test_every_refusal_of_the_route_is_its_4xx_never_a_500(
    client: TestClient, body, status
) -> None:
    answer = client.post(NOTES, json=body)
    assert answer.status_code == status, answer.text
    assert client.get(NOTES).json()["notes"] == []


def test_the_route_answers_429_for_the_twenty_first_note(client: TestClient) -> None:
    for index in range(20):
        assert client.post(NOTES, json=_note(text=f"{index}")).status_code == 200
    refused = client.post(NOTES, json=_note(text="21"))
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "rate_limited"


def test_the_route_refuses_bad_query_values(client: TestClient) -> None:
    assert client.get(NOTES, params={"since": "dün"}).status_code == 422
    assert client.get(NOTES, params={"since": "9999-12-31T23:59:59-23:59"}).status_code == 422
    assert client.get(NOTES, params={"limit": 0}).status_code == 422
    assert client.get(NOTES, params={"limit": 501}).status_code == 422


def test_the_board_needs_the_owner_session(engine) -> None:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.include_router(board_router)
    anonymous = TestClient(app)
    assert anonymous.get(NOTES).status_code == 401
    assert anonymous.post(NOTES, json=_note()).status_code == 401


def test_the_real_application_serves_the_board() -> None:
    # The real application object, not a test app with the router added by hand: the board is
    # served (401 without the owner's session), where an unknown path is 404. This FastAPI keeps
    # included routers as nested objects, so app.routes does not list their paths.
    client = TestClient(create_app(Settings(_env_file=None)))
    assert client.get(NOTES).status_code == 401
    assert client.post(NOTES, json=_note()).status_code == 401
    assert client.get("/v1/team/board/no-such-path").status_code == 404


# ------------------------------------------------------------------ the other half: TeamBoard.ps1


def _client_source() -> str:
    return (REPO / "scripts" / "lib" / "TeamBoard.ps1").read_text(encoding="utf-8")


def test_the_powershell_client_uses_this_routes_path_and_fields() -> None:
    source = _client_source()
    assert f'"{NOTES}"' in source
    for field in ("seat", "task", "kind", "to", "reply_to", "text"):
        assert re.search(rf"\b{field}\s*=", source), field


def test_the_powershell_client_knows_the_servers_seats_kinds_and_length() -> None:
    source = _client_source()
    kinds = re.search(r"\$script:TeamBoardKinds\s*=\s*@\(([^)]*)\)", source)
    assert kinds is not None
    assert tuple(re.findall(r'"([^"]+)"', kinds.group(1))) == board.KINDS
    seat = re.search(r"\$script:TeamBoardSeatPattern\s*=\s*'([^']+)'", source)
    assert seat is not None and seat.group(1) == board.SEAT_PATTERN
    length = re.search(r"\$script:TeamBoardTextMax\s*=\s*(\d+)", source)
    assert length is not None and int(length.group(1)) == board.TEXT_MAX_CHARS
    shown = re.search(r"\$script:TeamBoardReadMax\s*=\s*(\d+)", source)
    assert shown is not None and int(shown.group(1)) == board.READ_DEFAULT
