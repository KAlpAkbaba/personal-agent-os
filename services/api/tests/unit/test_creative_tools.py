"""The Creative Tools Operator's voice tools, through the REAL application object
(docs/M27_CREATIVE_TOOLS_SPEC.md §5) — the same relay/router/tool path the corpus uses
(``tests/voice_corpus``), narrowed here to the specific contracts that category covers
in aggregate: an utterance -> the tool -> ``CreativeService`` -> the fixture providers
-> the receipt with the comparison read back; the tool-word resolution (utterance,
else the current focus, else a family default); the honest Photoshop/Figma refusal.

Reuses ``tests.voice_corpus.harness.build_harness`` (the same wiring
``test_scene_tools.py`` already reuses for its own family) rather than re-deriving the
identity/broker/session boilerplate.
"""

from __future__ import annotations

import uuid

from app.creative.models import STATE_DEPENDENCY_UNAVAILABLE, CreativeRunRow
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_CREATIVE
from tests.voice_corpus.harness import build_harness

# ------------------------------------------------------------------- creative.design


def test_creative_design_without_a_named_tool_reaches_a_tool_that_can_work() -> None:
    """B03 req 505. The default used to be Figma, and ``FigmaProvider.token_present`` is a
    hard-coded ``False`` with nothing wired to set it - an honest placeholder for a credential
    store that does not exist. So an owner who said "bir arayuz tasarla" without naming a
    program got ``dependency_unavailable`` every single time: the tool failed by construction,
    and no owner could ever get through it. Naming Figma still reaches that refusal (the test
    below); naming nothing now reaches Paint, which does the work with Pillow."""
    h = build_harness()
    sid = h.new_session()
    call = h.tool(sid, "c-0", "creative.design", {"width": 400, "height": 300})

    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] != "refused", body
    with h.factory() as db:
        row = db.get(CreativeRunRow, uuid.UUID(body["run_id"]))
        assert row.tool == "paint"


def test_creative_design_figma_absent_is_an_honest_refusal() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Figma'da buna benzeyen bir arayüz tasarla.")
    call = h.tool(sid, "c-1", "creative.design", {})
    assert call["status"] == "succeeded", call  # a truthful refusal receipt, not an error
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "dependency_unavailable"
    assert "Figma" in body["speech"]

    with h.factory() as db:
        row = db.get(CreativeRunRow, uuid.UUID(body["run_id"]))
        assert row.state == STATE_DEPENDENCY_UNAVAILABLE


# --------------------------------------------------------------------- creative.open


def test_creative_open_paint_with_no_source_creates_a_blank_canvas_and_focuses_it() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Paint'te yeni bir şey aç.")
    call = h.tool(sid, "c-1", "creative.open", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"

    with h.factory() as db:
        row = db.get(CreativeRunRow, uuid.UUID(body["run_id"]))
        assert row.tool == "paint"
        current = focus_module.current(db, FOCUS_KIND_CREATIVE)
        assert current is not None
        assert current.object_id == body["run_id"]


def test_creative_open_photoshop_names_the_installed_alternative() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Bunu Photoshop'ta aç.")
    call = h.tool(sid, "c-1", "creative.open", {})
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "dependency_unavailable"
    assert "Photoshop" in body["speech"] and "Paint" in body["speech"]


def test_creative_open_with_no_tool_word_and_no_focus_is_a_clarification() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Bunu aç.")  # no tool word — the router sends this to ARTIFACT_OPEN, not here
    call = h.tool(sid, "c-1", "creative.open", {})
    assert call["status"] == "needs_clarification", call


# -------------------------------------------------------------- creative.background


def test_creative_background_on_the_focused_run() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Paint'te yeni bir şey aç.")
    h.tool(sid, "c-1", "creative.open", {})
    h.say(sid, "Arka planını kaldır.")
    call = h.tool(sid, "c-2", "creative.background", {})
    assert call["status"] == "succeeded", call
    assert call["result"]["execution_status"] == "executed"


def test_creative_background_with_no_run_at_all_is_a_clarification() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Arka planını kaldır.")
    call = h.tool(sid, "c-1", "creative.background", {})
    assert call["status"] == "needs_clarification", call


# ------------------------------------------------------------------ creative.export


def test_creative_export_uses_the_owners_named_format() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Paint'te yeni bir şey aç.")
    h.tool(sid, "c-1", "creative.open", {})
    h.say(sid, "Bunu PNG olarak dışa aktar.")
    call = h.tool(sid, "c-2", "creative.export", {})
    assert call["status"] == "succeeded", call
    assert call["result"]["output_name"].endswith(".png")


# -------------------------------------------------------- forbidden side effects


def test_no_owner_file_is_ever_touched_by_a_creative_tool_call() -> None:
    """spec §5's own forbidden-side-effect measure: hashing the source before and
    after every case. This Cloud Core half never reads/writes a real filesystem path
    at all (module docstring, ``app.creative.service``) — every byte lives in the
    object store — so there is nothing on disk for a creative tool call to touch."""
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Paint'te yeni bir şey aç.")
    call = h.tool(sid, "c-1", "creative.open", {})
    assert call["result"]["execution_status"] == "executed"
    # The fake device (M19/M25's own file-touching surface) saw no calls at all.
    assert h.device.calls == []
