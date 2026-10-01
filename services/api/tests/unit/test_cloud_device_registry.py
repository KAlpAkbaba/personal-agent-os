"""app.devices.cloud_registry (ADR-0213 PR 1c): the writer of the two registry facts
``app.execution.wiring`` reads - the cloud device's 'bulut' alias and the ``owner_chrome``
label. Real broker tables in SQLite and the execution wiring's own ``choose``."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.broker import service as broker_service
from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession, EnrollmentToken
from app.broker.runtime import BrokerRuntime
from app.config import Settings
from app.devices import cloud_registry
from app.devices import service as devices_service
from app.devices.types import DeviceView
from app.execution import wiring
from app.execution.rule import JobKind, Target
from app.ledger.models import ActivityEventRow

TABLES = [
    Device.__table__,
    DeviceSession.__table__,
    DeviceCommand.__table__,
    EnrollmentToken.__table__,
    AuditEvent.__table__,
    ActivityEventRow.__table__,
]
OWNER_CAP = cloud_registry.OWNER_PROFILE_CAPABILITY
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


@pytest.fixture()
def runtime() -> BrokerRuntime:
    return BrokerRuntime(Settings(_env_file=None))


def _spki() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")


def _enroll(db: Session, *, name: str, platform: str, capabilities: list[str]) -> Device:
    return broker_service.enroll_device(
        db, name=name, platform=platform, public_key_spki_b64=_spki(),
        capabilities=capabilities, trace_id=None,
    )


def _view(device: Device, runtime: BrokerRuntime, db: Session) -> DeviceView:
    view = devices_service.get_device_view(db, runtime, device.id)
    assert view is not None
    return view


def _online(runtime: BrokerRuntime, device: Device) -> None:
    class _Conn:
        device_id = device.id

    runtime.connections[device.id] = _Conn()


def _view_of(platform: str, capabilities: tuple[str, ...], name: str = "pc") -> DeviceView:
    return DeviceView(
        id=uuid.uuid4(), name=name, platform=platform, status="enrolled", presence="online",
        capabilities=capabilities, enrolled_at=NOW, last_seen_at=NOW,
    )


# ------------------------------------------------------------------ the 'bulut' alias


def test_a_cloud_device_gains_the_bulut_alias_once(db: Session, runtime: BrokerRuntime) -> None:
    device = _enroll(db, name="cloud-worker", platform="cloud", capabilities=["browser.chrome"])
    assert cloud_registry.ensure_cloud_alias(db, device) is True
    assert cloud_registry.ensure_cloud_alias(db, device) is False
    assert _view(device, runtime, db).aliases == ("bulut",)


def test_a_windows_device_never_gains_the_alias(db: Session, runtime: BrokerRuntime) -> None:
    device = _enroll(db, name="bulut", platform="windows", capabilities=["browser.chrome"])
    assert cloud_registry.ensure_cloud_alias(db, device) is False
    assert _view(device, runtime, db).aliases == ()


def test_the_alias_keeps_what_the_owner_set_and_is_not_added_twice_in_another_case(
    db: Session, runtime: BrokerRuntime
) -> None:
    device = _enroll(db, name="cw", platform="cloud", capabilities=["browser.chrome"])
    devices_service.update_metadata(db, device.id, aliases=["Sunucu", "BULUT"], labels=["x"])
    assert cloud_registry.ensure_cloud_alias(db, device) is False
    view = _view(device, runtime, db)
    assert view.aliases == ("Sunucu", "BULUT")
    assert view.labels == ("x",)
    devices_service.update_metadata(db, device.id, aliases=["Sunucu"])
    assert cloud_registry.ensure_cloud_alias(db, device) is True
    assert _view(device, runtime, db).aliases == ("Sunucu", "bulut")


# ------------------------------------------------------------------ the owner_chrome label


def test_a_device_advertising_the_owner_profile_is_labelled() -> None:
    view = _view_of("windows", ("browser.chrome", OWNER_CAP))
    assert cloud_registry.owner_chrome_label(view) == wiring.OWNER_CHROME_LABEL


def test_a_device_that_does_not_advertise_it_is_not_labelled() -> None:
    # the family marker alone implies every browser.* operation in has_capability; it must
    # NOT imply the owner profile
    assert cloud_registry.owner_chrome_label(_view_of("windows", ("browser.chrome",))) is None
    assert cloud_registry.owner_chrome_label(_view_of("windows", ())) is None


def test_the_name_never_makes_a_label() -> None:
    view = _view_of("windows", ("browser.chrome",), name="owner_chrome")
    assert cloud_registry.owner_chrome_label(view) is None


def test_the_cloud_worker_is_never_the_owners_chrome() -> None:
    view = _view_of("cloud", ("browser.chrome", OWNER_CAP))
    assert cloud_registry.owner_chrome_label(view) is None


# ------------------------------------------------------------------ the hello call site


def test_the_hello_function_writes_both_facts_and_is_idempotent(
    db: Session, runtime: BrokerRuntime
) -> None:
    cloud = _enroll(db, name="cw", platform="cloud", capabilities=["browser.chrome"])
    pc = _enroll(db, name="pc", platform="windows", capabilities=["browser.chrome", OWNER_CAP])
    plain = _enroll(db, name="laptop", platform="windows", capabilities=["browser.chrome"])
    for device, writes in ((cloud, True), (pc, True), (plain, False)):
        assert cloud_registry.sync_registry_facts(db, device) is writes
        assert cloud_registry.sync_registry_facts(db, device) is False
    assert _view(cloud, runtime, db).aliases == ("bulut",)
    assert _view(cloud, runtime, db).labels == ()
    assert _view(pc, runtime, db).labels == (wiring.OWNER_CHROME_LABEL,)
    assert _view(pc, runtime, db).aliases == ()
    assert _view(plain, runtime, db).labels == ()


def test_a_label_the_owner_set_survives_the_sync(db: Session, runtime: BrokerRuntime) -> None:
    pc = _enroll(db, name="pc", platform="windows", capabilities=["browser.chrome", OWNER_CAP])
    devices_service.update_metadata(db, pc.id, labels=["masaüstü"])
    cloud_registry.sync_registry_facts(db, pc)
    assert _view(pc, runtime, db).labels == ("masaüstü", wiring.OWNER_CHROME_LABEL)


# ------------------------------------------------------ the rule reads what is written


def _choose(db: Session, runtime: BrokerRuntime, **kw):
    args = dict(
        spoken_target=None, url="https://example.com/haber", needs_signed_in_session=False,
        acting=False, scheduled=False, db=db, runtime=runtime,
    )
    args.update(kw)
    return wiring.choose(JobKind.RESEARCH, **args)


def test_wiring_choose_selects_cloud_from_the_written_facts(
    db: Session, runtime: BrokerRuntime
) -> None:
    pc = _enroll(db, name="pc", platform="windows", capabilities=["browser.chrome"])
    _online(runtime, pc)
    near_miss = _choose(db, runtime, spoken_target="bulutta")
    assert near_miss.target is not Target.CLOUD  # a Windows machine is never the cloud
    cloud = _enroll(db, name="cw", platform="cloud", capabilities=["browser.chrome"])
    _online(runtime, cloud)
    cloud_registry.sync_registry_facts(db, cloud)
    decision = _choose(db, runtime, spoken_target="bulutta")
    assert decision.outcome == "selected"
    assert decision.target is Target.CLOUD


def test_wiring_choose_selects_owner_chrome_from_the_written_label(
    db: Session, runtime: BrokerRuntime
) -> None:
    pc = _enroll(db, name="pc", platform="windows", capabilities=["browser.chrome", OWNER_CAP])
    _online(runtime, pc)
    before = _choose(db, runtime, needs_signed_in_session=True)
    assert before.target is not Target.OWNER_CHROME
    cloud_registry.sync_registry_facts(db, pc)
    after = _choose(db, runtime, needs_signed_in_session=True)
    assert after.outcome == "selected"
    assert after.target is Target.OWNER_CHROME
