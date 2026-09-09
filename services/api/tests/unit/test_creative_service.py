"""``CreativeService`` (docs/M27_CREATIVE_TOOLS_SPEC.md §2-§7, ADR-0093): plan ->
provider gate -> execute -> compare -> self-correct -> receipt, against SQLite + an
in-memory object store — the same harness discipline ``test_scene_service.py``
establishes for its own family.
"""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.creative.models import (
    STATE_DEPENDENCY_UNAVAILABLE,
    STATE_MISMATCH,
    STATE_VERIFIED,
    CreativeRunRow,
)
from app.creative.providers import DetectionFacts, FigmaProvider, PaintProvider
from app.creative.service import MAX_ACTIVE_RUNS, CreativeService
from app.creative.spec import TOOL_FIGMA, TOOL_PAINT
from app.ledger.models import ActivityEventRow
from app.object_store import InMemoryObjectStore
from app.operator.models import FOCUS_KIND_CREATIVE, ObjectFocusRow

TABLES = [CreativeRunRow.__table__, ObjectFocusRow.__table__, ActivityEventRow.__table__]


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
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


class _InstalledPaintProvider(PaintProvider):
    """A test double whose ``detect()`` always answers "installed" — never running
    the real filesystem check, so this suite is deterministic on any machine
    (including one where ``mspaint.exe`` genuinely is not present)."""

    def detect(self) -> DetectionFacts:  # type: ignore[override]
        return DetectionFacts(installed=True, detail="mspaint.exe")


def _installed_paint() -> PaintProvider:
    return _InstalledPaintProvider()


@pytest.fixture()
def service(store: InMemoryObjectStore) -> CreativeService:
    return CreativeService(
        object_store=store,
        providers={
            TOOL_PAINT: _installed_paint(),
            TOOL_FIGMA: FigmaProvider(token_present=False),
        },
    )


PAINT_LAB_PLAN = {
    "tool": "paint",
    "name": "lab",
    "operations": [
        {"op": "new", "width": 320, "height": 240, "background": [255, 255, 255, 255]},
        {"op": "shape", "kind": "rect", "box": [10, 10, 100, 80], "fill": [255, 0, 0, 255]},
        {"op": "shape", "kind": "ellipse", "box": [150, 20, 250, 120], "fill": [0, 0, 255, 255]},
        {
            "op": "add_text",
            "text": "Merhaba",
            "position": [10, 150],
            "size": 20,
            "colour": [0, 0, 0, 255],
        },
        {"op": "export", "format": "png"},
    ],
}


def test_create_executes_and_verifies_the_paint_lab_plan(
    db: Session, service: CreativeService
) -> None:
    result = service.create(db, plan=PAINT_LAB_PLAN, session_id="s-1")
    assert result["execution_status"] == "executed", result
    assert result["state"] == STATE_VERIFIED
    assert result["compare"]["ok"] is True

    run_id = result["run_id"]
    row = db.get(CreativeRunRow, uuid.UUID(run_id))
    assert row is not None
    assert row.state == STATE_VERIFIED
    assert row.output_object_key is not None

    entry = db.query(ObjectFocusRow).filter_by(kind=FOCUS_KIND_CREATIVE).one()
    assert entry.object_id == run_id


def test_create_stores_a_real_png_in_the_object_store(
    db: Session, service: CreativeService, store: InMemoryObjectStore
) -> None:
    result = service.create(db, plan=PAINT_LAB_PLAN, session_id="s-1")
    row = db.get(CreativeRunRow, uuid.UUID(result["run_id"]))
    data = store.get(row.output_object_key)
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (320, 240)


def test_photoshop_not_installed_is_dependency_unavailable(
    db: Session, service: CreativeService
) -> None:
    plan = {
        "tool": "photoshop",
        "name": "lab",
        "operations": [{"op": "new", "width": 10, "height": 10}],
    }
    result = service.create(db, plan=plan, session_id="s-1")
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "dependency_unavailable"
    assert "Photoshop" in result["speech"]
    assert "Paint" in result["speech"]  # names the installed alternative
    row = db.get(CreativeRunRow, uuid.UUID(result["run_id"]))
    assert row.state == STATE_DEPENDENCY_UNAVAILABLE


def test_figma_without_token_is_dependency_unavailable(
    db: Session, service: CreativeService
) -> None:
    plan = {
        "tool": "figma",
        "name": "lab",
        "operations": [{"op": "new", "width": 10, "height": 10}],
    }
    result = service.create(db, plan=plan, session_id="s-1")
    assert result["error_class"] == "dependency_unavailable"


def test_layer_op_refused_at_the_schema_before_any_execution(
    db: Session, service: CreativeService
) -> None:
    plan = {
        "tool": "paint",
        "name": "lab",
        "operations": [{"op": "layer", "action": "add", "name": "x"}],
    }
    result = service.create(db, plan=plan, session_id="s-1")
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "validation_error"


def test_invalid_plan_is_refused_before_a_row_is_even_active(
    db: Session, service: CreativeService
) -> None:
    result = service.create(db, plan={"tool": "not-a-tool", "name": "lab"}, session_id="s-1")
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "validation_error"


def test_max_active_runs_bound(db: Session, service: CreativeService) -> None:
    for i in range(MAX_ACTIVE_RUNS):
        plan = {**PAINT_LAB_PLAN, "name": f"lab{i}"}
        result = service.create(db, plan=plan, session_id="s-1")
        assert result["execution_status"] == "executed"
    over = service.create(db, plan={**PAINT_LAB_PLAN, "name": "labx"}, session_id="s-1")
    assert over["execution_status"] == "refused"
    assert over["error_class"] == "validation_error"


def test_apply_export_on_an_existing_run(db: Session, service: CreativeService) -> None:
    created = service.create(db, plan=PAINT_LAB_PLAN, session_id="s-1")
    result = service.apply(
        db,
        target=created["run_id"],
        operations=[{"op": "export", "format": "jpg"}],
        capability="creative.export",
        session_id="s-1",
    )
    assert result["execution_status"] == "executed"
    assert result["output_name"].endswith(".jpg")


def test_apply_background_remove_on_existing_run(db: Session, service: CreativeService) -> None:
    created = service.create(db, plan=PAINT_LAB_PLAN, session_id="s-1")
    result = service.apply(
        db,
        target=created["run_id"],
        operations=[{"op": "background_remove", "method": "threshold", "tolerance": 10}],
        capability="creative.background",
        session_id="s-1",
    )
    assert result["execution_status"] == "executed"


def test_self_correction_fixes_a_missing_shape_within_bound(
    db: Session, service: CreativeService
) -> None:
    """A first round whose comparison finds a missing shape converges within the
    bounded retry (ADR-0093 decision 7) because the follow-up plan re-appends the
    failing op as the LAST thing painted."""
    # A plan whose LATER crop wipes out the earlier shape entirely, so round 1's
    # comparison finds it missing and round 2's correction (re-append the shape)
    # fixes it deterministically.
    plan = {
        "tool": "paint",
        "name": "lab",
        "operations": [
            {"op": "new", "width": 100, "height": 100, "background": [255, 255, 255, 255]},
            {"op": "shape", "kind": "rect", "box": [10, 10, 50, 50], "fill": [255, 0, 0, 255]},
            {"op": "crop", "box": [60, 60, 100, 100]},  # crops the red rect entirely out
            {"op": "shape", "kind": "rect", "box": [10, 10, 50, 50], "fill": [255, 0, 0, 255]},
        ],
    }
    result = service.create(db, plan=plan, session_id="s-1")
    assert result["execution_status"] == "executed"
    assert result["state"] == STATE_VERIFIED
    assert result["compare"]["ok"] is True


def test_a_defect_no_retry_can_fix_stops_and_reports_the_numbers(
    db: Session, service: CreativeService
) -> None:
    """A reference-similarity mismatch cannot be fixed by re-drawing anything the
    plan already asked for — the service must stop after round 1 and say so, never
    loop to no effect (ADR-0093 decision 7)."""
    plan = {
        "tool": "paint",
        "name": "lab",
        "operations": [
            {"op": "new", "width": 40, "height": 40, "background": [255, 255, 255, 255]}
        ],
    }
    black_reference = io.BytesIO()
    Image.new("RGB", (40, 40), (0, 0, 0)).save(black_reference, format="PNG")
    result = service.create(
        db, plan=plan, reference_bytes=black_reference.getvalue(), session_id="s-1"
    )
    row = db.get(CreativeRunRow, uuid.UUID(result["run_id"]))
    assert row.round_count == 1
    assert row.state == STATE_MISMATCH


def test_list_and_status(db: Session, service: CreativeService) -> None:
    created = service.create(db, plan=PAINT_LAB_PLAN, session_id="s-1")
    listing = service.list(db, session_id="s-1")
    assert listing["runs"][0]["name"] == "lab"
    status = service.status(db, target=created["run_id"], session_id="s-1")
    assert status["state"] == STATE_VERIFIED


def test_resolve_run_falls_back_to_most_recent(db: Session, service: CreativeService) -> None:
    service.create(db, plan=PAINT_LAB_PLAN, session_id="s-1")
    row = service.resolve_run(db, None)
    assert row is not None
    assert row.name == "lab"


def test_no_run_at_all_is_a_clarification(db: Session, service: CreativeService) -> None:
    result = service.status(db, target="current", session_id="s-1")
    assert result["status"] == "needs_clarification"
