"""Wiring test: File & Document Intelligence's runtime, through the REAL application
object (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3, the same pattern
``test_operator_wiring.py`` and ``test_alarms_wiring.py`` establish for their own
families: what a voice tool reads through ``ToolContext.live`` is what ``create_app``
actually built, not ``None``).

The ``device_action`` port ``DocumentService`` reaches through is the SAME
``BrokerDeviceAction`` object the wake sequence and the operator already hold — one
desktop/file authority, never a second one (spec §1: Cloud Core never receives a copy of
the owner's disk, and reads travel over the SAME command envelope every other device
capability does).
"""

from __future__ import annotations

from app.config import Settings
from app.documents.service import DocumentService
from app.main import create_app
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"


def test_create_app_wires_the_document_service_and_the_shared_device_action() -> None:
    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    live = app.state.voice_realtime.live_sources()

    assert isinstance(live["document_service"], DocumentService)
    assert live["document_service"] is app.state.document_service
    assert live["device_action"] is app.state.wake_sequence._device


def test_the_device_action_documents_holds_is_the_same_object_every_other_family_uses() -> None:
    """Invariant (spec §1): no second desktop/file authority. Both ``document_service``
    and ``operator`` are reachable from the SAME ``ctx.live`` a tool call sees, and both
    are dispatched the SAME ``device_action`` port every request shares (the wake
    sequence's own) - ``DocumentService`` takes ``device_action`` per call rather than
    holding it, so identity is checked at the ``ctx.live`` seam every tool actually reads,
    not on an internal attribute."""
    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    live = app.state.voice_realtime.live_sources()
    assert live["device_action"] is app.state.wake_sequence._device
    assert live["operator"] is app.state.operator_service
    assert live["document_service"] is app.state.document_service


def test_a_runtime_registers_document_service_additively() -> None:
    runtime = RealtimeVoiceRuntime(Settings(_env_file=None), providers={})
    assert "document_service" not in runtime.live_sources()
    marker = object()
    runtime.register_live(document_service=marker)
    live = runtime.live_sources()
    assert live["document_service"] is marker
