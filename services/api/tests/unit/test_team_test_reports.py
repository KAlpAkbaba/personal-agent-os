"""The test team's round reports (``/v1/team/test-reports``): the owner reads what was tested.

The owner, 2026-10-06: "test ekibinin yaptığı işlemleri ve aldığı sonuçların girdi çıktı olarak
raporlarını istiyorum incelemek için". ``scripts/testteam/test-round.ps1`` POSTs each round's
Turkish input/output report; the Ofis lists them (``OfficeTestReports.tsx``) and opens one.
Owner session only, text only, at most 256 KB a report, the last 50 rounds kept - in the
database (``team_state`` rows of kind ``test_report``) or in the file beside the queue.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.team import test_reports
from app.team.models import TeamStateRow
from app.team.store import DbStore
from app.team.test_reports import router as reports_router
from tests.identity_support import authenticate, install_identity

REPO = Path(__file__).resolve().parents[4]
REPORTS = "/v1/team/test-reports"


def _report(round_id: str = "t202610070100", **fields: object) -> dict[str, object]:
    return {
        "round": round_id,
        "staging_sha": "e" * 40,
        "counts": {"passed": 2, "failed": 1, "broke": 1},
        "unfinished": "",
        "text": "# Test turu\n\nGirdi:\n````\nGET /v1/system/health\n````\n",
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
def client(request, engine, tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    if request.param == "db":
        app.state.team_store = DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    else:
        app.state.team_root = tmp_path / "team"
    app.include_router(reports_router)
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    return test_client


def test_a_report_is_stored_listed_with_its_counts_and_opened_whole(client: TestClient) -> None:
    posted = client.post(REPORTS, json=_report())
    assert posted.status_code == 200, posted.text
    listed = client.get(REPORTS).json()["reports"]
    assert len(listed) == 1
    summary = listed[0]
    assert summary["round"] == "t202610070100"
    assert summary["counts"] == {"passed": 2, "failed": 1, "broke": 1}
    assert summary["staging_sha"] == "e" * 40
    assert summary["unfinished"] == ""
    assert summary["at"].endswith("Z")
    assert "text" not in summary, "the list is light; the text comes with the one report"
    opened = client.get(f"{REPORTS}/t202610070100").json()["report"]
    assert opened["text"] == _report()["text"]
    assert opened["counts"] == summary["counts"]
    assert client.get(f"{REPORTS}/t-yok").status_code == 404


def test_a_dead_round_is_kept_with_its_why(client: TestClient) -> None:
    assert client.post(REPORTS, json=_report(unfinished="kuyruk okunamadı")).status_code == 200
    assert client.get(REPORTS).json()["reports"][0]["unfinished"] == "kuyruk okunamadı"


def test_the_same_round_again_replaces_its_report(client: TestClient) -> None:
    client.post(REPORTS, json=_report(text="ilk"))
    client.post(REPORTS, json=_report(text="ikinci"))
    assert len(client.get(REPORTS).json()["reports"]) == 1
    assert client.get(f"{REPORTS}/t202610070100").json()["report"]["text"] == "ikinci"


def test_the_last_fifty_rounds_are_kept_newest_first(client: TestClient) -> None:
    for n in range(test_reports.KEEP + 3):
        assert client.post(REPORTS, json=_report(f"r{n:03d}")).status_code == 200
    listed = [r["round"] for r in client.get(REPORTS).json()["reports"]]
    assert test_reports.KEEP == 50
    assert len(listed) == 50
    assert listed[0] == "r052" and listed[-1] == "r003"
    assert client.get(f"{REPORTS}/r002").status_code == 404


@pytest.mark.parametrize("again", ["r052", "r003"])
def test_a_round_sent_again_at_fifty_keeps_fifty(client: TestClient, again: str) -> None:
    # Inspector, 2026-10-07: the database path counted the round's old row and its new one
    # as two, so 50 kept rounds and the newest sent again left 49 (r003 dropped).
    for n in range(test_reports.KEEP + 3):
        client.post(REPORTS, json=_report(f"r{n:03d}"))
    assert client.post(REPORTS, json=_report(again, text="yeniden")).status_code == 200
    listed = [r["round"] for r in client.get(REPORTS).json()["reports"]]
    assert len(listed) == 50, listed
    assert listed[0] == again
    assert set(listed) == {f"r{n:03d}" for n in range(3, test_reports.KEEP + 3)}
    assert client.get(f"{REPORTS}/{again}").json()["report"]["text"] == "yeniden"


@pytest.mark.parametrize(
    "fields",
    [
        {"text": "ikili gövde \u0000 burada"},
        {"text": "yarım emoji \ud83d burada"},
        {"text": "ters yarım \ude00"},
        {"unfinished": "kopma \u0000"},
        {"unfinished": "kopma \udfff"},
    ],
)
def test_a_nul_or_a_lone_surrogate_is_a_422_never_a_500(client: TestClient, fields) -> None:
    # Inspector, 2026-10-07: NUL passed SQLite and broke Postgres JSONB (DataError, 500); a lone
    # surrogate raised UnicodeEncodeError in the size check (500). test-round.ps1 cleans both
    # before it sends (TestTeam.ps1: ConvertTo-TestTeamCleanText); the route refuses them.
    # json.dumps escapes a lone surrogate as ``\ud83d`` (httpx' json= cannot encode it).
    answer = client.post(
        REPORTS,
        content=json.dumps(_report(**fields)).encode("ascii"),
        headers={"content-type": "application/json"},
    )
    assert answer.status_code == 422, answer.text
    assert answer.json()["detail"]["code"] == "invalid"
    assert client.get(REPORTS).json()["reports"] == []


def test_a_whole_emoji_is_taken(client: TestClient) -> None:
    assert client.post(REPORTS, json=_report(text="tamam 😀")).status_code == 200
    assert client.get(f"{REPORTS}/t202610070100").json()["report"]["text"] == "tamam 😀"


def test_a_report_over_256_kb_is_refused_and_one_at_the_bound_taken(client: TestClient) -> None:
    assert test_reports.TEXT_MAX_BYTES == 256 * 1024
    # Turkish letters are two bytes: the bound is in UTF-8 bytes, not characters.
    over = "ğ" * (test_reports.TEXT_MAX_BYTES // 2 + 1)
    refused = client.post(REPORTS, json=_report(text=over))
    assert refused.status_code == 413, refused.text
    assert refused.json()["detail"]["code"] == "too_large"
    assert client.get(REPORTS).json()["reports"] == []
    at_bound = "ğ" * (test_reports.TEXT_MAX_BYTES // 2)
    assert client.post(REPORTS, json=_report(text=at_bound)).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        _report(round="../../etc"),
        _report(round=""),
        _report(text=None),
        _report(text={"html": "<script>"}),
        _report(counts={"passed": -1, "failed": 0, "broke": 0}),
        _report(counts={"passed": "çok"}),
        _report(staging_sha="x" * 200),
        [1, 2],
        "metin",
    ],
)
def test_a_malformed_report_is_a_422_never_a_500(client: TestClient, body) -> None:
    answer = client.post(REPORTS, json=body)
    assert answer.status_code == 422, answer.text
    assert client.get(REPORTS).json()["reports"] == []


def test_the_reports_need_the_owner_session(engine) -> None:
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_store = DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    app.include_router(reports_router)
    anonymous = TestClient(app)
    assert anonymous.get(REPORTS).status_code == 401
    assert anonymous.post(REPORTS, json=_report()).status_code == 401
    assert anonymous.get(f"{REPORTS}/t202610070100").status_code == 401
    # A narrowed (scoped) session is not the owner's full authority either.
    scoped = TestClient(app)
    authenticate(app, scoped, settings=settings, scopes=["voice"])
    assert scoped.post(REPORTS, json=_report()).status_code in (401, 403)
    assert scoped.get(REPORTS).status_code in (401, 403)
    with sessionmaker(bind=engine)() as session:
        assert session.execute(select(TeamStateRow)).first() is None


def test_the_database_keeps_a_report_as_a_team_state_row(engine) -> None:
    store = test_reports.DbRoundReports(sessionmaker(bind=engine, expire_on_commit=False))
    store.put(test_reports.check_report(_report()))
    with sessionmaker(bind=engine)() as session:
        row = session.execute(select(TeamStateRow)).scalar_one()
    assert (row.kind, row.key) == (test_reports.KIND_TEST_REPORT, "t202610070100")
    assert row.doc["text"].startswith("# Test turu")


def test_the_round_script_posts_to_this_route_with_these_fields() -> None:
    # The other half of the contract: scripts/testteam/test-round.ps1 is what POSTs here.
    source = (REPO / "scripts" / "testteam" / "test-round.ps1").read_text(encoding="utf-8-sig")
    assert f'-Path "{REPORTS}"' in source
    for field in ("round", "staging_sha", "counts", "unfinished", "text"):
        assert f"{field} " in source or f"{field}=" in source, field
    team = (REPO / "scripts" / "testteam" / "TestTeam.ps1").read_text(encoding="utf-8-sig")
    assert f"$script:TestTeamReportMaxBytes = {test_reports.TEXT_MAX_BYTES}" in team


@pytest.mark.xfail(
    strict=True,
    reason="ALAN_ISTEGI: services/api/app/main.py must include app.team.test_reports.router "
    "(outside this card's area); strict, so the wiring turns this into a failure to remove",
)
def test_the_real_application_serves_the_reports() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))
    assert client.get(REPORTS).status_code == 401
