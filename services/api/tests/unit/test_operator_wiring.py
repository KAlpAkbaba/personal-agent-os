"""Wiring test: the Digital Operator's runtime, through the REAL application object
(docs/M19_DIGITAL_OPERATOR_SPEC.md §4, docs/DECISIONS.md ADR-0078's own pattern).

``test_alarms_wiring.py`` exists because a component built, tested and never wired was a
recurring defect class on this repository. This is the same proof for the operator: what
a voice tool reads through ``ToolContext.live`` is what ``create_app`` actually built, not
``None`` — the ``device_action`` port is the SAME ``BrokerDeviceAction`` object the wake
sequence already holds (one device port, never a second desktop-control path), and the
``operator`` runtime is reachable both through the live-sources dict AND through the
process-wide module registry the ONE router's ringing-aware Cancel/Status pair reads from
(``record_client_events`` builds no ``ToolContext`` at all).
"""

from __future__ import annotations

from app.config import Settings
from app.main import create_app
from app.operator.service import OperatorService, get_operator_service
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"


def test_create_app_wires_the_device_action_and_the_operator_runtime() -> None:
    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    live = app.state.voice_realtime.live_sources()

    assert live["device_action"] is app.state.wake_sequence._device
    assert live["operator"] is app.state.operator_service
    assert isinstance(app.state.operator_service, OperatorService)


def test_the_device_action_operator_holds_is_the_same_object_the_wake_sequence_uses() -> None:
    """Invariant 1 (spec §1): no alternate desktop-control path. There is exactly ONE
    ``DeviceActionPort`` in this process; the operator and the wake sequence both hold it."""
    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    live = app.state.voice_realtime.live_sources()
    assert live["device_action"] is app.state.wake_sequence._device


def test_the_module_registry_and_the_live_source_name_the_same_operator_service() -> None:
    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    live = app.state.voice_realtime.live_sources()
    assert get_operator_service() is live["operator"]
    assert get_operator_service() is app.state.operator_service


def test_a_runtime_registers_device_action_and_operator_additively() -> None:
    runtime = RealtimeVoiceRuntime(Settings(_env_file=None), providers={})
    assert "device_action" not in runtime.live_sources()
    assert "operator" not in runtime.live_sources()
    device_marker = object()
    operator_marker = object()
    runtime.register_live(device_action=device_marker)
    runtime.register_live(operator=operator_marker)
    live = runtime.live_sources()
    assert live["device_action"] is device_marker
    assert live["operator"] is operator_marker
