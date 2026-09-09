"""A device's refusal of our payload becomes a work item, without the owner filing it.

The Evolution Supervisor has been scanning production for days with nothing to read:
``ingest_incident`` was reachable only from ``POST /v1/selfhealing/incidents`` and nothing
inside the product ever called it. Every defect arrived because the OWNER noticed.

Three did on 2026-09-09, and every one left the same evidence in ``device_commands``. The
first block below is those three, verbatim from production, asserted to open incidents.
"""

from __future__ import annotations

import uuid

import pytest

from app.selfhealing import defects
from app.selfhealing.defects import (
    DEFECT_COMPONENT,
    device_defect_draft,
    is_server_fault,
    normalise_message,
    register_defect_sink,
    report_device_defect,
)

#: The three, exactly as the device said them.
THE_INCIDENT_DAY = [
    (
        "window.activate",
        "validation_error",
        "'Not Defteri' is not a window id (expected w-<hwnd>-<tick>)",
        {"window_id": "Not Defteri"},
    ),
    (
        "file.search",
        "validation_error",
        "payload.roots must be absolute paths",
        {"roots": ["İndirilenler"], "pattern": "kaf"},
    ),
    (
        "desktop.play_audio",
        "security_scope_error",
        "audio may only be fetched from http://100.90.158.26:8001; "
        "this payload named http://100.90.158.26",
        {"url": "http://100.90.158.26/x.mp3"},
    ),
]


@pytest.fixture(autouse=True)
def _clean_sink():
    register_defect_sink(None)
    yield
    register_defect_sink(None)


@pytest.mark.parametrize(("capability", "error_class", "message", "payload"), THE_INCIDENT_DAY)
def test_every_defect_the_owner_had_to_report_would_have_filed_itself(
    capability: str, error_class: str, message: str, payload: dict
) -> None:
    filed: list = []
    register_defect_sink(filed.append)

    assert report_device_defect(
        capability=capability,
        error_class=error_class,
        error_message=message,
        payload=payload,
        command_id=str(uuid.uuid4()),
        device_id=str(uuid.uuid4()),
    )

    (draft,) = filed
    assert draft.component == DEFECT_COMPONENT, "the device is not at fault; this server is"
    assert draft.error_class == error_class
    assert capability in draft.failing_check
    assert draft.evidence["payload"] == payload, "the payload IS the defect"
    assert draft.evidence["device_message"] == message


# ------------------------------------------------------------ what is NOT our fault


@pytest.mark.parametrize(
    ("error_class", "message"),
    [
        ("ui_target_not_found", "window 'w-1-2' no longer exists"),
        ("ui_state_changed", "window exists but is no longer visible"),
        ("capability_missing", "no capable device"),
        ("device_unreachable", "offline"),
        (None, ""),
        ("", ""),
    ],
)
def test_a_failure_that_is_not_ours_files_nothing(error_class, message: str) -> None:
    """The owner closed a window; the machine was busy. Filing these would drown the real
    ones, and a backlog nobody trusts is worse than no backlog."""
    filed: list = []
    register_defect_sink(filed.append)

    assert not report_device_defect(
        capability="window.activate", error_class=error_class, error_message=message
    )
    assert filed == []
    assert not is_server_fault(error_class)


# ------------------------------------------------- one defect, not one per bad value


def test_the_same_rule_broken_with_different_values_is_one_defect() -> None:
    """Eight identical refusals arrived within ninety seconds, each quoting a different
    window name. They are one bug. A fingerprint made of the quoted VALUE would have
    opened eight incidents and said nothing."""
    checks = {
        device_defect_draft(
            capability="window.activate",
            error_class="validation_error",
            error_message=f"'{name}' is not a window id (expected w-<hwnd>-<tick>)",
        ).failing_check
        for name in ("Not Defteri", "Excel", "Adsız - Not Defteri", "Chrome")
    }
    assert len(checks) == 1, checks


def test_two_different_rules_are_two_defects() -> None:
    first = device_defect_draft(
        capability="window.activate",
        error_class="validation_error",
        error_message="'x' is not a window id (expected w-<hwnd>-<tick>)",
    )
    second = device_defect_draft(
        capability="file.search",
        error_class="validation_error",
        error_message="payload.roots must be absolute paths",
    )
    assert first is not None and second is not None
    assert first.failing_check != second.failing_check


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("'a' is not a window id", "<value> is not a window id"),
        ('"a" is not a window id', "<value> is not a window id"),
        ("window 'w-123-456' no longer exists", "window <value> no longer exists"),
        ("w-123-456 is gone", "<window_id> is gone"),
        ("depth 6 exceeds 5", "depth <n> exceeds <n>"),
        (r"C:\Users\alpak\x.txt is not allowed", "<path> is not allowed"),
        ("only http://a.b:1/c is allowed", "only <url> is allowed"),
        ("  spaced   out  ", "spaced out"),
    ],
)
def test_the_rule_survives_normalisation_and_the_values_do_not(
    message: str, expected: str
) -> None:
    assert normalise_message(message) == expected


# ----------------------------------------------------------- it never breaks the ack


def test_a_sink_that_throws_does_not_break_the_command_acknowledgement() -> None:
    """A device's acknowledgement is the device's business. It must not depend on this
    server's backlog being writable."""

    def _explode(_draft):
        raise RuntimeError("backlog is down")

    register_defect_sink(_explode)
    assert not report_device_defect(
        capability="window.activate",
        error_class="validation_error",
        error_message="'x' is not a window id",
    )


def test_with_no_sink_registered_nothing_happens_and_nothing_raises() -> None:
    assert defects._sink is None
    assert not report_device_defect(
        capability="window.activate",
        error_class="validation_error",
        error_message="'x' is not a window id",
    )


# ----------------------------------------------------- and the application arms it


def test_create_app_registers_the_sink() -> None:
    """Both halves can be perfect while nothing joins them. The module default is None, so
    without this line in ``create_app`` every defect would be computed and dropped — and
    every test above would still pass."""
    from app.config import Settings
    from app.main import create_app

    register_defect_sink(None)
    assert defects._sink is None
    create_app(Settings(_env_file=None))
    assert defects._sink is not None, "create_app did not wire the defect sink"
