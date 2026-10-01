"""An idea's text lives in the team's store, not only in ``team/`` on one machine.

Owner decision 2026-10-01 (ADR-0214 addendum 5): the researcher's proposals appear in the
Onay Merkezi as ideas the owner reads and approves. Since the same day the team's state is in
the Cloud Core's PostgreSQL (ADR-0222) - and there is no ``team/`` folder on the Cloud Core, so
an idea showed its title and nothing to read. ``POST /v1/team/queue/proposals`` puts the text
where the queue is; ``GET /v1/team/approvals`` reads it from there. Every rule below runs on
both stores; the same calls go to real PostgreSQL in
``tests/integration/test_team_proposals_postgres.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.team import approvals
from app.team import store as team_store
from app.team.models import TeamStateRow
from tests.identity_support import authenticate, install_identity

PROPOSALS_API = "/v1/team/queue/proposals"
APPROVALS_API = "/v1/team/approvals"
NAME = "2026-10-01-ev-home-assistant.md"
TEXT = "# Ev otomasyonu\n\nIşığı sesle aç: ığüşöç İĞÜŞÖÇ.\n"
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
KEY_WIDTH = TeamStateRow.__table__.c.key.type.length
REPORTS_API = "/v1/team/queue/reports"
#: U+0000: SQLite and a file keep it, PostgreSQL's JSONB refuses it (a 500 until 2026-10-01).
NUL = chr(0)
#: Half of a surrogate pair: a Python string that is not UTF-8, so neither store can keep it.
LONE_SURROGATE = chr(0xD800)
#: Windows keeps these stems for devices whatever the extension: ``nul.md`` is the null device.
DEVICE_NAMES = (
    "nul.md",
    "con.md",
    "aux.md",
    "prn.md",
    "com1.md",
    "com9.md",
    "lpt1.md",
    "lpt9.md",
    "nul.fikir.md",
)
#: The ones a test may hand to a FileStore: were the rule missing, the write would go to the
#: null device and fail. ``com1.md`` would OPEN a serial port and hang (it did, 2026-10-01),
#: and a hanging test proves nothing - the other stems are asserted on the rule itself.
NULL_DEVICE_NAMES = ("nul.md", "nul.fikir.md")


def _task(task_id: str, state: str = "awaiting_owner", **extra: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "title": f"Fikir {task_id}",
        "roadmap_row": "",
        "state": state,
        "area": [],
        "branch": "",
        "worktree": "",
        "assignee": "",
        "reports": [],
        "budget": {"max_usd": 5},
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
    }
    task.update(extra)
    return task


def _name_of_length(length: int) -> str:
    return "a" * (length - 3) + ".md"


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    TeamStateRow.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def team_root(tmp_path: Path) -> Path:
    """``team/`` as the Cloud Core has it: a queue, a lock, and NO proposals folder."""
    root = tmp_path / "team"
    root.mkdir()
    (root / "queue.json").write_text('{"version": 1, "tasks": []}\n', encoding="utf-8")
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    return root


@pytest.fixture(params=["file", "db"])
def store(request, engine, team_root: Path) -> team_store.TeamStore:
    if request.param == "db":
        return team_store.DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    return team_store.FileStore(team_root)


@pytest.fixture()
def app_and_client(store, team_root: Path):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_root = team_root
    app.state.team_store = store
    return app, TestClient(app), settings


@pytest.fixture()
def owner(app_and_client) -> TestClient:
    app, client, settings = app_and_client
    authenticate(app, client, settings=settings)
    return client


def _everything_written(store, engine, team_root: Path) -> list[str]:
    """Every proposal either store holds, by where it is kept (not through read_proposal)."""
    if store.kind == "db":
        with sessionmaker(bind=engine)() as session:
            rows = session.query(TeamStateRow).filter(TeamStateRow.kind == "proposal").all()
            return sorted(row.key for row in rows)
    return sorted(p.name for p in (team_root / "proposals").glob("*"))


def _listed(owner: TestClient, task_id: str) -> dict[str, Any]:
    body = owner.get(APPROVALS_API).json()
    return next(a for a in body["approvals"] if a["task_id"] == task_id)


# ------------------------------------------------------------------ the store


def test_a_proposal_put_in_the_store_is_read_back_as_it_was_written(store):
    assert store.read_proposal(NAME) is None
    store.put_proposal(NAME, TEXT, now=NOW)
    assert store.read_proposal(NAME) == TEXT
    assert store.read_proposal("2026-10-01-another-idea.md") is None


def test_a_second_put_of_the_same_name_replaces_the_text_and_keeps_one_copy(
    store, engine, team_root
):
    store.put_proposal(NAME, "ilk\n", now=NOW)
    store.put_proposal(NAME, "ikinci\n", now=NOW)
    assert store.read_proposal(NAME) == "ikinci\n"
    assert _everything_written(store, engine, team_root) == [NAME]


@pytest.mark.parametrize(
    "bad",
    [
        "../x.md",
        "a/b.md",
        "a\\b.md",
        "Buyuk-Harf.md",
        "fikir.txt",
        "fikir",
        ".md",
        "a.md",  # one character before .md: the pattern asks for two
        "-fikir.md",
        "fikir .md",
        "fikir.md\n",
        "",
        _name_of_length(KEY_WIDTH + 1),  # fits the pattern (<= 124), not the key column
        *NULL_DEVICE_NAMES,
    ],
)
def test_a_name_that_breaks_the_pattern_or_the_key_column_is_refused_by_the_store(
    store, engine, team_root, bad
):
    with pytest.raises(team_store.Invalid):
        store.put_proposal(bad, TEXT, now=NOW)
    assert _everything_written(store, engine, team_root) == []
    assert store.read_proposal(bad) is None  # reading it is not an error, and finds nothing


def test_the_longest_name_the_key_column_holds_is_kept_whole(store, engine, team_root):
    longest = _name_of_length(KEY_WIDTH)
    store.put_proposal(longest, TEXT, now=NOW)
    assert store.read_proposal(longest) == TEXT
    assert _everything_written(store, engine, team_root) == [longest]


def test_a_text_over_the_limit_or_not_a_string_is_refused_by_the_store(store, engine, team_root):
    store.put_proposal(NAME, "x" * team_store.PROPOSAL_MAX_CHARS, now=NOW)
    for bad in ("x" * (team_store.PROPOSAL_MAX_CHARS + 1), None, 7, ["x"]):
        with pytest.raises(team_store.Invalid):
            store.put_proposal("2026-10-01-too-much.md", bad, now=NOW)  # type: ignore[arg-type]
    assert _everything_written(store, engine, team_root) == [NAME]


def test_a_proposal_is_not_a_task_and_not_a_cycle_report(store):
    store.put_proposal(NAME, TEXT, now=NOW)
    assert store.read_queue()["tasks"] == []
    assert store.newest_report() is None
    store.put_report("cycle-1.md", "# rapor\n", now=NOW)
    assert store.read_proposal("cycle-1.md") is None


def test_a_report_name_that_ends_in_a_newline_is_refused_like_a_proposal_name(store):
    # Found on the way: ``$`` matches before a final newline, so "x.md\n" was a valid report
    # name - a key with a newline in the database, an OSError (500) on the file store.
    with pytest.raises(team_store.Invalid):
        store.put_report("cycle-1.md\n", "rapor\n", now=NOW)
    assert store.newest_report() is None
    store.put_report("cycle-1.md", "rapor\n", now=NOW)
    assert store.newest_report()["file"] == "cycle-1.md"


def test_every_proposal_row_fits_the_widths_the_model_states(engine):
    """SQLite keeps a string longer than its VARCHAR and PostgreSQL refuses it (2026-10-01,
    the lock row). The widths are read from the model; the name is the longest allowed."""
    db = team_store.DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    db.put_proposal(_name_of_length(KEY_WIDTH), "x" * team_store.PROPOSAL_MAX_CHARS)
    db.put_proposal(NAME, TEXT)
    widths = {
        column.name: column.type.length
        for column in TeamStateRow.__table__.columns
        if getattr(column.type, "length", None)
    }
    assert set(widths) == {"kind", "key", "updated_at"}
    with sessionmaker(bind=engine)() as session:
        rows = session.query(TeamStateRow).filter(TeamStateRow.kind == "proposal").all()
    assert len(rows) == 2
    for row in rows:
        for name, width in widths.items():
            value = getattr(row, name)
            assert len(value) <= width, f"{row.key}: {name} is {len(value)} > {width}"
        assert set(row.doc) == {"text"}


def test_the_name_pattern_is_the_one_the_contract_states():
    # Binding with the sibling task whose cycle.ps1 posts here: the pattern, said once.
    assert team_store.PROPOSAL_NAME_PATTERN == r"^[a-z0-9][a-z0-9._-]{1,120}\.md$"
    assert team_store.PROPOSAL_MAX_CHARS == 200_000


def test_a_text_no_database_can_keep_is_refused_by_both_stores_alike(store, engine, team_root):
    """PostgreSQL's JSONB has no U+0000 and SQLite and a file keep it: the store refuses it on
    both, so a text one machine accepts is never a 500 on the other (ADR-0214 addendum 4)."""
    store.put_proposal(NAME, TEXT, now=NOW)
    for bad in (NUL, "ilk" + NUL + "son", TEXT + NUL, LONE_SURROGATE, "a" + LONE_SURROGATE):
        with pytest.raises(team_store.Invalid):
            store.put_proposal(NAME, bad, now=NOW)
        with pytest.raises(team_store.Invalid):
            store.put_proposal("2026-10-01-baska.md", bad, now=NOW)
    assert store.read_proposal(NAME) == TEXT  # the refused replace left the text as it was
    assert _everything_written(store, engine, team_root) == [NAME]


@pytest.mark.parametrize("device", DEVICE_NAMES)
def test_a_windows_device_name_cannot_name_a_proposal(device):
    # ``team/proposals/nul.md`` is not a file on the machine that runs the file store; the rule
    # is one for both stores, so a name is never good on the Cloud Core and bad at home.
    assert team_store.proposal_name_problems(device) != []
    assert team_store.proposal_name_problems("2026-10-01-" + device) == []


@pytest.mark.parametrize(
    "near", ["null.md", "console.md", "com10.md", "lpt.md", "2026-10-01-nul.md", "nul-fikir.md"]
)
def test_a_name_that_only_looks_like_a_device_name_is_kept(store, engine, team_root, near):
    store.put_proposal(near, TEXT, now=NOW)
    assert store.read_proposal(near) == TEXT
    assert _everything_written(store, engine, team_root) == [near]


def test_a_report_no_database_can_keep_is_refused_by_both_stores_alike(store):
    # Found on the way: the cycle report had the same two holes as the proposal.
    for bad in (NUL, "rapor" + NUL, LONE_SURROGATE):
        with pytest.raises(team_store.Invalid):
            store.put_report("cycle-1.md", bad, now=NOW)
    for device in ("nul.md", "NUL.md", "Nul.rapor.md"):  # the null device only: see above
        with pytest.raises(team_store.Invalid):
            store.put_report(device, "rapor", now=NOW)
    assert store.newest_report() is None
    store.put_report("null.md", "rapor", now=NOW)
    assert store.newest_report() == {"file": "null.md", "text": "rapor"}


# ------------------------------------------------------------------ the route


def test_a_posted_proposal_is_the_text_the_onay_merkezi_shows_for_the_idea_that_names_it(
    owner, store
):
    store.put_task(_task("ev-home-assistant", proposal=f"team/proposals/{NAME}"), None)
    store.put_task(_task("baska-fikir", proposal="team/proposals/2026-10-01-baska.md"), None)
    assert _listed(owner, "ev-home-assistant")["proposal_text"] is None

    posted = owner.post(PROPOSALS_API, json={"name": NAME, "text": TEXT})
    assert posted.status_code == 200, posted.text
    assert posted.json() == {"ok": True}

    assert _listed(owner, "ev-home-assistant")["proposal_text"] == TEXT
    assert _listed(owner, "ev-home-assistant")["proposal"] == f"team/proposals/{NAME}"
    assert _listed(owner, "baska-fikir")["proposal_text"] is None  # the other idea: not this text


def test_a_windows_path_to_the_proposal_names_the_same_file(owner, store):
    store.put_task(_task("ev-home-assistant", proposal=f"team\\proposals\\{NAME}"), None)
    owner.post(PROPOSALS_API, json={"name": NAME, "text": TEXT})
    assert _listed(owner, "ev-home-assistant")["proposal_text"] == TEXT


def test_a_second_post_of_the_same_name_replaces_what_the_owner_reads(owner, store):
    store.put_task(_task("ev-home-assistant", proposal=f"team/proposals/{NAME}"), None)
    assert owner.post(PROPOSALS_API, json={"name": NAME, "text": "ilk\n"}).status_code == 200
    assert owner.post(PROPOSALS_API, json={"name": NAME, "text": "ikinci\n"}).status_code == 200
    assert _listed(owner, "ev-home-assistant")["proposal_text"] == "ikinci\n"


def test_the_text_shown_is_cut_at_the_listing_limit_and_the_store_keeps_all_of_it(owner, store):
    store.put_task(_task("ev-home-assistant", proposal=f"team/proposals/{NAME}"), None)
    long = "ş" * (approvals.TEXT_MAX_CHARS + 5)
    assert owner.post(PROPOSALS_API, json={"name": NAME, "text": long}).status_code == 200
    assert _listed(owner, "ev-home-assistant")["proposal_text"] == "ş" * approvals.TEXT_MAX_CHARS
    assert store.read_proposal(NAME) == long


@pytest.mark.parametrize(
    "body",
    [
        {"name": "../x.md", "text": "x"},
        {"name": "a/b.md", "text": "x"},
        {"name": "Buyuk-Harf.md", "text": "x"},
        {"name": "fikir.txt", "text": "x"},
        {"name": "a.md", "text": "x"},
        {"name": "", "text": "x"},
        {"name": _name_of_length(KEY_WIDTH + 1), "text": "x"},
        {"name": "x" * 200 + ".md", "text": "x"},
        {"name": NAME, "text": "x" * 200_001},
        {"name": NAME, "text": 7},
        {"name": NAME, "text": None},
        {"name": NAME, "text": ["x"]},
        {"name": 7, "text": "x"},
        {"name": NAME},
        {"text": "x"},
        {},
        {"name": NAME, "text": "x", "path": "team/proposals"},
        {"name": NAME, "text": "ilk" + NUL + "son"},
        {"name": NAME, "text": NUL},
        *({"name": device, "text": "x"} for device in NULL_DEVICE_NAMES),
    ],
)
def test_a_body_the_contract_does_not_allow_is_a_422_and_nothing_is_written(
    owner, store, engine, team_root, body
):
    refused = owner.post(PROPOSALS_API, json=body)
    assert refused.status_code == 422, refused.text
    assert _everything_written(store, engine, team_root) == []


def test_the_limits_themselves_are_accepted(owner, store):
    longest = _name_of_length(KEY_WIDTH)
    at_limit = owner.post(PROPOSALS_API, json={"name": longest, "text": "x" * 200_000})
    assert at_limit.status_code == 200, at_limit.text
    assert len(store.read_proposal(longest)) == 200_000
    assert owner.post(PROPOSALS_API, json={"name": "ab.md", "text": ""}).status_code == 200
    assert store.read_proposal("ab.md") == ""


def test_a_post_without_an_owner_session_is_a_401_and_nothing_is_written(
    app_and_client, store, engine, team_root
):
    _, client, _ = app_and_client
    assert client.post(PROPOSALS_API, json={"name": NAME, "text": TEXT}).status_code == 401
    assert _everything_written(store, engine, team_root) == []


def test_a_refused_post_leaves_the_text_the_owner_already_reads(owner, store):
    store.put_task(_task("ev-home-assistant", proposal=f"team/proposals/{NAME}"), None)
    assert owner.post(PROPOSALS_API, json={"name": NAME, "text": TEXT}).status_code == 200
    refused = owner.post(PROPOSALS_API, json={"name": NAME, "text": "yeni" + NUL})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "invalid"
    assert _listed(owner, "ev-home-assistant")["proposal_text"] == TEXT


def test_a_report_with_a_nul_or_a_device_name_is_a_422_and_nothing_is_stored(owner, store):
    assert owner.post(REPORTS_API, json={"name": "cycle-1.md", "text": NUL}).status_code == 422
    assert owner.post(REPORTS_API, json={"name": "nul.md", "text": "rapor"}).status_code == 422
    assert store.newest_report() is None


# ------------------------------------------------------------------ where the text comes from


def test_on_the_database_store_a_file_under_team_is_never_what_the_owner_reads(
    engine, team_root, tmp_path
):
    """The Cloud Core has no ``team/``; a machine that has one and serves the database must
    not show a text the other machine cannot see. The store is the one source."""
    db = team_store.DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    (team_root / "proposals").mkdir()
    (team_root / "proposals" / NAME).write_text("diskteki eski metin\n", encoding="utf-8")
    db.put_task(_task("ev-home-assistant", proposal=f"team/proposals/{NAME}"), None)

    listed = approvals.list_pending(db.read_queue(), team_root, db)
    assert listed[0]["proposal_text"] is None
    db.put_proposal(NAME, TEXT)
    listed = approvals.list_pending(db.read_queue(), team_root, db)
    assert listed[0]["proposal_text"] == TEXT


def test_on_the_file_store_a_file_the_store_did_not_write_is_still_read(team_root):
    """The fallback, for the FileStore only: what a researcher wrote under ``team/`` by hand,
    with a name the route would refuse, and a path under ``team/`` that is not a proposal."""
    files = team_store.FileStore(team_root)
    (team_root / "proposals").mkdir()
    (team_root / "plans").mkdir()
    (team_root / "proposals" / "Eski_Fikir.md").write_text("elle yazılmış\n", encoding="utf-8")
    (team_root / "plans" / "bolme.md").write_text("bölme planı\n", encoding="utf-8")
    files.put_task(_task("eski-fikir", proposal="team/proposals/Eski_Fikir.md"), None)
    files.put_task(_task("bolme", proposal="team/plans/bolme.md"), None)
    files.put_task(_task("kacak", proposal="team/../secret.md"), None)
    (team_root.parent / "secret.md").write_text("gizli\n", encoding="utf-8")

    listed = {a["task_id"]: a for a in approvals.list_pending(files.read_queue(), team_root, files)}
    assert listed["eski-fikir"]["proposal_text"] == "elle yazılmış\n"
    assert listed["bolme"]["proposal_text"] == "bölme planı\n"
    assert listed["kacak"]["proposal_text"] is None


def test_a_proposal_that_is_prose_is_shown_as_it_is_on_both_stores(owner, store):
    store.put_task(_task("kisa", proposal="Kısa öneri metni"), None)
    assert _listed(owner, "kisa")["proposal_text"] == "Kısa öneri metni"


def test_the_file_store_writes_the_text_as_utf8_without_touching_its_line_ends(team_root):
    files = team_store.FileStore(team_root)
    files.put_proposal(NAME, TEXT, now=NOW)
    assert (team_root / "proposals" / NAME).read_bytes() == TEXT.encode("utf-8")
