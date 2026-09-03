"""app.devices.service: composes broker rows + BrokerRuntime connections into DeviceView."""

import base64
import uuid
from datetime import UTC, datetime, timedelta

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
from app.devices.presence import PRESENCE_OFFLINE, PRESENCE_ONLINE

BROKER_TABLES = [Device.__table__, DeviceSession.__table__, DeviceCommand.__table__,
                 EnrollmentToken.__table__, AuditEvent.__table__]


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


def _enroll(db: Session, *, name: str = "pc", capabilities: list[str] | None = None) -> Device:
    return broker_service.enroll_device(
        db, name=name, platform="windows", public_key_spki_b64=_spki(),
        capabilities=capabilities or ["browser.chrome"], trace_id=None,
    )


def test_offline_device_has_offline_presence_and_empty_metadata(
    db: Session, runtime: BrokerRuntime
) -> None:
    device = _enroll(db)
    view = devices_service.get_device_view(db, runtime, device.id)
    assert view is not None
    assert view.presence == PRESENCE_OFFLINE
    assert view.aliases == ()
    assert view.labels == ()
    assert view.policy == {}


def test_online_via_runtime_connections(db: Session, runtime: BrokerRuntime) -> None:
    device = _enroll(db)

    class _FakeConn:
        device_id = device.id

    runtime.connections[device.id] = _FakeConn()
    view = devices_service.get_device_view(db, runtime, device.id)
    assert view is not None
    assert view.presence == PRESENCE_ONLINE


def test_unknown_device_returns_none(db: Session, runtime: BrokerRuntime) -> None:
    assert devices_service.get_device_view(db, runtime, uuid.uuid4()) is None


def test_update_metadata_sets_aliases_labels_policy(db: Session, runtime: BrokerRuntime) -> None:
    device = _enroll(db)
    devices_service.update_metadata(
        db, device.id, aliases=["ev"], labels=["desktop"], policy={"allow": ["browser.chrome"]},
    )
    view = devices_service.get_device_view(db, runtime, device.id)
    assert view is not None
    assert view.aliases == ("ev",)
    assert view.labels == ("desktop",)
    assert view.policy == {"allow": ["browser.chrome"]}


def test_update_metadata_partial_update_preserves_other_keys(
    db: Session, runtime: BrokerRuntime
) -> None:
    device = _enroll(db)
    devices_service.update_metadata(db, device.id, aliases=["ev"])
    devices_service.update_metadata(db, device.id, labels=["desktop"])
    view = devices_service.get_device_view(db, runtime, device.id)
    assert view is not None
    assert view.aliases == ("ev",)
    assert view.labels == ("desktop",)


def test_update_metadata_unknown_device_returns_none(db: Session) -> None:
    assert devices_service.update_metadata(db, uuid.uuid4(), aliases=["x"]) is None


def test_list_device_views_includes_health_with_recent_outcomes(
    db: Session, runtime: BrokerRuntime
) -> None:
    device = _enroll(db)
    command, _created = broker_service.create_command(
        db, device_id=device.id, capability="browser.fetch_evidence", payload={},
        idempotency_key="k1", timeout_s=60, trace_id="t1",
    )
    broker_service.apply_command_ack(
        db, device_id=device.id, command_id=command.id, ack_status="succeeded",
        result={"ok": True}, error_class=None, error_message=None,
    )
    views = devices_service.list_device_views(db, runtime)
    assert len(views) == 1
    assert views[0].health is not None
    assert views[0].health.recent_outcomes[0].status == "succeeded"


def test_apply_hello_refreshes_capabilities_and_software_version(db: Session) -> None:
    device = _enroll(db, capabilities=["desktop.open_application"])
    broker_service.apply_hello(
        db, device.id, capabilities=["browser.chrome", "browser.fetch_evidence"],
        software_version="1.2.3",
    )
    refreshed = broker_service.get_device(db, device.id)
    assert refreshed is not None
    assert refreshed.capabilities_json == ["browser.chrome", "browser.fetch_evidence"]
    assert refreshed.software_version == "1.2.3"


def test_presence_stale_after_configurable_threshold(db: Session, runtime: BrokerRuntime) -> None:
    device = _enroll(db)
    broker_service.touch_last_seen(db, device.id)
    now = datetime.now(UTC) + timedelta(seconds=5)
    view = devices_service.get_device_view(db, runtime, device.id, now=now, stale_after_s=30)
    assert view is not None
    assert view.presence == "stale"


# --------------------------------------------------- alias conflicts (LOW-9)


def test_find_alias_conflict_detects_alias_used_by_another_enrolled_device(db: Session) -> None:
    a = _enroll(db, name="ev-pc")
    devices_service.update_metadata(db, a.id, aliases=["ev"])
    b = _enroll(db, name="laptop")
    conflict = devices_service.find_alias_conflict(db, device_id=b.id, aliases=["ev"])
    assert conflict == "ev"


def test_find_alias_conflict_returns_none_for_free_alias(db: Session) -> None:
    a = _enroll(db, name="ev-pc")
    devices_service.update_metadata(db, a.id, aliases=["ev"])
    b = _enroll(db, name="laptop")
    assert devices_service.find_alias_conflict(db, device_id=b.id, aliases=["is"]) is None


def test_find_alias_conflict_ignores_the_device_updating_itself(db: Session) -> None:
    a = _enroll(db, name="ev-pc")
    devices_service.update_metadata(db, a.id, aliases=["ev"])
    # a re-submitting its own existing alias (e.g. alongside a new one) must
    # not be treated as a conflict with itself.
    assert devices_service.find_alias_conflict(db, device_id=a.id, aliases=["ev", "yeni"]) is None


def test_find_alias_conflict_is_case_and_whitespace_insensitive(db: Session) -> None:
    a = _enroll(db, name="ev-pc")
    devices_service.update_metadata(db, a.id, aliases=["Ev"])
    b = _enroll(db, name="laptop")
    conflict = devices_service.find_alias_conflict(db, device_id=b.id, aliases=[" ev "])
    assert conflict == " ev "  # returns the caller's raw alias for a readable message


def test_find_alias_conflict_ignores_revoked_devices(db: Session) -> None:
    a = _enroll(db, name="ev-pc")
    devices_service.update_metadata(db, a.id, aliases=["ev"])
    broker_service.revoke_device(db, a.id, trace_id=None)
    b = _enroll(db, name="laptop")
    assert devices_service.find_alias_conflict(db, device_id=b.id, aliases=["ev"]) is None
