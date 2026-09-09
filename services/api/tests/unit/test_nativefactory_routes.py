"""The REST surface, through the REAL application object.

`app.main.create_app` and a real `TestClient`, not a router called directly: a route that
works when you call its function and 404s through the app has not been tested. That is the
"integration through the real application object is part of done" rule the development
policy states.

What this suite is mostly about is the two things the surface must NOT do — start a build,
and report a state the row does not hold — plus the distinction between "I never made
that" (404) and "I made it and it produced nothing" (409), which are different sentences
for the owner.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.nativefactory.models import (
    STATE_FAILED,
    STATE_UNAVAILABLE,
    STATE_VERIFIED,
    NativeBuildRow,
)
from tests.voice_corpus.harness import build_harness


@pytest.fixture()
def h():
    """The corpus harness: the REAL application object, real router, real services.

    `native_builds` is created here rather than added to the harness's own TABLES list,
    because that file belongs to the voice corpus and this suite should not reach into it.
    `checkfirst` keeps it correct either way, so the day it IS listed there nothing breaks.
    """
    harness = build_harness()
    with harness.factory() as session:
        NativeBuildRow.__table__.create(session.get_bind(), checkfirst=True)
    return harness


def _row(**overrides) -> NativeBuildRow:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "slug": "notlarim",
        "display_name": "Notlarım",
        "stack": "dotnet_wpf",
        "template": "notes-desktop",
        "target": "windows_exe",
        "version": "0.1.0",
        "state": STATE_VERIFIED,
        "spec_json": {"name": "Notlarim", "template": "notes-desktop",
                      "targets": ["windows_exe"], "version": "0.1.0"},
        "artifact_json": {
            "size_bytes": 162304, "sha256": "a" * 64, "version": "0.1.0",
            "architecture": "x64", "subsystem": "windows_gui",
        },
        "verdict_json": {"ok": True, "mismatches": []},
        "tests_json": {"passed": True, "summary": "Passed! - Failed: 0, Passed: 5"},
        "artifact_path": r"C:\builds\notlarim\notlarim.exe",
        "attempt": 1,
        "created_at": now,
        "updated_at": now,
    }
    return NativeBuildRow(**{**base, **overrides})


def test_the_list_is_a_read_of_the_rows(h) -> None:
    with h.factory() as db:
        db.add(_row())
        db.commit()

    body = h.client.get("/v1/native").json()
    assert len(body["builds"]) == 1
    build = body["builds"][0]
    assert build["state"] == STATE_VERIFIED
    assert build["artifact"]["size_bytes"] == 162304
    assert build["artifact"]["name"] == "notlarim.exe"
    # The panel and the voice read the same sentence off the same row, so they can never
    # say different things about one build.
    assert "hazır" in build["speech"]


def test_an_unavailable_row_carries_its_reason_not_a_failure(h) -> None:
    with h.factory() as db:
        db.add(
            _row(
                target="android_apk",
                state=STATE_UNAVAILABLE,
                artifact_json=None,
                verdict_json=None,
                artifact_path=None,
                error_class="dependency_unavailable",
                error_message="Android SDK burada ama Java yok efendim.",
            )
        )
        db.commit()

    build = h.client.get("/v1/native").json()["builds"][0]
    assert build["state"] == STATE_UNAVAILABLE
    assert build["error_class"] == "dependency_unavailable"
    assert build["artifact"] is None
    assert "Java yok" in build["speech"]


def test_an_unknown_build_is_404(h) -> None:
    assert h.client.get(f"/v1/native/{uuid.uuid4()}").status_code == 404


def test_a_build_with_no_artefact_is_409_not_404(h) -> None:
    """"I never made that" and "I made it and it produced nothing" are different answers,
    and an owner acts differently on each."""
    row = _row(state=STATE_FAILED, artifact_path=None, artifact_json=None,
               error_class="build_failed", error_message="error CS0103")
    with h.factory() as db:
        db.add(row)
        db.commit()

    response = h.client.get(f"/v1/native/{row.id}/artifact")
    assert response.status_code == 409
    assert response.json()["detail"]["error_class"] == "build_failed"


def test_an_artefact_whose_file_is_gone_is_410(h) -> None:
    """The row says a file was made and it is not there any more - which is neither a
    missing build nor a build that produced nothing."""
    row = _row(artifact_path=r"C:\builds\gone\notlarim.exe")
    with h.factory() as db:
        db.add(row)
        db.commit()

    response = h.client.get(f"/v1/native/{row.id}/artifact")
    assert response.status_code == 410
    assert response.json()["detail"]["error_class"] == "artifact_gone"


def test_the_toolchain_route_reports_what_is_measured(h) -> None:
    body = h.client.get("/v1/native/toolchain").json()
    assert set(body["can"]) == {"windows", "msix", "android", "ios"}
    # Not a flag anyone could flip on this machine, so it is a fact rather than a capability.
    assert body["can"]["ios"] is False
    assert "dotnet" in body["facts"]


def test_the_surface_offers_no_way_to_start_a_build(h) -> None:
    """A REST verb that kicked off a twenty-minute compile from a panel would be a way to
    start work nobody was watching. Asserted from the app's OWN route table, so adding one
    later fails here rather than being noticed in review."""
    # Read from the app's OWN published contract rather than from a route object, which
    # is what the app actually promises callers - and it catches a route added but never
    # called, which a behavioural probe alone would not.
    schema = h.client.get("/openapi.json").json()
    native = {
        path: set(operations)
        for path, operations in schema["paths"].items()
        if path.startswith("/v1/native")
    }
    assert native, "the native router is not registered at all"
    for path, methods in native.items():
        assert methods <= {"get", "head"}, (
            f"{path} accepts {sorted(methods)}: the native surface is read-only"
        )

    # ...and the promise holds in practice, not only on paper.
    assert h.client.post("/v1/native", json={}).status_code == 405
    assert h.client.delete(f"/v1/native/{uuid.uuid4()}").status_code == 405
