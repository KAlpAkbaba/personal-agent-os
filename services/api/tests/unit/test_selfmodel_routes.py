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

    assert body["selfmodel_version"] == SELFMODEL_VERSION == 1
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
