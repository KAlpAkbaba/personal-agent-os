"""Structural guards on the wake alarm (M18.3 spec §1.2, §11).

These read the SOURCE rather than run it, the same technique
``tests/unit/test_presence_privacy_structure.py`` uses for the perception boundary and the
companion's own display suite uses for its power APIs. They exist because the properties
below cannot be observed from a passing test at 07:30 — only from the code not being able
to violate them.

Four properties:

1. **The alarm never touches a realtime session.** The owner is asleep; there is no voice
   session. A greeting that reached for one would work in every test written by someone
   awake and fail on the only morning that matters.
2. **Display off is never system sleep** (spec §1.2). No shutdown, suspend, hibernate,
   logoff or lock API name may appear anywhere in the alarms or ambient packages.
3. **Every device call has a receipt.** The capabilities the sequence dispatches and the
   receipt enumeration are the same set.
4. **The sequence has no presence or eye dependency** (spec §11).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
ALARMS = APP / "alarms"
AMBIENT = APP / "ambient"

ALARM_SOURCES = sorted(ALARMS.glob("*.py"))
AMBIENT_SOURCES = sorted(AMBIENT.glob("*.py"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imported_names(path: Path) -> set[str]:
    """Every name this module imports, by static parse — a grep would also match the word
    inside a docstring, and these files talk ABOUT what they must not import."""
    tree = ast.parse(_text(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                names.add(f"{module}.{alias.name}")
                names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


def test_the_alarms_package_has_sources_to_check() -> None:
    """A guard on the guards: a rename that emptied these globs would make every structural
    test below pass vacuously."""
    assert len(ALARM_SOURCES) >= 8, [p.name for p in ALARM_SOURCES]
    assert len(AMBIENT_SOURCES) >= 4, [p.name for p in AMBIENT_SOURCES]


# ------------------------------------------------- 1. no realtime session, no microphone


@pytest.mark.parametrize("path", ALARM_SOURCES, ids=lambda p: p.name)
def test_no_alarm_module_imports_a_realtime_session(path: Path) -> None:
    """Spec §11: "the greeting never touches ``RealtimeSessionRow``"."""
    imported = _imported_names(path)
    assert "RealtimeSessionRow" not in imported, path.name
    assert not any(name.startswith("app.voice.realtime_sessions") for name in imported), path.name


@pytest.mark.parametrize("path", ALARM_SOURCES, ids=lambda p: p.name)
def test_no_alarm_module_reaches_for_a_microphone_or_the_camera(path: Path) -> None:
    imported = _imported_names(path)
    for forbidden in ("app.presence.eye", "app.presence.engine", "app.presence.observations"):
        assert forbidden not in imported, f"{path.name} imports {forbidden}"


def test_the_wake_sequence_has_no_presence_or_eye_dependency() -> None:
    """Spec §11: "the sequence has no presence/eye dependency".

    Checked on the SEQUENCE specifically, and by name rather than by import, because a
    presence read could equally arrive as an attribute access on something already
    imported.
    """
    source = _text(ALARMS / "sequence.py")
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    for name in ("PresenceState", "is_eye_enabled", "presence_service", "get_engine"):
        assert name not in body, f"app/alarms/sequence.py refers to {name}"


# ------------------------------------------------------ 2. display off is not system sleep

#: Every API name that would put the MACHINE to sleep rather than the SCREEN. Spec §1.2:
#: display power is the only machine-state capability in this milestone, and no code path
#: here locks, sleeps, hibernates, logs off, reboots or shuts down. The Windows companion
#: has the same guard over its own display files; this is the cloud half of it.
FORBIDDEN_POWER_APIS = (
    "SetSuspendState",
    "SetSystemPowerState",
    "ExitWindowsEx",
    "InitiateShutdown",
    "InitiateSystemShutdown",
    "LockWorkStation",
    "shutdown",
    "hibernate",
    "suspend",
    "logoff",
    "log_off",
    "reboot",
)


@pytest.mark.parametrize("path", ALARM_SOURCES + AMBIENT_SOURCES, ids=lambda p: p.name)
def test_nothing_in_these_packages_can_sleep_or_lock_the_machine(path: Path) -> None:
    source = _text(path)
    # Comments and docstrings are allowed to NAME what is forbidden (this test's own
    # module docstring does); only executable text is screened.
    tree = ast.parse(source)
    docstrings = {ast.get_docstring(n) or "" for n in ast.walk(tree) if isinstance(
        n, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
    )}
    body_lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or any(stripped and stripped in d for d in docstrings):
            continue
        body_lines.append(line)
    body = "\n".join(body_lines)
    for doc in docstrings:
        body = body.replace(doc, "")
    for api in FORBIDDEN_POWER_APIS:
        assert not re.search(rf"\b{re.escape(api)}\b", body, re.IGNORECASE), (
            f"{path.name} names the machine-power API {api!r}; M18.3 spec §1.2 allows the "
            "DISPLAY to be powered off and nothing else"
        )


def test_the_only_machine_state_capability_is_the_display() -> None:
    """The cloud may ask a device for exactly one power-related thing."""
    from app.alarms.sequence import RECEIPT_BY_DEVICE_CALL

    power_capabilities = {
        c for c in RECEIPT_BY_DEVICE_CALL if "display" in c or "power" in c or "sleep" in c
    }
    assert power_capabilities == {"desktop.display_off", "desktop.display_wake"}


# ------------------------------------------------------ 3. every device call has a receipt


def _dispatched_capabilities() -> set[str]:
    """The capability constants ``sequence.py`` actually passes to ``self._run_step``.

    Restricted to that ONE call site on purpose: ``_run_step`` is the single funnel through
    which this module reaches a device, and it is also the only place that writes a
    receipt. Scanning every ``capability=`` keyword would also pick up the ``StepOutcome``
    the funnel returns, which proves nothing.
    """
    tree = ast.parse(_text(ALARMS / "sequence.py"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "_run_step"):
            continue
        for keyword in node.keywords:
            if keyword.arg == "capability" and isinstance(keyword.value, ast.Name):
                found.add(keyword.value.id)
    return found


def test_every_capability_the_sequence_dispatches_maps_to_a_receipt() -> None:
    """Spec §11's structural row: "the sequence's device calls are enumerated and each maps
    to a receipt capability".

    A step added later without an entry in ``RECEIPT_BY_DEVICE_CALL`` raises a ``KeyError``
    at runtime — which is the right failure, and this test is what turns it into a failure
    at authoring time instead.
    """
    import app.alarms.sequence as sequence_mod
    from app.alarms.sequence import RECEIPT_BY_DEVICE_CALL

    dispatched = _dispatched_capabilities()
    assert dispatched, "the AST scan found no dispatched capabilities — it stopped matching"
    for constant in dispatched:
        capability = getattr(sequence_mod, constant)
        assert capability in RECEIPT_BY_DEVICE_CALL, (
            f"{constant} ({capability}) is dispatched with no receipt capability"
        )


def test_the_receipt_enumeration_has_no_dead_entries() -> None:
    """The other direction: an entry for a capability nothing dispatches is a claim the
    record does not support."""
    import app.alarms.sequence as sequence_mod
    from app.alarms.sequence import RECEIPT_BY_DEVICE_CALL

    dispatched = {getattr(sequence_mod, c) for c in _dispatched_capabilities()}
    assert set(RECEIPT_BY_DEVICE_CALL) == dispatched


def test_the_receipt_capabilities_are_the_ones_the_spec_names() -> None:
    from app.alarms.sequence import RECEIPT_BY_DEVICE_CALL

    assert set(RECEIPT_BY_DEVICE_CALL.values()) == {
        "alarm.arm",
        "alarm.disarm",
        "alarm.start",
        "alarm.stop",
        "display.wake",
        "display.off",
        "greeting.play",
        "media.play",
        "media.volume",
        "media.status",
        "media.stop",
    }


# ------------------------------------------------------------------ 4. no bypass anywhere


@pytest.mark.parametrize("path", ALARM_SOURCES, ids=lambda p: p.name)
def test_nothing_here_attempts_to_get_past_a_challenge(path: Path) -> None:
    """Spec §1.6: a CAPTCHA, consent wall or autoplay policy is a RECORDED failure and the
    fallback tone — never something to work around."""
    source = _text(path).lower()
    for word in ("captcha", "recaptcha", "solve_challenge", "bypass"):
        assert word not in source, f"{path.name} mentions {word!r}"
