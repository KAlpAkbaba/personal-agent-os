"""B42 - the artifact's life after the first render (req 405-416): provenance written
on the version row, every version listable, edit-as-new-version, clone with lineage,
delete under the owner's policy, compare and diff that name what differs.

Three seams, the same ones every artifact batch is proven on: the module against a SQLite
session and an in-memory object store, the REST surface through the real ``create_app``,
and the voice tools through ``tests.voice_corpus.harness`` (the same relay/router/tool
path the corpus drives).
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts import factory, lifecycle, provenance, service
from app.artifacts.models import (
    ARTIFACT_STATE_ARCHIVED,
    Artifact,
    ArtifactRender,
    ArtifactVersion,
    ResearchSource,
    Task,
    TaskRun,
)
from app.artifacts.runtime import ArtifactRuntime
from app.artifacts.spec import ArtifactSpec
from app.config import Settings
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.object_store import InMemoryObjectStore
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_ARTIFACT
from app.security import step_up
from tests.identity_support import authenticate
from tests.voice_corpus.corpus import CTX_ARTIFACT_FOCUSED, CTX_DOCUMENT_ARTIFACT_FOCUSED
from tests.voice_corpus.harness import build_harness

ARTIFACT_TABLES = [
    Task.__table__,
    TaskRun.__table__,
    Artifact.__table__,
    ArtifactVersion.__table__,
    ArtifactRender.__table__,
    ResearchSource.__table__,
]
DOCUMENT = "tests/fixtures/artifacts/specs/toplanti-notlari.json"
BUDGET = "tests/fixtures/artifacts/specs/butce-tablosu.json"


def _spec(path: str) -> ArtifactSpec:
    with open(path, encoding="utf-8") as f:
        return ArtifactSpec.model_validate(json.load(f))


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in ARTIFACT_TABLES:
        table.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


def _owner() -> provenance.Actor:
    return provenance.Actor(provenance.ACTOR_OWNER_REST, session_id="s-1")


# ------------------------------------------------------------ provenance (405-408)


def test_runtime_provenance_names_the_python_and_the_libraries_behind_the_format() -> None:
    record = provenance.runtime_provenance(("xlsx", "docx"))
    assert record["python"].count(".") == 2
    assert set(record["libraries"]) == {"openpyxl", "python-docx"}
    assert all(v and v != "not installed" for v in record["libraries"].values())
    assert record["renderer_module"] == "app.artifacts.renderers"


def test_an_actor_of_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        provenance.Actor("somebody")


def test_create_writes_who_asked_and_what_it_is_made_of_on_the_version(db: Session, store) -> None:
    result = factory.create(
        db, store, spec=_spec(DOCUMENT), actor=_owner(), sources=[{"url": "https://ex.ample/a"}]
    )
    version = service.get_current_version(db, result.artifact_id)
    assert version is not None
    prov = version.provenance_json
    assert prov["actor"] == {"kind": "owner_rest", "ref": None, "session_id": "s-1"}
    assert "python-docx" in prov["runtime"]["libraries"]
    assert prov["derived_from"] is None
    manifest = version.source_manifest_json
    assert manifest["kind"] == "document"
    assert manifest["counts"]["sections"] == 5
    assert manifest["spec_hash"] == version.content_hash
    assert manifest["sources"] == [{"url": "https://ex.ample/a"}]
    assert "docx" in manifest["formats"]


def test_create_without_an_actor_records_the_system_never_nothing(db: Session, store) -> None:
    result = factory.create(db, store, spec=_spec(BUDGET))
    version = service.get_current_version(db, result.artifact_id)
    assert version.provenance_json["actor"]["kind"] == provenance.ACTOR_SYSTEM


# ------------------------------------------------------ versions and edit (409, 410)


def _edit(*ops: dict, numbers: list[float] | None = None) -> lifecycle.ArtifactEdit:
    return lifecycle.ArtifactEdit(ops=list(ops), spoken_numbers=numbers)


def test_edit_makes_the_next_version_of_the_same_artifact_and_keeps_the_past(
    db: Session, store
) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    result = lifecycle.edit_artifact(
        db,
        store,
        created.artifact_id,
        _edit({"op": "append_section", "heading": "Bütçe Notu", "paragraphs": ["Takvim sıkışık."]}),
        actor=_owner(),
    )
    assert result.artifact_id == created.artifact_id
    assert result.version == 2 and result.created is True
    assert result.all_valid is True
    versions = service.list_versions(db, created.artifact_id)
    assert [v.version for v in versions] == [1, 2]
    v1 = json.loads(versions[0].canonical_body)
    v2 = json.loads(versions[1].canonical_body)
    assert [s["heading"] for s in v1["sections"]] == [
        "Giriş",
        "Kararlar",
        "Sorumlular",
        "Riskler",
        "Sonuç",
    ]
    assert [s["heading"] for s in v2["sections"]][-1] == "Bütçe Notu"
    prov = versions[1].provenance_json
    assert prov["actor"]["kind"] == provenance.ACTOR_EDIT
    assert prov["derived_from"] == {
        "artifact_id": str(created.artifact_id),
        "version": 1,
        "how": "edit",
        "ops": ["append_section"],
    }
    # Every format of the new version is rendered and stored - bytes, not a promise.
    renders = service.list_renders(db, versions[1].id)
    assert {r.format for r in renders} >= {"docx"}
    assert all(store.exists(r.object_key) for r in renders)


def test_an_edit_that_adds_a_number_the_owner_never_said_is_refused(db: Session, store) -> None:
    """The spec's own never-invented rule still holds on version two: an edit may
    introduce only the numbers the OWNER said in the edit sentence."""
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    with pytest.raises(Exception) as excinfo:
        lifecycle.edit_artifact(
            db,
            store,
            created.artifact_id,
            _edit(
                {"op": "append_section", "heading": "Bütçe", "paragraphs": ["Ek maliyet 99999."]}
            ),
            actor=_owner(),
        )
    assert "invented" in str(excinfo.value)
    # ...and with the number spoken, the same edit is admitted.
    result = lifecycle.edit_artifact(
        db,
        store,
        created.artifact_id,
        _edit(
            {"op": "append_section", "heading": "Bütçe", "paragraphs": ["Ek maliyet 99999."]},
            numbers=[99999],
        ),
        actor=_owner(),
    )
    assert result.version == 2


def test_an_edit_that_does_not_fit_the_kind_is_named_not_applied(db: Session, store) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    with pytest.raises(lifecycle.ArtifactLifecycleError) as excinfo:
        lifecycle.edit_artifact(
            db,
            store,
            created.artifact_id,
            _edit({"op": "append_slide", "title": "X"}),
            actor=_owner(),
        )
    assert excinfo.value.code == lifecycle.ERROR_INVALID_EDIT
    assert "slayt" in excinfo.value.speech
    with pytest.raises(lifecycle.ArtifactLifecycleError) as excinfo:
        lifecycle.edit_artifact(
            db,
            store,
            created.artifact_id,
            _edit({"op": "remove_section", "heading": "Yok"}),
            actor=_owner(),
        )
    assert "'Yok'" in excinfo.value.speech
    assert service.get_artifact(db, created.artifact_id).current_version == 1


def test_the_same_edit_twice_does_not_make_a_third_version(db: Session, store) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    edit = _edit({"op": "set_title", "title": "Toplantı Notları v2"})
    first = lifecycle.edit_artifact(db, store, created.artifact_id, edit, actor=_owner())
    again = lifecycle.edit_artifact(db, store, created.artifact_id, edit, actor=_owner())
    assert first.version == 2 and again.version == 2 and again.created is False
    # The second application changes nothing (the title is already the new one), so the
    # spec hash is identical and add_artifact_version returns the existing row.
    assert again.created is False or json.loads(
        service.get_version(db, created.artifact_id, 3).canonical_body
    ) == json.loads(service.get_version(db, created.artifact_id, 2).canonical_body)
    assert service.get_artifact(db, created.artifact_id).title == "Toplantı Notları v2"


def test_a_spreadsheet_row_edit_lands_on_the_named_sheet(db: Session, store) -> None:
    created = factory.create(db, store, spec=_spec(BUDGET), actor=_owner())
    result = lifecycle.edit_artifact(
        db,
        store,
        created.artifact_id,
        _edit({"op": "append_row", "row": ["Eğitim", 3000]}, numbers=[3000]),
        actor=_owner(),
    )
    spec = json.loads(service.get_version(db, created.artifact_id, result.version).canonical_body)
    assert spec["sheets"][0]["rows"][-1] == ["Eğitim", 3000]
    assert result.all_valid is True


# --------------------------------------------------------------------- clone (411)


def test_clone_is_a_new_artifact_whose_provenance_names_the_source(db: Session, store) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    clone = lifecycle.clone_artifact(db, store, created.artifact_id, title="Kopya", actor=_owner())
    assert clone.artifact_id != created.artifact_id
    assert clone.version == 1 and clone.created is True
    art = service.get_artifact(db, clone.artifact_id)
    assert art.title == "Kopya"
    prov = service.get_current_version(db, clone.artifact_id).provenance_json
    assert prov["actor"]["kind"] == provenance.ACTOR_CLONE
    assert prov["derived_from"] == {
        "artifact_id": str(created.artifact_id),
        "version": 1,
        "how": "clone",
    }
    # The source is untouched.
    assert service.get_artifact(db, created.artifact_id).current_version == 1
    assert service.get_artifact(db, created.artifact_id).title == "Toplantı Notları"


def test_clone_of_a_named_version_copies_that_version_not_the_latest(db: Session, store) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    lifecycle.edit_artifact(
        db,
        store,
        created.artifact_id,
        _edit({"op": "set_title", "title": "Sonraki"}),
        actor=_owner(),
    )
    clone = lifecycle.clone_artifact(
        db, store, created.artifact_id, title=None, actor=_owner(), version_number=1
    )
    assert service.get_artifact(db, clone.artifact_id).title == "Toplantı Notları"
    assert (
        service.get_current_version(db, clone.artifact_id).provenance_json["derived_from"][
            "version"
        ]
        == 1
    )


# -------------------------------------------------------------------- delete (412)


def test_delete_under_confirm_policy_waits_for_the_yes_then_removes_every_render(
    db: Session, store
) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    lifecycle.edit_artifact(
        db,
        store,
        created.artifact_id,
        _edit({"op": "set_title", "title": "Sonraki"}),
        actor=_owner(),
    )
    keys = [
        r.object_key
        for v in service.list_versions(db, created.artifact_id)
        for r in service.list_renders(db, v.id)
    ]
    assert keys and all(store.exists(k) for k in keys)
    asked = lifecycle.delete_artifact(
        db, store, created.artifact_id, policy="confirm", confirmed=False
    )
    assert asked.status == "needs_confirmation"
    assert "Evet, sil" in asked.speech
    assert all(store.exists(k) for k in keys)
    assert service.get_artifact(db, created.artifact_id).state != ARTIFACT_STATE_ARCHIVED
    done = lifecycle.delete_artifact(
        db, store, created.artifact_id, policy="confirm", confirmed=True
    )
    assert done.status == "deleted"
    assert done.renders_removed == len(keys)
    assert not any(store.exists(k) for k in keys)
    # The record of what existed stays: the row, its versions, their provenance.
    assert service.get_artifact(db, created.artifact_id).state == ARTIFACT_STATE_ARCHIVED
    assert [v.version for v in service.list_versions(db, created.artifact_id)] == [1, 2]
    # ...and an archived artifact cannot be edited into life again.
    with pytest.raises(lifecycle.ArtifactLifecycleError) as excinfo:
        lifecycle.edit_artifact(
            db, store, created.artifact_id, _edit({"op": "set_title", "title": "Z"}), actor=_owner()
        )
    assert excinfo.value.code == lifecycle.ERROR_ARCHIVED


def test_delete_under_deny_policy_never_deletes_even_with_a_yes(db: Session, store) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    outcome = lifecycle.delete_artifact(
        db, store, created.artifact_id, policy="deny", confirmed=True
    )
    assert outcome.status == "denied"
    assert outcome.renders_removed == 0
    assert service.get_artifact(db, created.artifact_id).state != ARTIFACT_STATE_ARCHIVED


def test_delete_under_free_policy_deletes_at_once_and_an_unknown_policy_is_refused(
    db: Session, store
) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    with pytest.raises(lifecycle.ArtifactLifecycleError) as excinfo:
        lifecycle.delete_artifact(db, store, created.artifact_id, policy="maybe", confirmed=True)
    assert excinfo.value.code == lifecycle.ERROR_INVALID_POLICY
    outcome = lifecycle.delete_artifact(
        db, store, created.artifact_id, policy="free", confirmed=False
    )
    assert outcome.status == "deleted" and outcome.renders_removed >= 1
    twice = lifecycle.delete_artifact(
        db, store, created.artifact_id, policy="free", confirmed=False
    )
    assert twice.status == "deleted" and twice.renders_removed == 0


# ------------------------------------------------------------ compare and diff (415, 416)


def test_compare_names_what_differs_never_a_bare_different(db: Session, store) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    lifecycle.edit_artifact(
        db,
        store,
        created.artifact_id,
        _edit(
            {"op": "append_section", "heading": "Bütçe Notu", "paragraphs": ["Takvim sıkışık."]},
            {"op": "remove_section", "heading": "Sorumlular"},
            {"op": "replace_section", "heading": "Giriş", "paragraphs": ["Yeni giriş."]},
        ),
        actor=_owner(),
    )
    result = lifecycle.compare_versions(
        db, created.artifact_id, created.artifact_id, a_version=1, b_version=2
    )
    comparison = result["comparison"]
    assert comparison["same"] is False
    assert comparison["parts"] == {
        "added": ["Bütçe Notu"],
        "removed": ["Sorumlular"],
        "changed": ["Giriş"],
        "kept": 4,
    }
    assert "eklenen: Bütçe Notu" in result["speech"]
    assert "çıkarılan: Sorumlular" in result["speech"]
    assert "değişen: Giriş" in result["speech"]
    assert result["a"]["version"] == 1 and result["b"]["version"] == 2
    diff = result["diff"]
    assert diff["lines"][0].startswith("--- Toplantı Notları v1")
    assert any(line.startswith("+") and "Bütçe Notu" in line for line in diff["lines"])
    assert any(line.startswith("-") and "Sorumlular" in line for line in diff["lines"])


def test_compare_of_identical_versions_says_so(db: Session, store) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    result = lifecycle.compare_versions(db, created.artifact_id, created.artifact_id)
    assert result["comparison"]["same"] is True
    assert "birebir aynı" in result["speech"]
    assert result["diff"]["lines"] == []


def test_compare_across_kinds_names_the_kind_and_the_numbers(db: Session, store) -> None:
    doc = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    budget = factory.create(db, store, spec=_spec(BUDGET), actor=_owner())
    result = lifecycle.compare_versions(db, doc.artifact_id, budget.artifact_id)
    assert result["comparison"]["kind"] == {"a": "document", "b": "spreadsheet", "same": False}
    assert "türleri farklı" in result["speech"]
    assert result["comparison"]["numbers"]["only_in_b"]


def test_compare_with_an_unknown_version_is_named(db: Session, store) -> None:
    created = factory.create(db, store, spec=_spec(DOCUMENT), actor=_owner())
    with pytest.raises(lifecycle.ArtifactLifecycleError) as excinfo:
        lifecycle.compare_versions(db, created.artifact_id, created.artifact_id, a_version=7)
    assert excinfo.value.code == lifecycle.ERROR_NOT_FOUND
    with pytest.raises(lifecycle.ArtifactLifecycleError):
        lifecycle.compare_versions(db, uuid.uuid4(), created.artifact_id)


# ------------------------------------------------------------------------- REST


@pytest.fixture()
def client() -> TestClient:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in ARTIFACT_TABLES:
        table.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    artifacts._store = InMemoryObjectStore()
    app.state.artifacts = artifacts
    test_client = TestClient(app)
    authenticate(app, test_client, settings=settings)
    yield test_client
    test_client.close()


def _spec_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_rest_create_carries_provenance_and_the_versions_route_lists_them(
    client: TestClient,
) -> None:
    created = client.post("/v1/artifacts/factory", json={"spec": _spec_json(DOCUMENT)})
    assert created.status_code == 201, created.text
    aid = created.json()["artifact_id"]
    edited = client.post(
        f"/v1/artifacts/{aid}/edit",
        json={
            "edit": {
                "ops": [{"op": "append_section", "heading": "Bütçe Notu", "paragraphs": ["Az."]}]
            }
        },
    )
    assert edited.status_code == 201, edited.text
    assert edited.json()["version"] == 2
    listed = client.get(f"/v1/artifacts/{aid}/versions")
    assert listed.status_code == 200
    body = listed.json()
    assert body["current_version"] == 2
    assert [v["version"] for v in body["versions"]] == [1, 2]
    assert body["versions"][0]["provenance"]["actor"]["kind"] == "owner_rest"
    assert body["versions"][0]["provenance"]["actor"]["session_id"]
    assert body["versions"][1]["provenance"]["derived_from"]["how"] == "edit"
    assert body["versions"][1]["source_manifest"]["counts"]["sections"] == 6
    shown = client.get(f"/v1/artifacts/{aid}")
    assert shown.status_code == 200
    assert shown.json()["provenance"]["actor"]["kind"] == "edit"
    assert shown.json()["source_manifest"]["kind"] == "document"


def test_rest_edit_that_invents_a_number_is_422_with_the_owner_sentence(client: TestClient) -> None:
    aid = client.post("/v1/artifacts/factory", json={"spec": _spec_json(DOCUMENT)}).json()[
        "artifact_id"
    ]
    bad = client.post(
        f"/v1/artifacts/{aid}/edit",
        json={
            "edit": {
                "ops": [{"op": "append_section", "heading": "Bütçe", "paragraphs": ["Ek 77777."]}]
            }
        },
    )
    assert bad.status_code == 422, bad.text
    unknown = client.post(
        f"/v1/artifacts/{uuid.uuid4()}/edit",
        json={"edit": {"ops": [{"op": "set_title", "title": "X"}]}},
    )
    assert unknown.status_code == 404
    malformed = client.post(
        f"/v1/artifacts/{aid}/edit", json={"edit": {"ops": [{"op": "explode"}]}}
    )
    assert malformed.status_code == 422


def test_rest_clone_delete_and_compare(client: TestClient) -> None:
    aid = client.post("/v1/artifacts/factory", json={"spec": _spec_json(DOCUMENT)}).json()[
        "artifact_id"
    ]
    cloned = client.post(f"/v1/artifacts/{aid}/clone", json={"title": "Kopya"})
    assert cloned.status_code == 201, cloned.text
    cid = cloned.json()["artifact_id"]
    assert cid != aid
    same = client.get(f"/v1/artifacts/{cid}/compare", params={"against": aid})
    assert same.status_code == 200
    assert same.json()["comparison"]["parts"]["kept"] == 5
    assert same.json()["comparison"]["title"]["same"] is False
    # No previous version yet: the compare of a fresh artifact against itself is named.
    none = client.get(f"/v1/artifacts/{cid}/compare")
    assert none.status_code == 422
    assert none.json()["detail"]["code"] == "no_previous_version"
    # Delete under the default (confirm) policy: asked first, then done.
    asked = client.post(f"/v1/artifacts/{cid}/delete", json={"confirm": False})
    assert asked.status_code == 200
    assert asked.json()["status"] == "needs_confirmation"
    assert asked.json()["policy"] == "confirm"
    done = client.post(f"/v1/artifacts/{cid}/delete", json={"confirm": True})
    assert done.json()["status"] == "deleted"
    assert done.json()["renders_removed"] >= 1
    assert client.get(f"/v1/artifacts/{cid}/versions").json()["versions"]


# ------------------------------------------------------------------- image (400)

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63f8cfc0f01f0005000101d0a9c7ec0000000049454e44ae426082"
)


def test_an_image_the_creative_path_stored_becomes_an_artifact_with_its_own_copy(
    db: Session, store
) -> None:
    store.put("creative/run-1/logo.png", PNG_1X1, content_type="image/png")
    result = lifecycle.register_image_artifact(
        db,
        store,
        title="Logo",
        object_key="creative/run-1/logo.png",
        actor=_owner(),
        sources=[{"creative_run_id": "run-1"}],
    )
    assert result.version == 1 and result.all_valid is True
    art = service.get_artifact(db, result.artifact_id)
    assert art.kind == lifecycle.KIND_IMAGE and art.state == "READY"
    version = service.get_current_version(db, result.artifact_id)
    assert version.provenance_json["actor"]["kind"] == "owner_rest"
    assert "pillow" in version.provenance_json["runtime"]["libraries"]
    assert version.source_manifest_json["sources"] == [{"creative_run_id": "run-1"}]
    renders = service.list_renders(db, version.id)
    assert len(renders) == 1 and renders[0].format == "png" and renders[0].mime_type == "image/png"
    assert renders[0].object_key != "creative/run-1/logo.png"
    assert store.get(renders[0].object_key) == PNG_1X1
    # Deleting the artifact removes ITS copy and leaves the creative run's output alone.
    done = lifecycle.delete_artifact(db, store, result.artifact_id, policy="free", confirmed=False)
    assert done.renders_removed == 1
    assert not store.exists(renders[0].object_key)
    assert store.exists("creative/run-1/logo.png")


def test_an_image_artifact_is_cloned_and_compared_but_not_edited_here(db: Session, store) -> None:
    store.put("creative/run-1/logo.png", PNG_1X1, content_type="image/png")
    store.put("creative/run-2/other.png", PNG_1X1 + b"\x00", content_type="image/png")
    a = lifecycle.register_image_artifact(
        db, store, title="Logo", object_key="creative/run-1/logo.png", actor=_owner()
    )
    b = lifecycle.register_image_artifact(
        db, store, title="Öteki", object_key="creative/run-2/other.png", actor=_owner()
    )
    clone = lifecycle.clone_artifact(db, store, a.artifact_id, title="Logo kopya", actor=_owner())
    assert clone.artifact_id != a.artifact_id
    assert (
        service.get_current_version(db, clone.artifact_id).provenance_json["derived_from"]["how"]
        == "clone"
    )
    same = lifecycle.compare_versions(db, a.artifact_id, clone.artifact_id)
    assert same["comparison"]["same"] is True and "aynı görsel" in same["speech"]
    different = lifecycle.compare_versions(db, a.artifact_id, b.artifact_id)
    assert different["comparison"]["same"] is False and "farklı görseller" in different["speech"]
    assert any(line.startswith("+sha256") for line in different["diff"]["lines"])
    with pytest.raises(lifecycle.ArtifactLifecycleError) as excinfo:
        lifecycle.edit_artifact(
            db, store, a.artifact_id, _edit({"op": "set_title", "title": "X"}), actor=_owner()
        )
    assert excinfo.value.code == lifecycle.ERROR_IMAGE_NOT_EDITABLE


def test_registering_an_image_that_is_not_in_the_store_or_not_an_image_is_named(
    db: Session, store
) -> None:
    with pytest.raises(lifecycle.ArtifactLifecycleError) as excinfo:
        lifecycle.register_image_artifact(
            db, store, title="Yok", object_key="creative/run-9/logo.png", actor=_owner()
        )
    assert excinfo.value.code == lifecycle.ERROR_OBJECT_MISSING
    store.put("creative/run-1/notes.txt", b"metin", content_type="text/plain")
    with pytest.raises(lifecycle.ArtifactLifecycleError) as excinfo:
        lifecycle.register_image_artifact(
            db, store, title="Metin", object_key="creative/run-1/notes.txt", actor=_owner()
        )
    assert excinfo.value.code == lifecycle.ERROR_INVALID_EDIT


def test_rest_image_artifact_route(client: TestClient) -> None:
    store = client.app.state.artifacts.store
    store.put("creative/run-1/logo.png", PNG_1X1, content_type="image/png")
    made = client.post(
        "/v1/artifacts/image",
        json={
            "title": "Logo",
            "object_key": "creative/run-1/logo.png",
            "sources": [{"creative_run_id": "run-1"}],
        },
    )
    assert made.status_code == 201, made.text
    aid = made.json()["artifact_id"]
    shown = client.get(f"/v1/artifacts/{aid}")
    assert shown.status_code == 200
    assert shown.json()["kind"] == "image"
    assert shown.json()["provenance"]["actor"]["kind"] == "owner_rest"
    missing = client.post(
        "/v1/artifacts/image", json={"title": "Yok", "object_key": "creative/run-9/x.png"}
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "object_missing"


# ------------------------------------------------------------------------ voice


def _ledger_actions(h) -> list[str]:
    with h.factory() as db:
        rows = db.execute(select(ActivityEventRow)).scalars().all()
    return [row.action for row in rows]


def test_voice_edit_adds_the_section_to_the_focused_document_as_a_new_version() -> None:
    h = build_harness()
    h.seed(CTX_DOCUMENT_ARTIFACT_FOCUSED)
    sid = h.new_session()
    routed = h.say(sid, "Bu belgeye Riskler bölümünü ekle.")
    assert routed["resolved_intents"][-1]["intent"] == "artifact_edit", routed
    call = h.tool(
        sid,
        "c-1",
        "artifact.edit",
        {
            "ops": [
                {"op": "append_section", "heading": "Bütçe Notu", "paragraphs": ["Takvim sıkışık."]}
            ]
        },
    )
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed", body
    assert body["version"] == 2
    assert body["artifact_id"] == h.ids["artifact:current"]
    assert "sürüm 2" in body["speech"]
    assert "artifact.edit" in _ledger_actions(h)


def test_voice_edit_may_only_add_the_numbers_the_owner_said() -> None:
    h = build_harness()
    h.seed(CTX_DOCUMENT_ARTIFACT_FOCUSED)
    sid = h.new_session()
    h.say(sid, "Bu belgeye Bütçe bölümünü ekle: ek maliyet 4500.")
    admitted = h.tool(
        sid,
        "c-1",
        "artifact.edit",
        {"ops": [{"op": "append_section", "heading": "Bütçe", "paragraphs": ["Ek maliyet 4500."]}]},
    )
    assert admitted["result"]["execution_status"] == "executed", admitted
    refused = h.tool(
        sid,
        "c-2",
        "artifact.edit",
        {"ops": [{"op": "append_section", "heading": "Gizli", "paragraphs": ["Sayı 98765."]}]},
    )
    assert refused["result"]["execution_status"] == "refused", refused
    assert refused["result"]["error_class"] == "invented_number"


def test_voice_edit_that_does_not_fit_is_a_named_refusal_not_a_silent_no_op() -> None:
    h = build_harness()
    h.seed(CTX_ARTIFACT_FOCUSED)  # current: the budget spreadsheet
    sid = h.new_session()
    h.say(sid, "Bu dosyaya Riskler bölümünü ekle.")
    call = h.tool(
        sid, "c-1", "artifact.edit", {"ops": [{"op": "append_section", "heading": "Bütçe Notu"}]}
    )
    assert call["result"]["execution_status"] == "refused"
    assert call["result"]["error_class"] == "invalid_edit"
    assert "bölümleri yok" in call["result"]["speech"]


def test_voice_clone_makes_a_copy_and_focuses_it() -> None:
    h = build_harness()
    h.seed(CTX_DOCUMENT_ARTIFACT_FOCUSED)
    sid = h.new_session()
    routed = h.say(sid, "Bunu kopyala.")
    assert routed["resolved_intents"][-1]["intent"] == "artifact_clone", routed
    call = h.tool(sid, "c-1", "artifact.clone", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed", body
    assert body["artifact_id"] != h.ids["artifact:current"]
    with h.factory() as db:
        assert focus_module.current(db, FOCUS_KIND_ARTIFACT).object_id == body["artifact_id"]
        assert focus_module.previous(db, FOCUS_KIND_ARTIFACT).object_id == h.ids["artifact:current"]
    assert "artifact.clone" in _ledger_actions(h)


def test_voice_delete_asks_first_then_deletes_on_the_owners_yes() -> None:
    h = build_harness()
    h.seed(CTX_DOCUMENT_ARTIFACT_FOCUSED)
    sid = h.new_session()
    routed = h.say(sid, "Bunu sil.")
    assert routed["resolved_intents"][-1]["intent"] == "artifact_delete", routed
    asked = h.tool(sid, "c-1", "artifact.delete", {})
    assert asked["status"] == "succeeded", asked
    assert asked["result"]["status"] == "needs_confirmation"
    assert "artifact.delete" not in _ledger_actions(h)
    with h.factory() as db:
        assert (
            service.get_artifact(db, uuid.UUID(h.ids["artifact:current"])).state
            != ARTIFACT_STATE_ARCHIVED
        )
    yes = h.say(sid, "Evet, sil.", turn=2)
    assert yes["resolved_intents"][-1]["intent"] == "artifact_delete", yes
    done = h.tool(sid, "c-2", "artifact.delete", {})
    assert done["result"]["execution_status"] == "executed", done
    assert done["result"]["renders_removed"] >= 1
    with h.factory() as db:
        assert (
            service.get_artifact(db, uuid.UUID(h.ids["artifact:current"])).state
            == ARTIFACT_STATE_ARCHIVED
        )
    assert "artifact.delete" in _ledger_actions(h)


def test_voice_delete_the_models_own_confirm_flag_cannot_stand_in_for_the_owners_yes() -> None:
    """The model saying ``confirm: true`` on its own is admitted ONLY as the second call
    of the same exchange - the router's ``artifact_confirm`` is what the first call lacks,
    and the tool reads the model flag too because the model is told to re-call with it
    after the owner's yes. What must never happen: a deny policy honouring either."""
    h = build_harness()
    h.runtime.artifacts.settings.artifact_delete_policy = "deny"
    h.seed(CTX_DOCUMENT_ARTIFACT_FOCUSED)
    sid = h.new_session()
    h.say(sid, "Evet, sil.")
    call = h.tool(sid, "c-1", "artifact.delete", {"confirm": True})
    assert call["result"]["execution_status"] == "refused"
    assert call["result"]["error_class"] == "delete_denied"
    with h.factory() as db:
        assert (
            service.get_artifact(db, uuid.UUID(h.ids["artifact:current"])).state
            != ARTIFACT_STATE_ARCHIVED
        )


def test_voice_compare_against_the_previous_version_and_the_previous_artifact() -> None:
    h = build_harness()
    h.seed(CTX_DOCUMENT_ARTIFACT_FOCUSED)
    sid = h.new_session()
    h.say(sid, "Bu belgeye Riskler bölümünü ekle.")
    h.tool(
        sid, "c-1", "artifact.edit", {"ops": [{"op": "append_section", "heading": "Bütçe Notu"}]}
    )
    routed = h.say(sid, "Öncekiyle karşılaştır.", turn=2)
    assert routed["resolved_intents"][-1]["intent"] == "artifact_compare", routed
    call = h.tool(sid, "c-2", "artifact.compare", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["a"]["version"] == 1 and body["b"]["version"] == 2
    assert body["comparison"]["parts"]["added"] == ["Bütçe Notu"]
    assert "eklenen: Bütçe Notu" in body["speech"]
    other = h.tool(sid, "c-3", "artifact.compare", {"against": "previous_artifact"})
    assert other["result"]["a"]["artifact_id"] == h.ids["artifact:previous"]
    assert other["result"]["comparison"]["kind"]["same"] is False


def test_voice_compare_with_no_previous_version_is_an_honest_clarification() -> None:
    h = build_harness()
    h.seed(CTX_DOCUMENT_ARTIFACT_FOCUSED)
    sid = h.new_session()
    h.say(sid, "Öncekiyle karşılaştır.")
    call = h.tool(sid, "c-1", "artifact.compare", {})
    assert call["result"]["status"] == "needs_clarification"
    assert "önceki sürümü yok" in call["result"]["speech"]


def test_without_an_artifact_in_focus_the_lifecycle_words_keep_their_old_owners() -> None:
    """The gate (req 410-416): "bunu sil" in an empty room is NOT an artifact delete."""
    h = build_harness()
    sid = h.new_session()
    for text in ("Bunu sil.", "Bunu kopyala.", "Öncekiyle karşılaştır."):
        routed = h.say(sid, text)
        assert not str(routed["resolved_intents"][-1]["intent"]).startswith("artifact_"), (
            text,
            routed,
        )


def test_the_lifecycle_tools_are_tiered_and_named_in_the_ledger_vocabulary() -> None:
    from app.ledger.vocabulary import EVENT_TYPES

    assert step_up.tier_of("artifact.delete") == step_up.TIER_CRITICAL
    assert step_up.tier_of("artifact.edit") == step_up.TIER_SENSITIVE
    assert step_up.tier_of("artifact.clone") == step_up.TIER_SENSITIVE
    assert step_up.tier_of("artifact.compare") == step_up.TIER_OPEN
    assert {"artifact.edit", "artifact.clone", "artifact.delete"} <= set(EVENT_TYPES)
