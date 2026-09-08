"""The Artifact Factory's voice tools, through the REAL application object
(docs/M22_ARTIFACT_FACTORY_SPEC.md §5, ADR-0085) — the same relay/router/tool path the
corpus uses (``tests/voice_corpus``), narrowed here to the specific contracts that
category covers in aggregate: create -> render -> validate -> open, the "never
invented" refusal, the focus stack (current/previous), and no device ->
``capability_missing``.

Reuses ``tests.voice_corpus.harness.build_harness`` — the same wiring
``test_owner_utterance_corpus.py`` drives the whole corpus through — rather than
re-deriving the identity/broker/session boilerplate a second time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts import factory as artifact_factory
from app.artifacts.models import Artifact, ArtifactRender, ArtifactVersion
from app.artifacts.runtime import ArtifactRuntime
from app.artifacts.spec import ArtifactSpec
from app.config import Settings
from app.object_store import InMemoryObjectStore
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_ARTIFACT, ObjectFocusRow
from app.voice.realtime_sessions.tools import ToolContext
from app.voice.realtime_sessions.tools_artifacts import artifact_open
from tests.voice_corpus.corpus import CTX_ARTIFACT_FOCUSED
from tests.voice_corpus.harness import build_harness

BUDGET_SPEC = "tests/fixtures/artifacts/specs/butce-tablosu.json"


def _spec_json(path: str) -> dict:
    import json

    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ artifact.create


def test_artifact_create_makes_a_real_spreadsheet_and_focuses_it() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Bana bir bütçe tablosu yap: kira 12000, maaş 45000, yazılım 8000.")
    call = h.tool(
        sid,
        "c-1",
        "artifact.create",
        {
            "kind": "spreadsheet",
            "title": "Bütçe",
            "spec": {
                "kind": "spreadsheet",
                "title": "Bütçe",
                "sheets": [
                    {
                        "name": "Özet",
                        "columns": ["Kalem", "Tutar"],
                        "rows": [["Kira", 12000], ["Maaş", 45000], ["Yazılım", 8000]],
                    }
                ],
            },
        },
    )
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["all_valid"] is True
    assert set(body["formats"]) == {"xlsx", "csv"}
    assert sorted(body["numbers"]) == [8000.0, 12000.0, 45000.0]
    assert "doğrulandı" in body["speech"]

    with h.factory() as db:
        current = focus_module.current(db, FOCUS_KIND_ARTIFACT)
    assert current is not None
    assert current.object_id == body["artifact_id"]


def test_artifact_create_refuses_a_number_the_owner_never_said() -> None:
    """The "never invented" rule (app.artifacts.spec), end to end through the tool: a
    number in the model's OWN spec that the router never heard the owner say is refused,
    never rendered."""
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Bana bir bütçe tablosu yap: kira 12000.")
    call = h.tool(
        sid,
        "c-1",
        "artifact.create",
        {
            "kind": "spreadsheet",
            "title": "Bütçe",
            "spec": {
                "kind": "spreadsheet",
                "title": "Bütçe",
                "sheets": [
                    {
                        "name": "Özet",
                        "columns": ["Kalem", "Tutar"],
                        "rows": [["Kira", 12000], ["Bilinmeyen", 99999]],
                    }
                ],
            },
        },
    )
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "validation_error"


def test_artifact_create_with_no_kind_word_at_all_asks_which_kind() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Bir şey yap.")
    call = h.tool(sid, "c-1", "artifact.create", {})
    assert call["status"] == "needs_clarification", call


# ------------------------------------------------------------------ artifact.render


def test_artifact_render_adds_a_new_format_to_the_focused_artifact() -> None:
    h = build_harness()
    h.seed(CTX_ARTIFACT_FOCUSED)  # current: the budget spreadsheet (xlsx + csv already)
    sid = h.new_session()
    h.say(sid, "Bunu PDF yap.")
    call = h.tool(sid, "c-1", "artifact.render", {"format": "pdf"})
    # The budget spreadsheet's kind (spreadsheet) cannot produce "pdf" — refused, never
    # silently substituted with a different format.
    assert call["status"] == "succeeded", call
    assert call["result"]["execution_status"] == "refused"
    assert call["result"]["error_class"] == "validation_error"


def test_artifact_render_produces_a_valid_extra_format() -> None:
    h = build_harness()
    h.seed(CTX_ARTIFACT_FOCUSED)
    sid = h.new_session()
    h.say(sid, "Bunu tekrar CSV yap.")
    call = h.tool(sid, "c-1", "artifact.render", {"format": "csv"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["state"] == "valid"


# ---------------------------------------------------------------- artifact.validate


def test_artifact_validate_reports_valid_for_the_focused_artifact() -> None:
    h = build_harness()
    h.seed(CTX_ARTIFACT_FOCUSED)
    sid = h.new_session()
    h.say(sid, "Bu dosya doğru mu?")
    call = h.tool(sid, "c-1", "artifact.validate", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["all_valid"] is True
    assert "doğru" in body["speech"]


def test_artifact_validate_with_nothing_focused_asks_which_file() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Bu dosya doğru mu?")
    call = h.tool(sid, "c-1", "artifact.validate", {})
    assert call["status"] == "needs_clarification", call


# -------------------------------------------------------------------- artifact.open


def test_artifact_open_fetches_and_opens_through_the_fake_device() -> None:
    h = build_harness()
    h.seed(CTX_ARTIFACT_FOCUSED)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Bunu aç.")
    call = h.tool(sid, "c-1", "artifact.open", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["state"] == "opened"
    assert body["window_title"]
    assert h.device.capabilities_called() == ["file.fetch"]
    payload = h.device.payload_for("file.fetch")
    assert payload["open"] is True
    assert payload["sha256"]
    assert payload["size"] > 0


def test_artifact_open_previous_reaches_the_older_artifact() -> None:
    h = build_harness()
    h.seed(CTX_ARTIFACT_FOCUSED)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Önceki dosyayı aç.")
    call = h.tool(sid, "c-1", "artifact.open", {})
    assert call["status"] == "succeeded", call
    assert call["result"]["artifact_id"] == h.ids["artifact:previous"]


def test_artifact_open_with_nothing_produced_is_an_honest_clarification() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Bunu aç.")
    call = h.tool(sid, "c-1", "artifact.open", {})
    assert call["status"] == "needs_clarification", call
    assert "Hangi dosya" in call["result"]["speech"]


def test_artifact_open_refuses_an_invalid_render_even_when_named_explicitly() -> None:
    """ADR-0085 §3: an invalid render is never opened, even when the caller names it
    explicitly — a lying renderer is exercised here by asking for a format that was
    never produced for this artifact's kind at all (never_rendered stands in for
    invalid, since the fixture spec always renders valid bytes)."""
    h = build_harness()
    h.seed(CTX_ARTIFACT_FOCUSED)
    sid = h.new_session()
    call = h.tool(sid, "c-1", "artifact.open", {"format": "pptx"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "not_found"


# ------------------------------------------------------------------ artifact.list


def test_artifact_list_names_what_was_made() -> None:
    h = build_harness()
    h.seed(CTX_ARTIFACT_FOCUSED)
    sid = h.new_session()
    h.say(sid, "Neler ürettin?")
    call = h.tool(sid, "c-1", "artifact.list", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert len(body["artifacts"]) == 2
    assert any(a["title"] == "Bütçe 2026" for a in body["artifacts"])


def test_artifact_list_with_nothing_made_is_honest_about_it() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Neler ürettin?")
    call = h.tool(sid, "c-1", "artifact.list", {})
    assert call["status"] == "succeeded", call
    assert "Henüz" in call["result"]["speech"]
    assert call["result"]["artifacts"] == []


# ------------------------------------------------------------------ capability_missing


def test_artifact_open_with_no_device_action_is_a_capability_missing_receipt() -> None:
    """No ``device_action`` on ``ctx.live`` at all (the honest production answer today:
    the deployed agent may advertise no ``file.fetch`` capability). Exercises the real
    tool handler and ``open_service`` directly (a hand-built ``ToolContext``, the same
    seam ``test_documents_tools.py``'s own no-device test reaches) rather than standing
    up a second full application."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        Artifact.__table__,
        ArtifactVersion.__table__,
        ArtifactRender.__table__,
        ObjectFocusRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None)
    runtime = ArtifactRuntime(settings)
    runtime._engine = engine
    runtime._session_factory = factory
    runtime._store = InMemoryObjectStore()

    with factory() as db:
        spec = ArtifactSpec.model_validate(_spec_json(BUDGET_SPEC))
        result = artifact_factory.create(db, runtime.store, spec=spec)
        focus_module.set_focus(
            db, FOCUS_KIND_ARTIFACT, str(result.artifact_id), label=spec.title, source="test"
        )

        ctx = ToolContext(
            session_id=uuid4(),
            owner_session_id=uuid4(),
            device_id=None,
            client_kind="desktop",
            context={},
            db=db,
            now=datetime.now(UTC),
            live={"artifacts_runtime": runtime},
        )
        response = artifact_open(ctx, {})
    assert response["execution_status"] == "refused"
    assert response["error_class"] == "capability_missing"
    assert "dosya getiremiyor" in response["speech"]


# --------------------------------------------------------------- ADR-0085 addendum 5


def test_artifact_open_token_never_leaks_into_the_ledger_or_the_receipt() -> None:
    """ADR-0085 addendum 5: the single-use render-fetch token minted for the device's
    ``file.fetch`` must never appear in a receipt, a ledger row, a log line or UI
    metadata — a test greps for it (the task's own non-negotiable). This is the SAME
    tool-handler seam as the ``capability_missing`` test above, with a real device
    (``FakeDeviceAction``) present so a token is actually minted."""
    import json as json_module

    from sqlalchemy import select

    from app.ledger.models import ActivityEventRow
    from tests.alarms_support import FakeDeviceAction
    from tests.artifacts_support import file_fetch_ok

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        Artifact.__table__,
        ArtifactVersion.__table__,
        ArtifactRender.__table__,
        ObjectFocusRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None)
    runtime = ArtifactRuntime(settings)
    runtime._engine = engine
    runtime._session_factory = factory
    runtime._store = InMemoryObjectStore()
    device = FakeDeviceAction(results={"file.fetch": file_fetch_ok})

    with factory() as db:
        spec = ArtifactSpec.model_validate(_spec_json(BUDGET_SPEC))
        result = artifact_factory.create(db, runtime.store, spec=spec)
        focus_module.set_focus(
            db, FOCUS_KIND_ARTIFACT, str(result.artifact_id), label=spec.title, source="test"
        )

        ctx = ToolContext(
            session_id=uuid4(),
            owner_session_id=uuid4(),
            device_id=None,
            client_kind="desktop",
            context={},
            db=db,
            now=datetime.now(UTC),
            live={"artifacts_runtime": runtime, "device_action": device},
        )
        response = artifact_open(ctx, {})

        payload = device.payload_for("file.fetch")
        token = payload["url"].rsplit("/", 1)[-1]
        assert token and len(token) >= 32  # a real token was actually minted

        rows = db.execute(select(ActivityEventRow)).scalars().all()
        ledger_blob = json_module.dumps([r.detail_json for r in rows])

    assert response["execution_status"] == "executed"
    assert token not in json_module.dumps(response)
    assert token not in ledger_blob
