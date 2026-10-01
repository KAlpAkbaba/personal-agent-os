"""The allow-list editor (ADR-0218): POST/DELETE /v1/team/allowlist and the store behind it.

The owner's sites live in ``team_state`` rows of kind ``allowlist`` (no new table); the shared
JSON is the seed. ``allowlist_store.acting_allowed`` merges both and asks the deny-list first.
The ledger vocabulary is closed and the lead adds the two event types at merge time, so the
fixture below wires them for the tests that are about something else, and one test leaves them
out to hold "the ledger first, a refusal writes nothing".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.execution import allowlist, allowlist_store
from app.ledger import vocabulary
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.team.allowlist_routes import router as allowlist_router
from app.team.models import TeamStateRow
from tests.identity_support import authenticate, install_identity

BASE = "/v1/team/allowlist"
SHOP = "https://www.magaza.com.tr/sepet"


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
def wired_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vocabulary,
        "EVENT_TYPES",
        (
            *vocabulary.EVENT_TYPES,
            allowlist_store.EVENT_SITE_ADDED,
            allowlist_store.EVENT_SITE_REMOVED,
        ),
    )


@pytest.fixture(autouse=True)
def empty_seed_and_unbound(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(allowlist, "sites", lambda: ())
    allowlist_store.unbind()
    yield
    allowlist_store.unbind()


@pytest.fixture()
def api(engine):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    app.include_router(allowlist_router)
    return app, TestClient(app), settings


@pytest.fixture()
def owner(api, wired_vocabulary) -> TestClient:
    app, client, settings = api
    authenticate(app, client, settings=settings)
    return client


def _events(engine) -> list[ActivityEventRow]:
    with sessionmaker(bind=engine)() as session:
        return list(session.execute(select(ActivityEventRow)).scalars())


def _rows(engine) -> list[TeamStateRow]:
    with sessionmaker(bind=engine)() as session:
        return list(
            session.execute(
                select(TeamStateRow).where(TeamStateRow.kind == allowlist_store.KIND_ALLOWLIST)
            ).scalars()
        )


def _add(client: TestClient, site: str):
    return client.post(BASE, json={"site": site})


# -------------------------------------------------------------------- the session gate


def test_without_an_owner_session_every_verb_is_refused(api):
    _, client, _ = api
    assert client.get(BASE).status_code == 401
    assert client.post(BASE, json={"site": "magaza.com.tr"}).status_code == 401
    assert client.delete(f"{BASE}/magaza.com.tr").status_code == 401


# -------------------------------------------------------------------- add, list, remove


def test_add_then_list_then_remove(owner, engine):
    assert owner.get(BASE).json()["sites"] == []

    added = _add(owner, "magaza.com.tr")
    assert added.status_code == 200, added.text
    assert added.json()["site"] == "magaza.com.tr"

    listed = owner.get(BASE).json()["sites"]
    assert [s["site"] for s in listed] == ["magaza.com.tr"]
    assert listed[0]["source"] == "owner"
    assert listed[0]["added_by"] == "shell"
    assert listed[0]["added_at"]

    removed = owner.delete(f"{BASE}/magaza.com.tr")
    assert removed.status_code == 200, removed.text
    assert owner.get(BASE).json()["sites"] == []
    assert _rows(engine) == []


def test_the_site_is_normalised_and_a_subdomain_is_not_a_site(owner):
    assert _add(owner, "  Magaza.COM.TR. ").json()["site"] == "magaza.com.tr"
    near_miss = _add(owner, "www.baska.com.tr")
    assert near_miss.status_code == 422
    assert near_miss.json()["detail"]["code"] == "not_a_registrable_domain"
    assert [s["site"] for s in owner.get(BASE).json()["sites"]] == ["magaza.com.tr"]


def test_adding_twice_is_one_row_and_one_ledger_event(owner, engine):
    assert _add(owner, "magaza.com.tr").status_code == 200
    second = _add(owner, "magaza.com.tr")
    assert second.status_code == 200
    assert second.json()["already_listed"] is True
    assert len(_rows(engine)) == 1
    assert len(_events(engine)) == 1


def test_removing_a_site_that_is_not_listed_is_a_404_and_no_event(owner, engine):
    _add(owner, "magaza.com.tr")
    response = owner.delete(f"{BASE}/baska.com.tr")
    assert response.status_code == 404
    assert len(_events(engine)) == 1  # the add only


def test_a_seed_site_is_listed_but_cannot_be_removed_here(owner, monkeypatch, engine):
    monkeypatch.setattr(allowlist, "sites", lambda: ("tohum.com",))
    listed = owner.get(BASE).json()["sites"]
    assert [(s["site"], s["source"]) for s in listed] == [("tohum.com", "seed")]
    response = owner.delete(f"{BASE}/tohum.com")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "seed_site"
    assert _events(engine) == []


# -------------------------------------------------------------------- refusals


def test_a_deny_listed_site_is_refused_even_when_asked_twice(owner, engine):
    for _ in range(2):
        response = _add(owner, "isbank.com.tr")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "deny_listed_site"
    # the label rule too: "bank" inside a label
    assert _add(owner, "yenibank.example.com").status_code == 422
    assert _rows(engine) == []
    assert _events(engine) == []
    # the near miss: a shop that is not on the deny-list is taken
    assert _add(owner, "magaza.com.tr").status_code == 200


@pytest.mark.parametrize("bare", ["com.tr", "co.uk", "co.example", "com", "tr"])
def test_a_bare_public_suffix_is_refused(owner, engine, bare):
    response = _add(owner, bare)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] in {"bare_public_suffix", "not_a_registrable_domain"}
    assert _rows(engine) == []


def test_a_suffix_lookalike_that_is_a_real_domain_is_taken(owner):
    assert _add(owner, "co.com").status_code == 200
    assert _add(owner, "web.com.tr").status_code == 200


@pytest.mark.parametrize(
    "junk", ["", "   ", "a b.com", "127.0.0.1", "localhost", "x" * 90 + ".com"]
)
def test_input_that_is_not_a_domain_is_refused(owner, junk):
    assert _add(owner, junk).status_code == 422


def test_a_ledger_that_refuses_the_event_leaves_no_row(api, engine, monkeypatch):
    app, client, settings = api
    authenticate(app, client, settings=settings)
    # The lead wired the two event types at merge (office-01), so the refusal is produced
    # the way a closed vocabulary would produce it: the type is taken out again.
    monkeypatch.setattr(
        vocabulary,
        "EVENT_TYPES",
        tuple(t for t in vocabulary.EVENT_TYPES if t != allowlist_store.EVENT_SITE_ADDED),
    )
    response = _add(client, "magaza.com.tr")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ledger_refused"
    assert _rows(engine) == []


# -------------------------------------------------------------------- the ledger


def test_every_change_is_one_ledger_event(owner, engine):
    _add(owner, "magaza.com.tr")
    owner.delete(f"{BASE}/magaza.com.tr")
    events = _events(engine)
    assert [e.event_type for e in events] == ["allowlist.site_added", "allowlist.site_removed"]
    assert {e.subsystem for e in events} == {"team"}
    assert all(e.detail_json["site"] == "magaza.com.tr" for e in events)
    assert all(e.detail_json["actor"] == "owner" for e in events)


# ---- acting_allowed follows the store


def test_acting_allowed_follows_the_store_at_once(owner):
    assert allowlist_store.acting_allowed(SHOP) == (False, "not_on_owner_allow_list")
    _add(owner, "magaza.com.tr")
    assert allowlist_store.acting_allowed(SHOP) == (True, "")
    assert allowlist_store.acting_allowed("https://evilmagaza.com.tr/") == (
        False,
        "not_on_owner_allow_list",
    )
    owner.delete(f"{BASE}/magaza.com.tr")
    assert allowlist_store.acting_allowed(SHOP) == (False, "not_on_owner_allow_list")


def test_the_seed_and_the_rows_are_merged(owner, monkeypatch):
    monkeypatch.setattr(allowlist, "sites", lambda: ("tohum.com",))
    _add(owner, "magaza.com.tr")
    assert allowlist_store.acting_allowed("https://tohum.com/")[0] is True
    assert allowlist_store.acting_allowed(SHOP)[0] is True
    assert allowlist_store.effective_sites() == ("tohum.com", "magaza.com.tr")
    assert allowlist_store.acting_allowed("https://baska.com/")[0] is False


def test_the_deny_list_wins_over_a_row_written_behind_the_editor(api, engine):
    app, _, _ = api
    allowlist_store.bind(app.state.artifacts.session)
    with sessionmaker(bind=engine)() as session:
        session.add(
            TeamStateRow(
                kind=allowlist_store.KIND_ALLOWLIST,
                key="isbank.com.tr",
                doc={"added_at": "2026-10-01T00:00:00Z", "added_by": "shell"},
                updated_at="2026-10-01T00:00:00Z",
            )
        )
        session.commit()
    assert allowlist_store.acting_allowed("https://www.isbank.com.tr/") == (
        False,
        "deny_listed_site",
    )
    assert "isbank.com.tr" not in allowlist_store.effective_sites()


def test_unbound_the_store_answers_from_the_seed_only(monkeypatch):
    monkeypatch.setattr(allowlist, "sites", lambda: ("tohum.com",))
    assert allowlist_store.acting_allowed("https://tohum.com/x")[0] is True
    assert allowlist_store.acting_allowed(SHOP)[0] is False
