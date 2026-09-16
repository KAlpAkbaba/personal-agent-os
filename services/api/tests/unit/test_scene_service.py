"""``SceneService`` (docs/M25_CREATIVE_3D_SPEC.md §2-§4, ADR-0088): create -> apply ->
inspect -> compare -> render, against SQLite + the fake device
(``tests.creative3d_support.FakeCreative3DDevice``) — the same harness discipline
``test_appfactory_service.py`` establishes for its own device-calling service.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.creative3d.models import (
    STATE_APPLIED,
    STATE_DEPENDENCY_UNAVAILABLE,
    STATE_MISMATCH,
    SceneRow,
)
from app.creative3d.service import (
    MAX_ACTIVE_SCENES,
    PROJECT_ROOT_3D,
    RUN_COMMAND_KEY,
    DriverPinMismatch,
    SceneService,
    driver_text,
    run_command,
)
from app.ledger.models import ActivityEventRow
from app.object_store import InMemoryObjectStore
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_SCENE, ObjectFocusRow
from app.uistate.contract import SCENE_ACTIVITY_STEPS
from tests.alarms_support import FakeDeviceAction
from tests.creative3d_support import UNITY_LICENSE_MESSAGE, FakeCreative3DDevice

TABLES = [SceneRow.__table__, ObjectFocusRow.__table__, ActivityEventRow.__table__]


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def fake_device(tmp_path) -> FakeCreative3DDevice:
    return FakeCreative3DDevice(unity_available=False, render_dir=tmp_path)


@pytest.fixture()
def device(fake_device: FakeCreative3DDevice) -> FakeDeviceAction:
    return FakeDeviceAction(results=fake_device.capability_results())


@pytest.fixture()
def service() -> SceneService:
    return SceneService(object_store=InMemoryObjectStore())


BLENDER_CREATE_PLAN = {
    "tool": "blender",
    "project": "lab",
    "scene": "demo",
    "operations": [
        {"op": "create_scene"},
        {"op": "add_primitive", "kind": "sphere", "name": "Kure", "location": [0.0, 0.0, 0.0]},
    ],
}


def test_create_scaffolds_and_applies_and_sets_focus(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    result = service.create(db, device, plan=BLENDER_CREATE_PLAN, session_id="s-1")
    assert result["execution_status"] == "executed", result
    # The receipt carries the STEP a client reads; the row below keeps the database's word.
    assert result["state"] == "verified"
    assert result["objects"] == 1
    assert device.capabilities_called() == ["project.scaffold", "project.run", "scene.inspect"]

    scene_id = result["scene_id"]
    row = db.get(SceneRow, uuid.UUID(scene_id))
    assert row is not None
    assert row.state == STATE_APPLIED
    assert row.inspection_json["objects"][0]["name"] == "Kure"

    current = focus_module.current(db, FOCUS_KIND_SCENE)
    assert current is not None
    assert current.object_id == scene_id


def test_create_with_an_invalid_plan_never_reaches_the_device(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    bad_plan = {"tool": "blender", "project": "LAB", "scene": "demo", "operations": []}
    result = service.create(db, device, plan=bad_plan)
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "validation_error"
    assert device.calls == []
    assert db.query(SceneRow).count() == 0


def test_create_refuses_a_project_naming_the_owners_real_path(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    bad_plan = {
        "tool": "blender",
        "project": r"E:\hologram\HologramVehicleTest",
        "scene": "demo",
        "operations": [{"op": "create_scene"}],
    }
    result = service.create(db, device, plan=bad_plan)
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "validation_error"
    assert device.calls == []


def test_apply_adds_a_primitive_onto_the_existing_scene(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    created = service.create(db, device, plan=BLENDER_CREATE_PLAN)
    scene_id = created["scene_id"]

    result = service.apply(
        db,
        device,
        target=scene_id,
        operations=[
            {"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [1.0, 1.0, 1.0]}
        ],
        capability="scene.add",
    )
    assert result["execution_status"] == "executed", result
    assert result["objects"] == 2  # Kure from create() + Kup from apply(), same persistent state
    assert "eklendi" in result["speech"]
    assert "Kup" in result["speech"]


def test_apply_with_no_scene_created_yet_is_a_clarification(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    result = service.apply(db, device, target="current", operations=[{"op": "inspect"}])
    assert result["status"] == "needs_clarification"
    assert device.calls == []


def test_render_produces_a_stored_nontrivial_png(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    created = service.create(db, device, plan=BLENDER_CREATE_PLAN)
    scene_id = created["scene_id"]
    # A camera is required for a real render (the M25 lesson mirrored from Blender
    # itself: no camera, no render) — add one and aim it first.
    service.apply(
        db,
        device,
        target=scene_id,
        operations=[
            {
                "op": "add_primitive",
                "kind": "camera",
                "name": "Kamera",
                "location": [0.0, -5.0, 0.0],
            }
        ],
    )
    service.apply(
        db,
        device,
        target=scene_id,
        operations=[{"op": "set_camera", "name": "Kamera", "look_at": "Kure"}],
    )
    result = service.render(db, device, target=scene_id, width=64, height=48)
    assert result["execution_status"] == "executed", result
    assert result["state"] == "verified"

    row = db.get(SceneRow, uuid.UUID(scene_id))
    assert row.render_object_key is not None
    assert row.render_bytes is not None and row.render_bytes > 0
    stored = service._object_store.get(row.render_object_key)  # noqa: SLF001 - assert the real bytes landed
    assert len(stored) == row.render_bytes


def test_inspect_reads_back_without_mutating_state(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    created = service.create(db, device, plan=BLENDER_CREATE_PLAN)
    scene_id = created["scene_id"]
    device.calls.clear()
    result = service.inspect(db, device, target=scene_id)
    assert result["execution_status"] == "executed"
    assert result["objects"] == 1
    assert device.capabilities_called() == ["scene.inspect"]  # never scaffold/run again


def test_unity_licence_refusal_is_honest_never_a_crash_or_done(db: Session) -> None:
    fake_unity_device = FakeCreative3DDevice(unity_available=False)
    device = FakeDeviceAction(results=fake_unity_device.capability_results())
    service = SceneService()
    plan = {
        "tool": "unity",
        "project": "lab",
        "scene": "demo",
        "operations": [{"op": "create_scene"}],
    }
    result = service.create(db, device, plan=plan)
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "dependency_unavailable"
    assert UNITY_LICENSE_MESSAGE in result["speech"]

    row = db.query(SceneRow).one()
    assert row.state == STATE_DEPENDENCY_UNAVAILABLE

    # A subsequent apply against the same row stays honest without calling the
    # device again — never silently "succeeds".
    device.calls.clear()
    result2 = service.apply(db, device, target=str(row.id), operations=[{"op": "inspect"}])
    assert result2["execution_status"] == "refused"
    assert device.calls == []


def test_unity_available_actually_applies(db: Session) -> None:
    fake_unity_device = FakeCreative3DDevice(unity_available=True)
    device = FakeDeviceAction(results=fake_unity_device.capability_results())
    service = SceneService()
    plan = {
        "tool": "unity",
        "project": "lab",
        "scene": "demo",
        "operations": [
            {"op": "create_scene"},
            {"op": "add_primitive", "kind": "cube", "name": "Kup", "location": [0.0, 0.0, 0.0]},
        ],
    }
    result = service.create(db, device, plan=plan)
    assert result["execution_status"] == "executed", result
    assert result["objects"] == 1


def test_two_scene_cap(db: Session, device: FakeDeviceAction, service: SceneService) -> None:
    plan_a = {**BLENDER_CREATE_PLAN, "project": "lab", "scene": "a"}
    plan_b = {**BLENDER_CREATE_PLAN, "project": "lab", "scene": "b"}
    plan_c = {**BLENDER_CREATE_PLAN, "project": "lab", "scene": "c"}
    r1 = service.create(db, device, plan=plan_a)
    assert r1["execution_status"] == "executed"
    r2 = service.create(db, device, plan=plan_b)
    assert r2["execution_status"] == "executed"
    r3 = service.create(db, device, plan=plan_c)
    assert r3["execution_status"] == "refused"
    assert r3["error_class"] == "invalid_argument"
    assert db.query(SceneRow).count() == 2 + 0  # the third never got created at all
    assert MAX_ACTIVE_SCENES == 2


def test_no_device_runtime_refuses_honestly(db: Session, service: SceneService) -> None:
    result = service.create(db, None, plan=BLENDER_CREATE_PLAN)
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "capability_missing"


def test_status_and_list(db: Session, device: FakeDeviceAction, service: SceneService) -> None:
    created = service.create(db, device, plan=BLENDER_CREATE_PLAN)
    scene_id = created["scene_id"]
    status = service.status(db, target=scene_id)
    # The step, in the vocabulary every client reads — and the sentence the owner hears is
    # Turkish, not the database's English token ("... sahnesi applied durumunda efendim").
    assert status["state"] == "verified"
    assert "applied" not in status["speech"]
    assert "doğrulandı" in status["speech"]
    listing = service.list(db)
    assert listing["execution_status"] == "executed"
    assert len(listing["scenes"]) == 1
    assert listing["scenes"][0]["scene_id"] == scene_id


# ------------------------------------------------- what the M25 security review found


def test_run_command_is_the_argv_the_device_allowlist_requires() -> None:
    """The service used to send the bare word "blender"/"unity" as the run command, so
    `project.scaffold` would have refused at the first device call, every time — and the
    unit tests missed it because the fake device echoed success without reading the command
    text. These are the exact token shapes DEVICE_PROTOCOL.md §6m matches, and this test is
    the Python half of that one contract."""
    # `--factory-startup` is the FIRST flag of both forms and is never optional: without it
    # Blender loads the owner's own installed add-ons into the run, and on the owner's
    # machine one of them starts a watchdog thread that never stops, so the editor never
    # exits (measured 2026-09-08 by running this very command against the real editor). The
    # scene file is what varies: absent on a project's first run, because a `.blend` that
    # does not exist cannot be opened and `project.scaffold` writes text only.
    first = run_command("blender", existing_scene=False).split()
    assert first[0:4] == ["blender", "--factory-startup", "-b", "--python"]
    assert first[4].endswith(".py")
    assert first[5] == "--"
    assert first[6].endswith(".json") and first[7].endswith(".json")
    assert len(first) == 8

    later = run_command("blender", existing_scene=True).split()
    assert later[0:3] == ["blender", "--factory-startup", "-b"]
    assert later[3] == "scene.blend"
    assert later[4] == "--python" and later[6] == "--"
    assert len(later) == 9
    # The one token that must never be dropped between the two forms.
    assert "--factory-startup" in later and "--factory-startup" in first

    unity = run_command("unity", existing_scene=True).split()
    assert unity[0:8] == [
        "unity",
        "-batchmode",
        "-quit",
        "-projectPath",
        "<root>",
        "-executeMethod",
        "PagentOS.SceneDriver.Run",
        "-planPath",
    ]
    # B50: never -nographics - without a graphics device the editor's render is one flat
    # colour (measured on the first licensed run).
    assert "-nographics" not in unity
    assert unity[9] == "-outPath" and unity[11] == "-logFile"
    assert len(unity) == 13


def test_the_scaffold_asks_for_the_3d_root_and_sends_the_real_command(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    """Two halves of the same defect: the 3D editors are refused outside the 3D root, and
    the manifest must carry the real argv rather than the tool's name."""
    service.create(db, device, plan=BLENDER_CREATE_PLAN, session_id="s-1")
    payload = device.payload_for("project.scaffold")
    assert payload is not None
    assert payload["root"] == PROJECT_ROOT_3D
    command = payload["manifest"]["run"][RUN_COMMAND_KEY["blender"]]
    assert command != "blender"
    assert command == run_command("blender", existing_scene=False)
    assert {f["path"] for f in payload["files"]} == {"plan.json", "blender_driver.py"}


def test_a_driver_whose_bytes_do_not_match_its_pin_is_never_shipped(monkeypatch) -> None:
    """The pin lived only in a test: a driver edited after merge would have been shipped to
    the device and run with nothing noticing."""
    import app.creative3d.service as service_module

    original = service_module._DRIVERS_DIR

    class _Tampered:
        def __truediv__(self, name: str):
            class _F:
                @staticmethod
                def read_bytes() -> bytes:
                    return b"# not the pinned driver\n"

                @staticmethod
                def read_text(encoding: str = "utf-8") -> str:
                    return (original / name).read_text(encoding=encoding)

            return _F() if name.endswith(".py") else original / name

    monkeypatch.setattr(service_module, "_DRIVERS_DIR", _Tampered())
    with pytest.raises(DriverPinMismatch):
        driver_text("blender")


def test_a_read_back_that_disagrees_is_never_reported_as_verified(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    """The review's own PoC: a plan that transforms an object which was never created used
    to answer "executed / verified" and speak a success sentence, while the tool's own
    inspection proved the object absent. The comparison decides the state now."""
    plan = {
        "tool": "blender",
        "project": "lab",
        "scene": "demo",
        "operations": [
            {"op": "create_scene"},
            {"op": "transform", "name": "Kup", "location": [1.0, 0.0, 0.0]},
        ],
    }
    result = service.create(db, device, plan=plan, session_id="s-1")
    assert result["execution_status"] == "executed"
    assert result["terminal_status"] == "unverified"
    assert result["state"] == STATE_MISMATCH
    assert result["compare"]["ok"] is False
    assert "Uyuşmazlık" in result["speech"]
    assert "Kup" in result["speech"]
    row = db.get(SceneRow, uuid.UUID(result["scene_id"]))
    assert row is not None and row.state == STATE_MISMATCH
    events = db.query(ActivityEventRow).all()
    assert any(e.event_type == "scene.mismatch" for e in events)


def test_a_matching_read_back_is_still_verified(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    """The other side of the same branch, so the fix cannot have turned every run into a
    mismatch."""
    result = service.create(db, device, plan=BLENDER_CREATE_PLAN, session_id="s-1")
    assert result["terminal_status"] == "verified"
    assert result["state"] == "verified"
    assert result["compare"]["ok"] is True
    # And the row keeps its own word, which is a different vocabulary on purpose.
    row = db.get(SceneRow, uuid.UUID(result["scene_id"]))
    assert row is not None and row.state == STATE_APPLIED


def test_a_plan_with_nothing_to_check_is_neither_verified_nor_a_mismatch(
    db: Session, device: FakeDeviceAction, service: SceneService
) -> None:
    """An empty scene asks for nothing checkable, and `compare()` says so with `ok=False`
    and `no_constraints` — on purpose, because claiming a match over an empty comparison is
    the M24 defect this loop refuses. But the run succeeded at exactly what was asked, so
    calling it a disagreement is the review's own finding pointing the other way. Found by
    the route test driving "Blender'da yeni sahne aç." through the real application object,
    after the first version of that fix made every `not ok` a mismatch."""
    plan = {
        "tool": "blender",
        "project": "lab",
        "scene": "bos",
        "operations": [{"op": "create_scene"}],
    }
    result = service.create(db, device, plan=plan, session_id="s-1")
    assert result["execution_status"] == "executed"
    # Not a mismatch: nothing disagreed. The row stays `applied`; the step the client reads
    # is `unverified`, which is neither of the two words it must never be rounded to.
    assert db.get(SceneRow, uuid.UUID(result["scene_id"])).state == STATE_APPLIED
    assert result["state"] == "unverified"
    assert not any(e.event_type == "scene.mismatch" for e in db.query(ActivityEventRow).all())
    assert "Uyuşmazlık" not in result["speech"]
    # And not verified either: nothing was read back to verify.
    assert result["terminal_status"] == "unverified"
    assert result["compare"]["ok"] is False
    assert result["compare"]["reason"] == "no_constraints"
    assert "Doğrulanacak bir şey yoktu" in result["speech"]


# ------------------------------------------- what the Cockpit is actually told


def _published_steps(monkeypatch) -> list[str]:
    """Collects `metadata.state` from every `scene.activity` this test publishes."""
    import app.creative3d.service as service_module

    steps: list[str] = []

    def _capture(*_args, **kwargs):
        steps.append((kwargs.get("metadata") or {}).get("state"))

    monkeypatch.setattr(service_module, "publish_ui_state", _capture)
    return steps


def test_a_verified_run_publishes_words_the_cockpit_can_read(
    db: Session, device: FakeDeviceAction, service: SceneService, monkeypatch
) -> None:
    """The defect this replaces: the service published the DATABASE ROW's word ("applied"),
    which the web build cannot read at all, so the Cockpit's 3B Sahne row could never say
    "doğrulandı" and never settled — it drew every finished run as one still being made.
    Measured on the real service 2026-09-08; `test_scene_activity_vocabulary.py` is what
    keeps the two lists together from now on."""
    steps = _published_steps(monkeypatch)
    result = service.create(db, device, plan=BLENDER_CREATE_PLAN, session_id="s-1")
    assert result["terminal_status"] == "verified"
    assert steps == ["creating", "verified"]
    assert all(step in SCENE_ACTIVITY_STEPS for step in steps)


def test_a_disagreeing_run_publishes_the_mismatch_the_web_already_drew(
    db: Session, device: FakeDeviceAction, service: SceneService, monkeypatch
) -> None:
    """The web contract carried a `mismatch` posture from the day it was written; the
    backend had never emitted it (the M25 security review's first finding)."""
    steps = _published_steps(monkeypatch)
    service.create(
        db,
        device,
        plan={
            "tool": "blender",
            "project": "lab",
            "scene": "demo",
            "operations": [
                {"op": "create_scene"},
                {"op": "transform", "name": "Kup", "location": [1.0, 0.0, 0.0]},
            ],
        },
        session_id="s-1",
    )
    assert steps == ["creating", "mismatch"]


def test_a_run_with_nothing_to_check_publishes_its_own_word(
    db: Session, device: FakeDeviceAction, service: SceneService, monkeypatch
) -> None:
    """Neither "verified" nor "mismatch" may stand in for it: the run did what was asked
    and there was nothing checkable to read back."""
    steps = _published_steps(monkeypatch)
    service.create(
        db,
        device,
        plan={
            "tool": "blender",
            "project": "lab",
            "scene": "bos",
            "operations": [{"op": "create_scene"}],
        },
        session_id="s-1",
    )
    assert steps == ["creating", "unverified"]


def test_a_render_says_it_is_rendering_and_an_edit_says_it_is_applying(
    db: Session, device: FakeDeviceAction, service: SceneService, monkeypatch
) -> None:
    """Writing an image is not the same act as building the thing in it, and the Core draws
    them differently — so the publisher has to tell them apart."""
    created = service.create(db, device, plan=BLENDER_CREATE_PLAN, session_id="s-1")
    scene_id = created["scene_id"]

    steps = _published_steps(monkeypatch)
    service.apply(
        db,
        device,
        target=scene_id,
        operations=[
            {"op": "add_primitive", "kind": "cube", "name": "Kup2", "location": [2.0, 0.0, 0.0]}
        ],
        session_id="s-1",
    )
    assert steps[0] == "applying"

    steps.clear()
    service.render(db, device, target=scene_id, width=64, height=48, session_id="s-1")
    assert steps[0] == "rendering"


def test_unity_without_a_licence_publishes_the_settled_unavailable_step(
    db: Session, service: SceneService, monkeypatch, tmp_path
) -> None:
    """A tool that cannot be driven is a fact about a licence, not a fault (ADR-0088 §5),
    and the Core has a dim settled posture for exactly that — which was unreachable,
    because this path published nothing at all."""
    fake = FakeCreative3DDevice(unity_available=False, render_dir=tmp_path)
    unity_device = FakeDeviceAction(results=fake.capability_results())
    steps = _published_steps(monkeypatch)
    result = service.create(
        db,
        unity_device,
        plan={
            "tool": "unity",
            "project": "lab",
            "scene": "arac",
            "operations": [{"op": "create_scene"}],
        },
        session_id="s-1",
    )
    assert result["error_class"] == "dependency_unavailable"
    assert UNITY_LICENSE_MESSAGE in result["speech"]
    assert steps == ["creating", "unavailable"]
