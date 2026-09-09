"""The device-identity contract, from both sides at once (2026-09-08 incident).

What happened: the owner staged Windows agent candidate 0.6.0. It promoted, it
started, and Cloud Core really did see it -- 40 capabilities, the whole M18.3
desktop set, the browser family. The installer's staged-update verifier
(``scripts/lib/AgentUpdate.ps1``, ``Test-AgentHeartbeatOnCore``) nonetheless
reported::

    Cloud Core does not see the candidate after 92.6 s: the device reports
    software version "", candidate is 0.6.0

and the journaled deployment engine rolled a healthy release back.

Root cause: the verifier read ``row["software_version"]`` off a ``GET
/v1/devices`` row. No such key had ever existed there. The version was
reachable only at ``row["health"]["software_version"]``. Both halves had green
suites -- the PowerShell suite fed itself a fake row it had invented, which
carried a top-level ``software_version``, and the Python suite never looked at
the verifier at all. Two correct halves, one contract, nobody reading across.

So these tests read the OTHER side's source. ``test_device_row_carries_...``
asserts what Cloud Core emits; ``test_the_windows_verifier_reads_...`` opens
``scripts/lib/AgentUpdate.ps1`` and asserts the verifier reads exactly the keys
Cloud Core emits. Renaming a key on either side fails here.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime
from app.config import Settings
from app.devices import service as devices_service
from app.devices.types import DEVICE_IDENTITY_KEYS

REPO_ROOT = Path(__file__).resolve().parents[4]
AGENT_UPDATE_PS1 = REPO_ROOT / "scripts" / "lib" / "AgentUpdate.ps1"
QUALIFY_STAGED_UPDATE_PS1 = REPO_ROOT / "scripts" / "qualify-staged-update.ps1"

BROKER_TABLES = [
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    AuditEvent.__table__,
]


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in BROKER_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def runtime() -> BrokerRuntime:
    return BrokerRuntime(Settings(_env_file=None))


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


def _enroll(db: Session, capabilities: list[str] | None = None) -> Device:
    return broker_service.enroll_device(
        db,
        name="owner-pc",
        platform="windows",
        public_key_spki_b64=_spki(),
        capabilities=capabilities or ["desktop.open_application"],
        trace_id=None,
    )


# ------------------------------------------------------- the Cloud Core half


def test_device_row_carries_the_version_the_agent_announced(
    db: Session, runtime: BrokerRuntime
) -> None:
    """The candidate says 0.6.0 in its hello; the row says 0.6.0 at the top."""
    device = _enroll(db)
    broker_service.apply_hello(
        db,
        device.id,
        capabilities=["desktop.open_application", "browser.chrome", "browser.media_play"],
        software_version="0.6.0",
    )
    broker_service.touch_last_seen(db, device.id)

    row = devices_service.get_device_view(db, runtime, device.id)
    assert row is not None
    payload = row.as_dict()

    assert payload["software_version"] == "0.6.0"
    # ...and the nested copy the Cockpit reads still agrees. One value, two
    # readable places -- never two values.
    assert payload["health"]["software_version"] == "0.6.0"
    assert payload["capability_count"] == 3
    assert payload["last_seen_at"] is not None


def test_a_device_that_never_said_hello_reports_none_not_empty_string(
    db: Session, runtime: BrokerRuntime
) -> None:
    """"I do not know" and "it announced an empty version" are different facts.

    The installer must be able to tell them apart: the first is a device that
    has not connected, the second would be a broken agent.
    """
    device = _enroll(db)
    row = devices_service.get_device_view(db, runtime, device.id)
    assert row is not None
    assert row.as_dict()["software_version"] is None


def test_every_canonical_identity_key_is_present_on_the_row(
    db: Session, runtime: BrokerRuntime
) -> None:
    device = _enroll(db)
    broker_service.apply_hello(
        db, device.id, capabilities=["desktop.open_application"], software_version="0.6.0"
    )
    payload = devices_service.get_device_view(db, runtime, device.id).as_dict()  # type: ignore[union-attr]
    missing = [key for key in DEVICE_IDENTITY_KEYS if key not in payload]
    assert not missing, f"the canonical identity keys are not all on the row: {missing}"


def test_the_listing_route_does_not_drop_the_identity_keys() -> None:
    """``GET /v1/devices`` merges ``_device_payload`` over ``view.as_dict()``.

    The merge order is the trap: a key the second dict also spells wins. This
    reads the route's source and asserts the identity keys are not among the
    ones ``_device_payload`` overwrites with a different meaning.
    """
    source = (REPO_ROOT / "services" / "api" / "app" / "broker" / "routes.py").read_text(
        encoding="utf-8"
    )
    block = source[source.index("def _device_payload(") : source.index("@router.get(\"\", ")]
    overwritten = set(re.findall(r'^\s{8}"([a-z_]+)":', block, re.MULTILINE))
    # `status` is deliberately re-computed by the route (enrollment status ->
    # presence string) and is not an identity key; `capabilities`/`last_seen_at`
    # are re-emitted with the SAME meaning, which is harmless.
    clobbered = overwritten & {"software_version", "capability_count", "presence"}
    assert not clobbered, (
        f"_device_payload overwrites canonical identity keys: {sorted(clobbered)}"
    )


# --------------------------------------------- the Windows installer's half


def _verifier_source() -> str:
    if not AGENT_UPDATE_PS1.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{AGENT_UPDATE_PS1} is not present in this checkout")
    return AGENT_UPDATE_PS1.read_text(encoding="utf-8")


def test_the_windows_verifier_reads_the_canonical_version_key() -> None:
    """The exact regression: the verifier must read a key Cloud Core emits.

    Before the fix it read ``Get-ManifestMember $row "software_version"`` from a
    row that had no such key, and the reading was ``""`` for a live 0.6.0.
    """
    source = _verifier_source()
    reader = source[source.index("function Get-DeviceRowSoftwareVersion") :]
    reader = reader[: reader.index("function Test-AgentHeartbeatOnCore")]

    assert 'Get-ManifestMember $Row "software_version"' in reader, (
        "the verifier must read the canonical top-level software_version"
    )
    assert 'Get-ManifestMember $Row "health"' in reader, (
        "it must still fall back to health.software_version, so an installer run "
        "against a Cloud Core that has not been deployed yet keeps working"
    )
    assert "software_version" in DEVICE_IDENTITY_KEYS


def test_the_windows_verifier_never_accepts_an_absent_version() -> None:
    """Forbidden fix: an empty ``software_version`` must not pass health.

    It must also be reported AS a contract fault, not as "the device reports
    version ''" -- that sentence sent the 2026-09-08 investigation to the agent
    instead of to this file.
    """
    source = _verifier_source()
    check = source[source.index("function Test-AgentHeartbeatOnCore") :]

    assert "if (-not $version) {" in check, "an absent version must be its own branch"
    assert "carries no software version at all" in check
    assert "Cloud Core contract fault" in check
    # And the "wrong version" branch is still there, unweakened.
    assert "the device reports software version" in check


def test_the_windows_verifier_still_requires_presence_and_capabilities() -> None:
    """Nothing in this fix may weaken the rest of candidate health."""
    source = _verifier_source()
    check = source[source.index("function Test-AgentHeartbeatOnCore") :]
    assert "not online" in check
    assert "the device does not advertise" in check


def test_every_key_the_windows_verifier_reads_is_a_key_the_row_carries(
    db: Session, runtime: BrokerRuntime
) -> None:
    """The general form of the 2026-09-08 bug, not just its one instance.

    The fix pinned ``software_version``. It left four other names --
    ``device_id``, ``presence``, ``capabilities``, ``last_seen_at``, plus the
    ``status`` and ``health`` fallbacks -- read by
    ``scripts/lib/AgentUpdate.ps1`` off a row this file shapes, with nothing
    asserting the two spellings agree. Renaming any of them here would repeat
    the incident exactly: the verifier reads nothing, reports the device as
    "'', not online" or "does not advertise ...", and the engine rolls a
    healthy candidate back after 90 s.

    So: collect every device-row key the verifier reads, out of its source, and
    require each one to exist on the row Cloud Core really emits.
    """
    source = _verifier_source()
    read_by_verifier = set(re.findall(r'Get-ManifestMember \$[Rr]ow "([a-z_]+)"', source))
    assert read_by_verifier, (
        "the verifier reads no device-row key at all; the regex or the reader moved"
    )

    device = _enroll(db)
    broker_service.apply_hello(
        db, device.id, capabilities=["desktop.open_application"], software_version="0.6.0"
    )
    broker_service.touch_last_seen(db, device.id)
    payload = devices_service.get_device_view(db, runtime, device.id).as_dict()  # type: ignore[union-attr]

    unserved = sorted(read_by_verifier - set(payload))
    assert not unserved, (
        f"the Windows installer's verifier reads device-row keys Cloud Core does not emit: "
        f"{unserved}. That is the 2026-09-08 failure verbatim -- it read row['software_version'] "
        f"off a row that had no such key and rolled a healthy 0.6.0 back."
    )
    # And the canonical identity is genuinely consulted, not merely emitted.
    assert {"device_id", "presence", "software_version", "capabilities"} <= read_by_verifier


# ------------------- the staged-update qualification's own fake Cloud Core


def _qualification_source() -> str:
    if not QUALIFY_STAGED_UPDATE_PS1.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{QUALIFY_STAGED_UPDATE_PS1} is not present in this checkout")
    return QUALIFY_STAGED_UPDATE_PS1.read_text(encoding="utf-8")


def test_the_staged_update_qualification_derives_its_row_from_this_file() -> None:
    """The qualification's fake Cloud Core is the same hazard, one level up.

    ``scripts/qualify-staged-update.ps1`` runs in CI on every commit and judges
    a candidate against a ``/v1/devices`` document it writes itself -- which is
    exactly the thing that made 2026-09-08 invisible ("the PowerShell half
    tested itself against a device row it had invented"). A fake is safe only
    while it is DERIVED from the source that really serves it, so the script
    parses ``DEVICE_IDENTITY_KEYS`` out of ``app/devices/types.py`` and checks
    its own row against it (gate 0). This test is what keeps that gate there.
    """
    source = _qualification_source()
    assert "function Get-CloudCoreIdentityKeys" in source, (
        "qualify-staged-update.ps1 no longer reads the canonical identity keys out of "
        "app/devices/types.py; its fake device row is then an invention again"
    )
    reader = source[source.index("function Get-CloudCoreIdentityKeys") :]
    reader = reader[: reader.index("function New-QualificationBrowserTree")]
    # It must PARSE this file, not merely mention it: the name it looks for and the path
    # it looks in are both load-bearing.
    assert "DEVICE_IDENTITY_KEYS" in reader, (
        "the qualification's reader no longer looks for DEVICE_IDENTITY_KEYS by name"
    )
    assert 'services\\api\\app\\devices\\types.py' in source, (
        "the qualification no longer reads app/devices/types.py"
    )
    assert "Get-CloudCoreIdentityKeys -TypesPath" in source, (
        "the qualification parses the canonical keys but never uses them"
    )
    listing = source[source.index("function New-CoreListing") :]
    listing = listing[: listing.index("function Get-CloudCoreIdentityKeys")]
    missing = [key for key in DEVICE_IDENTITY_KEYS if f"{key} " not in listing]
    assert not missing, (
        f"the qualification's fake device row does not carry the canonical identity keys "
        f"{missing}; a candidate judged against it is judged against a row nobody serves"
    )
