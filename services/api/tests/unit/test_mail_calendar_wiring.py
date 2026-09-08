"""Wiring test: Mail & Calendar's runtime, through the REAL application object
(docs/M21_MAIL_CALENDAR_SPEC.md §2, §3, ADR-0084) — the same pattern
``test_documents_wiring.py`` establishes: what a voice tool reads through
``ToolContext.live`` is what ``create_app`` actually built, and with nothing configured
the provider (and therefore the service) is honest about ``account_missing``.
"""

from __future__ import annotations

from app.calendar.service import CalendarService
from app.config import Settings
from app.mail.service import MailService
from app.main import create_app

VENDOR_KEY = "unit-test-vendor-key-sentinel-must-never-leave-the-server"


def test_create_app_wires_the_mail_and_calendar_services() -> None:
    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    live = app.state.voice_realtime.live_sources()

    assert isinstance(live["mail_service"], MailService)
    assert live["mail_service"] is app.state.mail_service
    assert isinstance(live["calendar_service"], CalendarService)
    assert live["calendar_service"] is app.state.calendar_service


def test_with_nothing_configured_every_service_answers_account_missing() -> None:
    """The honest production answer until the owner puts a real account on the host
    (spec §2, owner item)."""
    app = create_app(Settings(_env_file=None, voice_openai_api_key=VENDOR_KEY))
    mail = app.state.mail_service
    calendar = app.state.calendar_service
    assert mail._provider is None  # type: ignore[attr-defined]
    assert mail._sender is None  # type: ignore[attr-defined]
    assert calendar._provider is None  # type: ignore[attr-defined]
    assert calendar._writer is None  # type: ignore[attr-defined]


def test_a_runtime_registers_mail_and_calendar_services_additively() -> None:
    from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime

    runtime = RealtimeVoiceRuntime(Settings(_env_file=None), providers={})
    assert "mail_service" not in runtime.live_sources()
    assert "calendar_service" not in runtime.live_sources()
    mail_marker = object()
    calendar_marker = object()
    runtime.register_live(mail_service=mail_marker, calendar_service=calendar_marker)
    live = runtime.live_sources()
    assert live["mail_service"] is mail_marker
    assert live["calendar_service"] is calendar_marker


def test_mail_send_enabled_flag_gates_whether_a_real_sender_is_built() -> None:
    """``PAGENTOS_MAIL_SEND_ENABLED``/``PAGENTOS_CALENDAR_WRITE_ENABLED`` are host flags
    the autonomous system never writes (ADR-0084 decision 1) — proven here by their
    absence from any owner-facing settings mutation path; this only proves the WIRING
    reads them, with an account configured but the flag off."""
    settings = Settings(
        _env_file=None,
        voice_openai_api_key=VENDOR_KEY,
        mail_imap_host="imap.example.com",
        mail_imap_user="owner",
        mail_imap_password="x",
        mail_smtp_host="smtp.example.com",
        mail_from="alp@example.com",
        mail_send_enabled=False,
    )
    app = create_app(settings)
    mail = app.state.mail_service
    assert mail._provider is not None  # type: ignore[attr-defined]
    assert mail._sender is None  # type: ignore[attr-defined]  - flag off, sender refuses
