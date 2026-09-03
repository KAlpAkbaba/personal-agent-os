"""Architectural invariant (ADR-0049): PersonalAgentOS is never tied to one machine.

The K66 qualification is a per-device audio-quality qualification; the product must
run and be centrally managed from any owner-authorised device. This test is the
cheap guard that keeps day-to-day work from drifting single-machine: application code
(not docs, not tests, not the owner's local dev configuration) must not carry a
hardcoded machine SID, a specific microphone/device label, a machine name, or the
owner's tailnet addresses. Anything device-specific belongs in a per-device profile
or in Cloud Core's device inventory.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]

APP_ROOTS = (
    REPO / "services" / "api" / "app",
    REPO / "services" / "browser" / "browser_agent",
    REPO / "apps" / "web" / "app",
    REPO / "devices" / "windows-agent" / "src",
)
SUFFIXES = {".py", ".ts", ".tsx", ".cs", ".json"}

FORBIDDEN = (
    (re.compile(r"S-1-5-21-\d+-\d+-\d+"), "a machine/domain-specific Windows SID"),
    (re.compile(r"\bK66\b"), "the owner's current microphone model as a literal"),
    (re.compile(r"\bDESKTOP-[A-Z0-9]{6,}\b"), "a Windows machine name"),
    # (?!/\d{1,2}) excludes a CIDR-notated network (e.g. "100.64.0.0/10", the
    # RFC 6598 CGNAT block definition app.research.destination checks other
    # addresses against) from this guard — that is a protocol-level range,
    # not one owner's specific tailnet address, which is what this pattern
    # exists to catch (a bare "100.x.x.x" with no prefix length still is).
    (
        re.compile(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b(?!/\d{1,2})"),
        "a tailnet IP address (CGNAT range)",
    ),
    (re.compile(r"\btail[0-9a-f]{6}\.ts\.net\b"), "the owner's tailnet MagicDNS domain"),
    (re.compile(r"[A-Za-z]:\\\\Users\\\\[A-Za-z0-9_]+"), "a user-profile path on one PC"),
)


def _sources() -> list[Path]:
    out: list[Path] = []
    for root in APP_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix in SUFFIXES and "node_modules" not in path.parts \
                    and "__pycache__" not in path.parts and ".next" not in path.parts \
                    and "bin" not in path.parts and "obj" not in path.parts:
                out.append(path)
    return out


def test_application_code_carries_no_single_machine_literals() -> None:
    offenders: list[str] = []
    for path in _sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, why in FORBIDDEN:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(REPO)}:{line}: {why} ({match.group(0)!r})")
    assert not offenders, "single-machine literal(s) in application code:\n" + "\n".join(offenders)


def test_per_device_state_is_keyed_by_device_not_global() -> None:
    # The web client's microphone profiles are stored per device fingerprint (ADR-0044)
    # and the Arbor target is an owner-level setting on the server (ADR-0043): a noisy
    # K66 profile can never touch another machine's microphone configuration.
    profile_path = REPO / "apps" / "web" / "app" / "lib" / "voice" / "profile.ts"
    profile = profile_path.read_text(encoding="utf-8")
    assert "fingerprint" in profile and "deviceId" in profile
    config = (REPO / "services" / "api" / "app" / "config.py").read_text(encoding="utf-8")
    assert "voice_realtime_owner_target_voice_profile" in config
