"""Unit tests: the ``/v1/selfmodel`` REST surface (PHASE 6).

``app/main.py`` is not modified by this track -- the integrator mounts the
router -- so these tests mount it themselves on a real app built by
``create_app``. Owner authentication is the real dependency through the real
identity service (``tests/identity_support.authenticate``), so a passing test is
evidence the surface is gated, not that gating was stubbed out.

``POST /index`` never accepts a path from the caller; it walks
``default_repo_root()``. The tests point that at the fixture tree through the
``PAGENTOS_SELFMODEL_REPO_ROOT`` operator override rather than through a request
field, which is exactly the property being protected.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.main import create_app
from app.selfmodel.indexer import IndexConfig, build_index
from app.selfmodel.routes import SELFMODEL_VERSION
from app.selfmodel.routes import router as selfmodel_router
from tests.identity_support import authenticate
from tests.selfmodel_support import (
    FIXTURE_MODULE,
    FIXTURE_PACKAGE,
    FIXTURE_PLANS_MODULE,
    FIXTURE_TOOLS_MODULE,
    make_engine,
    write_fixture_tree,
)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = write_fixture_tree(tmp_path / "repo")
    monkeypatch.setenv("PAGENTOS_SELFMODEL_REPO_ROOT", str(root))
    return root


@pytest.fixture()
def engine():
    eng = make_engine()
    yield eng
    eng.dispose()


@pytest.fixture()
def app_and_client(engine):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.include_router(selfmodel_router)

    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts

    return app, TestClient(app)


@pytest.fixture()
def client(app_and_client) -> TestClient:
    app, test_client = app_and_client
    authenticate(app, test_client, settings=Settings(_env_file=None))
    return test_client


@pytest.fixture()
def indexed_client(client: TestClient, engine, repo: Path) -> TestClient:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        build_index(session, config=IndexConfig(repo_root=repo))
        session.commit()
    return client


# ------------------------------------------------------------------ gating


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/v1/selfmodel/policy"),
        ("post", "/v1/selfmodel/index"),
        ("get", "/v1/selfmodel/modules"),
        ("get", "/v1/selfmodel/modules/app.observer"),
        ("get", "/v1/selfmodel/modules/app.observer/problems"),
        ("get", "/v1/selfmodel/capabilities/observer.look"),
        ("get", "/v1/selfmodel/search?q=observer"),
    ],
)
def test_every_endpoint_requires_an_owner_session(app_and_client, method: str, path: str) -> None:
    _, test_client = app_and_client
    response = getattr(test_client, method)(path)
    assert response.status_code == 401


# ------------------------------------------------------------------ policy


def test_policy_reports_the_contract(client: TestClient) -> None:
    response = client.get("/v1/selfmodel/policy")
    assert response.status_code == 200
    body = response.json()

    assert body["selfmodel_version"] == SELFMODEL_VERSION == 2
    assert set(body["truth_kinds"]) == {"source", "installed", "runtime", "evidence"}
    assert "route" in body["symbol_kinds"]
    assert "released_as" in body["edge_kinds"]
    assert "source_only" in body["production_states"]
    assert body["ui_subsystem"] == "self_model"
    assert body["required_gates"] == ["tests_passed", "security_review_passed", "shadow_ready"]


# ------------------------------------------------------------------- index


def test_post_index_builds_and_reports_counters(client: TestClient, repo: Path) -> None:
    response = client.post("/v1/selfmodel/index")
    assert response.status_code == 200
    report = response.json()["report"]

    assert report["modules_discovered"] > 0
    assert report["modules_reparsed"] > 0
    assert report["writes"] > 0
    assert response.json()["ui_frames"] > 0

    # Second call over the same untouched tree does nothing.
    again = client.post("/v1/selfmodel/index").json()["report"]
    assert again["modules_reparsed"] == 0
    assert again["writes"] == 0
    assert again["modules_skipped_unchanged"] == report["modules_discovered"]


def test_index_takes_no_path_from_the_caller(client: TestClient, repo: Path) -> None:
    """A forged root in the body must be ignored, not honoured."""
    response = client.post("/v1/selfmodel/index", json={"repo_root": "C:/"})
    assert response.status_code == 200

    listed = client.get("/v1/selfmodel/modules", params={"limit": 500}).json()["modules"]
    paths = {m["path"] for m in listed}
    assert "services/api/app/observer/diagnostic_observer.py" in paths


# ----------------------------------------------------------------- modules


def test_list_modules_and_filter_by_kind(indexed_client: TestClient) -> None:
    everything = indexed_client.get("/v1/selfmodel/modules", params={"limit": 500})
    assert everything.status_code == 200
    assert len(everything.json()["modules"]) > 5

    scripts = indexed_client.get("/v1/selfmodel/modules", params={"kind": "script"}).json()
    assert [m["module_id"] for m in scripts["modules"]] == ["scripts/Deploy-Observer.ps1"]


def test_unknown_kind_is_rejected(indexed_client: TestClient) -> None:
    response = indexed_client.get("/v1/selfmodel/modules", params={"kind": "nonsense"})
    assert response.status_code == 422
    assert response.json()["detail"]["error_class"] == "validation_error"


def test_module_status_shape(indexed_client: TestClient) -> None:
    response = indexed_client.get(f"/v1/selfmodel/modules/{FIXTURE_MODULE}")
    assert response.status_code == 200
    body = response.json()

    assert body["found"] is True
    assert body["module_id"] == FIXTURE_MODULE
    assert body["is_live"] is False
    assert body["production_state"] == "source_only"
    assert set(body["truths"]) == {"source"}
    assert body["resolution"]["matched_by"] == "exact"
    assert body["adr_refs"] == ["ADR-0099"]
    assert isinstance(body["confidence"], float)
    assert "no_runtime_evidence" in body["unknown"]


def test_module_status_accepts_a_spoken_name(indexed_client: TestClient) -> None:
    response = indexed_client.get("/v1/selfmodel/modules/Diagnostic Observer")
    assert response.status_code == 200
    assert response.json()["module_id"] == FIXTURE_MODULE


def test_module_status_accepts_a_path_shaped_key(indexed_client: TestClient) -> None:
    response = indexed_client.get("/v1/selfmodel/modules/services/browser/browser_agent/worker.py")
    assert response.status_code == 200
    assert response.json()["module_id"] == "services/browser/browser_agent/worker.py"


def test_unknown_module_is_404_with_candidates(indexed_client: TestClient) -> None:
    response = indexed_client.get("/v1/selfmodel/modules/app.observer.telemetry_sink")
    assert response.status_code == 404
    detail = response.json()["detail"]

    assert detail["found"] is False
    assert detail["reason"] == "module_id_not_found"
    assert FIXTURE_PACKAGE in detail["candidates"]


def test_problems_endpoint_is_reachable_under_the_path_key(indexed_client: TestClient) -> None:
    """The greedy path converter must not swallow the ``/problems`` suffix."""
    response = indexed_client.get(f"/v1/selfmodel/modules/{FIXTURE_MODULE}/problems")
    assert response.status_code == 200
    body = response.json()

    assert body["module_id"] == FIXTURE_MODULE
    assert body["has_problems"] is False
    assert body["open_incidents"] == []
    assert "no_runtime_evidence" in body["known_limitations"]


# ------------------------------------------------------------------ search


def test_search_returns_modules_and_symbols(indexed_client: TestClient) -> None:
    response = indexed_client.get("/v1/selfmodel/search", params={"q": "observ"})
    assert response.status_code == 200
    body = response.json()

    assert FIXTURE_MODULE in {m["module_id"] for m in body["modules"]}
    assert "observe" in {s["name"] for s in body["symbols"]}


def test_search_requires_a_query(indexed_client: TestClient) -> None:
    assert indexed_client.get("/v1/selfmodel/search").status_code == 422
    assert indexed_client.get("/v1/selfmodel/search", params={"q": ""}).status_code == 422


# ------------------------------------------------------------ capabilities


def test_a_capability_resolves_to_the_file_that_answers_it(indexed_client: TestClient) -> None:
    """ADR-0111. "observer.look failed" -> a module, a path, a line."""
    response = indexed_client.get("/v1/selfmodel/capabilities/observer.look")
    assert response.status_code == 200
    body = response.json()

    assert body["capability"] == "observer.look"
    assert body["implemented_by"]["module_id"] == FIXTURE_TOOLS_MODULE
    assert body["implemented_by"]["path"] == "services/api/app/observer/tools.py"
    assert body["implemented_by"]["lineno"] > 0
    assert body["implemented_by"]["signature"] == "observer.look -> observer_look()"
    assert body["confidence"] == 1.0


def test_a_device_capability_names_the_file_that_dispatches_it(
    indexed_client: TestClient,
) -> None:
    """Nothing in Cloud Core ANSWERS ``screen.read``; the device does. So the
    answer is the dispatcher, and the answer says that is what it is."""
    response = indexed_client.get("/v1/selfmodel/capabilities/screen.read")
    assert response.status_code == 200
    body = response.json()

    assert body["implemented_by"] is None
    assert [d["module_id"] for d in body["dispatched_by"]] == [FIXTURE_PLANS_MODULE]
    assert "no_module_in_this_service_implements_it" in body["unknown"]


def test_an_unknown_capability_is_a_404_carrying_what_the_index_does_have(
    indexed_client: TestClient,
) -> None:
    response = indexed_client.get("/v1/selfmodel/capabilities/observer.telepathy")
    assert response.status_code == 404
    detail = response.json()["detail"]

    assert detail["reason"] == "capability_not_indexed"
    assert "observer.look" in detail["candidates"]


def test_post_index_refuses_while_the_background_refresher_holds_the_index(
    client: TestClient, repo: Path
) -> None:
    """The route's own lock cannot see the refresher (ADR-0111 review finding 2).

    ``build_index`` serialises on a process-wide lock, so an overlapping POST is
    safe either way -- but waiting out a repository walk with no explanation is
    not an answer. The route reads the real lock and says so.

    The lock is held from ANOTHER thread and released on a deadline rather than
    held across the request: holding it here would make the un-wired version
    block forever on it, and a hanging test proves nothing (it did, on the first
    attempt). Released, the wrong version answers 200 and this fails on the
    status -- a red, not a stall.
    """
    import threading

    from app.selfmodel import indexer

    release = threading.Event()

    def hold_the_index() -> None:
        with indexer._INDEX_LOCK:
            release.wait(timeout=10.0)

    holder = threading.Thread(target=hold_the_index, daemon=True)
    holder.start()
    try:
        deadline = time.perf_counter() + 5.0
        while not indexer.index_running() and time.perf_counter() < deadline:
            time.sleep(0.01)
        assert indexer.index_running(), "the helper thread never took the lock"

        response = client.post("/v1/selfmodel/index")
    finally:
        release.set()
        holder.join(timeout=10.0)
    assert not holder.is_alive()

    assert response.status_code == 409
    assert response.json()["detail"]["error_class"] == "conflict"

    # ...and the refusal is not permanent: once the run finishes, it works.
    assert client.post("/v1/selfmodel/index").status_code == 200
