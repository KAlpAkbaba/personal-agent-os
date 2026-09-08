"""The App Factory's voice tools, through the REAL application object
(docs/M23_APP_FACTORY_SPEC.md §5) — the same relay/router/tool path the corpus uses
(``tests/voice_corpus``), narrowed here to the specific contracts that category covers in
aggregate: an utterance -> the tool -> ``AppFactoryService`` -> the fake device -> the
receipt and its speech; the "sil" negative (ADR-0086 decision 5); a path-shaped name
refused before the device; nothing scaffolded -> clarification; no device ->
``capability_missing``.

Reuses ``tests.voice_corpus.harness.build_harness`` (the same wiring
``test_owner_utterance_corpus.py`` drives every case through, and ``test_documents_
tools.py``/``test_artifact_tools.py`` already reuse for their own families) rather than
re-deriving the identity/broker/session boilerplate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.appfactory.models import STATE_RUNNING, STATE_SCAFFOLDED, AppProjectRow
from app.appfactory.service import AppFactoryService
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_PROJECT, ObjectFocusRow
from app.voice.realtime_sessions.tools import ToolContext
from app.voice.realtime_sessions.tools_apps import app_create
from tests.voice_corpus.corpus import CTX_APP_RUNNING, CTX_APP_SCAFFOLDED, CTX_NONE
from tests.voice_corpus.harness import build_harness

# --------------------------------------------------------------------------- create


def test_app_create_scaffolds_a_task_tracker_and_focuses_it() -> None:
    h = build_harness()
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Bana bir görev takip uygulaması yap.")
    call = h.tool(sid, "c-1", "app.create", {})

    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["state"] == STATE_SCAFFOLDED
    assert h.device.capabilities_called() == ["project.scaffold"]

    with h.factory() as db:
        row = db.get(AppProjectRow, __import__("uuid").UUID(body["project_id"]))
        assert row.template == "task-tracker"
        assert row.kind == "web_static"
        current = focus_module.current(db, FOCUS_KIND_PROJECT)
        assert current is not None
        assert current.object_id == body["project_id"]


def test_app_create_a_named_static_page() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Küçük bir web sayfası uygulaması oluştur: adı Notlarım.")
    call = h.tool(sid, "c-1", "app.create", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    with h.factory() as db:
        row = db.get(AppProjectRow, __import__("uuid").UUID(body["project_id"]))
        assert row.template == "static-page"
        assert row.name == "Notlarım"


def test_app_create_a_cli_tool_with_named_commands() -> None:
    h = build_harness()
    sid = h.new_session()
    h.say(sid, "Komut satırı aracı yap: selamla ve say komutları.")
    call = h.tool(sid, "c-1", "app.create", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    with h.factory() as db:
        row = db.get(AppProjectRow, __import__("uuid").UUID(body["project_id"]))
        assert row.template == "cli-tool"
        assert [c["name"] for c in row.spec_json["commands"]] == ["selamla", "say"]


def test_app_create_with_a_path_shaped_name_is_refused_before_the_device() -> None:
    h = build_harness()
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Bana bir görev takip uygulaması yap: adı ..\\x.")
    call = h.tool(sid, "c-1", "app.create", {"template": "task-tracker", "name": "..\\x"})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "refused"
    assert body["error_class"] == "validation_error"
    assert h.device.calls == []


def test_app_create_with_no_device_is_capability_missing() -> None:
    """No ``device_action`` on ``ctx.live`` at all — the same seam
    ``test_documents_tools.py``'s own no-device test reaches (a hand-built
    ``ToolContext``, the real handler, no device at all)."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (AppProjectRow.__table__, ObjectFocusRow.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        ctx = ToolContext(
            session_id=uuid4(),
            owner_session_id=uuid4(),
            device_id=None,
            client_kind="desktop",
            context={},
            db=db,
            now=datetime.now(UTC),
            live={"app_factory_service": AppFactoryService()},
        )
        result = app_create(ctx, {"template": "task-tracker", "name": "X"})
    assert result["execution_status"] == "refused"
    assert result["error_class"] == "capability_missing"


# ------------------------------------------------------------------------------ run


def test_app_run_starts_the_scaffolded_project() -> None:
    h = build_harness()
    h.seed(CTX_APP_SCAFFOLDED)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Uygulamayı çalıştır.")
    call = h.tool(sid, "c-1", "app.run", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["state"] == STATE_RUNNING
    assert h.device.capabilities_called() == ["project.run"]


def test_app_run_with_nothing_scaffolded_is_a_clarification() -> None:
    h = build_harness()
    h.seed(CTX_NONE)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Uygulamayı çalıştır.")
    call = h.tool(sid, "c-1", "app.run", {})
    assert call["status"] == "needs_clarification", call
    assert call["result"]["speech"]


# ----------------------------------------------------------------------------- test


def test_app_test_runs_the_projects_own_tests() -> None:
    h = build_harness()
    h.seed(CTX_APP_SCAFFOLDED)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Testleri çalıştır.")
    call = h.tool(sid, "c-1", "app.test", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["passed"] == 5
    assert body["failed"] == 0
    assert h.device.capabilities_called() == ["project.test"]


# ----------------------------------------------------------------------------- stop


def test_app_stop_stops_the_running_project() -> None:
    h = build_harness()
    h.seed(CTX_APP_RUNNING)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Uygulamayı durdur.")
    call = h.tool(sid, "c-1", "app.stop", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert h.device.capabilities_called() == ["project.stop"]


# --------------------------------------------------------------------------- status


def test_app_status_reports_running_and_port() -> None:
    h = build_harness()
    h.seed(CTX_APP_RUNNING)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Uygulama çalışıyor mu?")
    call = h.tool(sid, "c-1", "app.status", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["state"] == "running"
    assert body["port"]


# ----------------------------------------------------------------------------- open


def test_app_open_reveals_the_folder_and_the_browser() -> None:
    h = build_harness()
    h.seed(CTX_APP_RUNNING)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Uygulamayı aç.")
    call = h.tool(sid, "c-1", "app.open", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert "file.reveal" in h.device.capabilities_called()


# ----------------------------------------------------------------------------- list


def test_app_list_names_every_project() -> None:
    h = build_harness()
    h.seed(CTX_APP_SCAFFOLDED)
    sid = h.new_session()
    h.device.reset()
    h.say(sid, "Hangi uygulamaları yaptın?")
    call = h.tool(sid, "c-1", "app.list", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["projects"]
    assert h.device.calls == []


# ------------------------------------------------------------------------ negative


def test_delete_reaches_no_tool_at_all() -> None:
    """ADR-0086 decision 5: "Projeyi sil." reaches nothing — no delete tool exists."""
    h = build_harness()
    h.seed(CTX_APP_SCAFFOLDED)
    sid = h.new_session()
    h.device.reset()
    said = h.say(sid, "Projeyi sil.")
    resolved = said["resolved_intents"][0]
    assert resolved["intent"] == "none"
    assert h.device.calls == []
