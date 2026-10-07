"""Aktivra's 'önemli' channel (card aktivra-inbound-events): POST /v1/aktivra/events.

Aktivra is a separate project; what crosses the line is "something important happened" and a
short title, nothing more. Driven through the REAL application object (``create_app``; the
router is found by ``app.registry``), with the artifacts runtime swapped for an in-memory
SQLite holding the notifications, ledger and aktivra tables - the way
test_identity_enforcement swaps the identity runtime. The Postgres half (unique event id in
the database, the migration up/down) is tests/integration/test_aktivra_inbound_pg.py.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.aktivra import auth, service
from app.aktivra.models import AktivraEventRow
from app.aktivra.routes import MAX_BODY_BYTES, MAX_EVENTS_PER_HOUR, AktivraEvent
from app.config import Settings
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.ledger import vocabulary as v
from app.ledger.models import ActivityEventRow
from app.main import create_app
from app.notifications.models import PRIORITY_NORMAL, PRIORITY_URGENT, NotificationRow
from app.telephony import policy
from tests.identity_support import bearer, make_identity_engine

REPO_ROOT = Path(__file__).resolve().parents[4]
PROTOCOL_DOC = REPO_ROOT / "packages" / "protocol" / "AKTIVRA_EVENTS.md"
TOKEN = "pagentos_ak_" + "T" * 43
WRONG = "pagentos_ak_" + "W" * 43
EVENTS = "/v1/aktivra/events"
STATUS = "/v1/aktivra/status"


def _event(**over) -> dict:
    body = {
        "event_id": "evt-" + uuid.uuid4().hex[:12],
        "title": "Yeni sözleşme imzalandı",
        "summary": "Kısa özet.",
        "severity": "important",
        "occurred_at": "2026-10-07T09:00:00Z",
    }
    body.update(over)
    return body


def _factory():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        NotificationRow.__table__,
        ActivityEventRow.__table__,
        AktivraEventRow.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _build(token: str = TOKEN):
    settings = Settings(_env_file=None, aktivra_inbound_token=SecretStr(token))
    app = create_app(settings)
    runtime = IdentityRuntime(
        settings, engine=make_identity_engine(), root=InMemoryCredentialRoot()
    )
    runtime.service.bootstrap()
    app.state.identity = runtime
    factory = _factory()
    app.state.artifacts = SimpleNamespace(session=factory)
    return TestClient(app), runtime, factory, app


@pytest.fixture()
def built():
    client, runtime, factory, app = _build()
    yield client, runtime, factory, app
    client.close()


def _post(client, body, token: str | None = TOKEN):
    headers = bearer(token) if token is not None else {}
    return client.post(EVENTS, json=body, headers=headers)


def _rows(factory, model):
    with factory() as db:
        return list(db.execute(select(model)).scalars())


# ------------------------------------------------------------------ authentication


def test_no_token_configured_means_no_channel_404() -> None:
    client, _, factory, _ = _build(token="")
    try:
        assert _post(client, _event()).status_code == 404
        assert _post(client, _event(), token=None).status_code == 404
        assert _rows(factory, NotificationRow) == []
    finally:
        client.close()


def test_a_wrong_token_is_401_with_a_fingerprinted_ledger_line(built) -> None:
    client, _, factory, _ = built
    response = _post(client, _event(), token=WRONG)
    assert response.status_code == 401
    assert WRONG not in response.text
    rows = [r for r in _rows(factory, ActivityEventRow) if r.subsystem == v.SUBSYSTEM_AKTIVRA]
    assert [r.event_type for r in rows] == [v.EVENT_TYPE_AKTIVRA_REJECTED]
    assert rows[0].detail_json["fingerprint"] == auth.fingerprint_of(WRONG)
    assert WRONG not in repr(rows[0].detail_json) + rows[0].factual_summary
    assert _rows(factory, NotificationRow) == []


def test_a_missing_header_is_401(built) -> None:
    client, _, _, _ = built
    assert _post(client, _event(), token=None).status_code == 401


def test_the_owners_session_token_does_not_pass_here(built) -> None:
    client, runtime, factory, _ = built
    issued = runtime.service.issue_session(client_kind="cli")
    assert _post(client, _event(), token=issued.token).status_code == 401
    assert _rows(factory, NotificationRow) == []


def test_the_aktivra_token_passes_on_no_owner_route(built) -> None:
    client, _, _, _ = built
    assert client.get(STATUS, headers=bearer(TOKEN)).status_code == 401
    assert client.get("/v1/notifications", headers=bearer(TOKEN)).status_code == 401
    assert client.get("/v1/urgent-alert/status", headers=bearer(TOKEN)).status_code == 401


def test_the_comparison_is_constant_time_over_hashes(monkeypatch) -> None:
    """Mutation (a): ``==`` instead of ``hmac.compare_digest`` turns this red."""
    seen: list[tuple[str, str]] = []
    real = auth.hmac.compare_digest

    def spy(left, right):
        seen.append((left, right))
        return real(left, right)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    configured = SecretStr(TOKEN)
    assert auth.verify(f"Bearer {TOKEN}", configured).ok
    assert not auth.verify(f"Bearer {WRONG}", configured).ok
    assert len(seen) == 2
    # Hashes are compared, never the raw tokens.
    for left, right in seen:
        assert TOKEN not in (left, right) and WRONG not in (left, right)
        assert len(left) == len(right) == 64
    source = Path(auth.__file__).read_text("utf-8")
    assert not re.search(r"(presented|configured|expected|token)\w*\s*==", source)


def test_a_minted_token_is_opaque_and_prefixed() -> None:
    token = auth.new_inbound_token()
    assert token.startswith("pagentos_ak_")
    assert len(token) == len("pagentos_ak_") + 43
    assert auth.new_inbound_token() != token


# ------------------------------------------------------------------- acceptance


def test_an_important_event_is_an_urgent_aktivra_notification(built) -> None:
    client, _, factory, _ = built
    body = _event()
    response = _post(client, body)
    assert response.status_code == 201
    notification_id = response.json()["notification_id"]
    [row] = _rows(factory, NotificationRow)
    assert str(row.id) == notification_id
    assert row.kind == "aktivra.important"
    assert row.priority == PRIORITY_URGENT
    assert row.title == body["title"]
    assert row.body == body["summary"]
    assert row.group_key == "aktivra:" + body["event_id"]
    ledger = [r for r in _rows(factory, ActivityEventRow) if r.subsystem == v.SUBSYSTEM_AKTIVRA]
    assert [r.event_type for r in ledger] == [v.EVENT_TYPE_AKTIVRA_RECEIVED]
    assert ledger[0].source_ref == f"notification:{notification_id}"


def test_an_info_event_is_a_normal_notification(built) -> None:
    client, _, factory, _ = built
    assert _post(client, _event(severity="info", summary=None)).status_code == 201
    [row] = _rows(factory, NotificationRow)
    assert row.kind == "aktivra.info"
    assert row.priority == PRIORITY_NORMAL
    assert row.body == ""


def test_quiet_hours_defer_info_but_never_important() -> None:
    factory = _factory()
    night = datetime(2026, 10, 7, 0, 30, tzinfo=UTC)  # 03:30 in Istanbul
    with factory() as db:
        important = service.accept(
            db, event_id="night-important", title="t", summary="", severity="important", now=night
        )
        info = service.accept(
            db, event_id="night-info-0001", title="t", summary="", severity="info", now=night
        )
    rows = {r.kind: r for r in _rows(factory, NotificationRow)}
    assert important.created and info.created
    assert rows["aktivra.important"].deferred_until is None
    assert rows["aktivra.info"].deferred_until is not None


def test_the_same_event_id_twice_is_200_and_one_notification(built) -> None:
    """Mutation (b): without the duplicate check this turns red."""
    client, _, factory, _ = built
    body = _event()
    first = _post(client, body)
    second = _post(client, body)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["notification_id"] == first.json()["notification_id"]
    assert len(_rows(factory, NotificationRow)) == 1
    assert len(_rows(factory, AktivraEventRow)) == 1


def test_the_eleventh_event_in_an_hour_is_429(built) -> None:
    client, _, factory, _ = built
    accepted = [_event() for _ in range(MAX_EVENTS_PER_HOUR)]
    for body in accepted:
        assert _post(client, body).status_code == 201
    assert MAX_EVENTS_PER_HOUR == 10
    assert _post(client, _event()).status_code == 429
    # A retry of one already accepted is still the same answer, not a refusal.
    assert _post(client, accepted[0]).status_code == 200
    assert len(_rows(factory, NotificationRow)) == MAX_EVENTS_PER_HOUR


def test_the_hour_is_counted_from_the_table() -> None:
    factory = _factory()
    start = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    with factory() as db:
        for i in range(MAX_EVENTS_PER_HOUR):
            service.accept(
                db, event_id=f"hour-{i:04d}", title="t", summary="", severity="info", now=start
            )
        assert service.events_in_last_hour(db, now=start + timedelta(minutes=59)) == 10
        assert service.events_in_last_hour(db, now=start + timedelta(minutes=61)) == 0


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("extra field", _event(customer="Ahmet Yılmaz")),
        ("attachment", _event(attachment="aGVsbG8=")),
        ("121-char title", _event(title="a" * 121)),
        ("281-char summary", _event(summary="a" * 281)),
        ("short event id", _event(event_id="short")),
        ("65-char event id", _event(event_id="e" * 65)),
        ("unknown severity", _event(severity="critical")),
        ("not a time", _event(occurred_at="dün")),
        ("empty title", _event(title="")),
    ],
)
def test_refused_bodies_are_422(built, label: str, body: dict) -> None:
    """Mutation (c): without the title bound the 121-character case turns red."""
    client, _, factory, _ = built
    response = _post(client, body)
    assert response.status_code == 422, label
    assert _rows(factory, NotificationRow) == []


def test_a_body_over_4_kb_is_422(built) -> None:
    client, _, factory, _ = built
    assert MAX_BODY_BYTES == 4096
    padded = '{"event_id": "evt-big-000001", "pad": "' + "x" * MAX_BODY_BYTES + '"}'
    response = client.post(
        EVENTS,
        content=padded.encode(),
        headers={**bearer(TOKEN), "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert _rows(factory, NotificationRow) == []


def test_a_title_of_exactly_120_is_accepted(built) -> None:
    client, _, _, _ = built
    assert _post(client, _event(title="a" * 120, summary="b" * 280)).status_code == 201


# ------------------------------------------------------------------- status


def test_status_needs_the_owner_and_carries_no_token(built) -> None:
    client, runtime, _, _ = built
    assert client.get(STATUS).status_code == 401
    session = runtime.service.issue_session(client_kind="cli")
    empty = client.get(STATUS, headers=bearer(session.token)).json()
    assert empty == {"configured": True, "last_event_at": None, "events_24h": 0}
    assert _post(client, _event()).status_code == 201
    after = client.get(STATUS, headers=bearer(session.token))
    assert after.status_code == 200
    assert after.json()["events_24h"] == 1
    assert after.json()["last_event_at"].endswith("Z")
    assert TOKEN not in after.text


def test_status_says_not_configured_without_a_token() -> None:
    client, runtime, _, _ = _build(token="")
    try:
        session = runtime.service.issue_session(client_kind="cli")
        body = client.get(STATUS, headers=bearer(session.token)).json()
        assert body["configured"] is False
    finally:
        client.close()


# ------------------------------------------------------------------- secrets


def test_no_token_value_reaches_a_log_record_or_a_response(built, caplog) -> None:
    client, _, _, _ = built
    caplog.set_level(logging.DEBUG)
    texts = [
        _post(client, _event(), token=WRONG).text,
        _post(client, _event()).text,
        _post(client, _event(title="a" * 121)).text,
    ]
    for record in caplog.records:
        assert TOKEN not in record.getMessage() and WRONG not in record.getMessage()
        assert TOKEN not in repr(record.__dict__) and WRONG not in repr(record.__dict__)
    assert caplog.text.count(TOKEN) == 0 and caplog.text.count(WRONG) == 0
    for text in texts:
        assert TOKEN not in text and WRONG not in text


def test_the_settings_never_show_the_token() -> None:
    settings = Settings(_env_file=None, aktivra_inbound_token=SecretStr(TOKEN))
    assert TOKEN not in repr(settings)
    assert Settings(_env_file=None).aktivra_inbound_token.get_secret_value() == ""


# ------------------------------------------------------------------- importance


def test_aktivra_important_is_in_the_one_importance_list() -> None:
    """If the telephony policy list drops the kind, the phone stops ringing - red here."""
    assert service.KIND_IMPORTANT == policy.KIND_AKTIVRA_IMPORTANT == "aktivra.important"
    assert "aktivra.important" in policy.important_notification_kinds()
    assert policy.is_important_notification("aktivra.important")
    assert policy.alert_category("aktivra.important") == "Aktivra"
    assert policy.call_kind_for_notification("aktivra.important") == "aktivra.important"
    assert not policy.is_important_notification(service.KIND_INFO)


def test_the_vocabulary_knows_the_subsystem_and_both_events() -> None:
    assert v.SUBSYSTEM_AKTIVRA == "aktivra"
    assert v.SUBSYSTEM_AKTIVRA in v.SUBSYSTEMS
    assert v.EVENT_TYPE_AKTIVRA_RECEIVED == "aktivra.received"
    assert v.EVENT_TYPE_AKTIVRA_REJECTED == "aktivra.rejected"
    assert {v.EVENT_TYPE_AKTIVRA_RECEIVED, v.EVENT_TYPE_AKTIVRA_REJECTED} <= set(v.EVENT_TYPES)
    for name in ("SUBSYSTEM_AKTIVRA", "EVENT_TYPE_AKTIVRA_RECEIVED", "EVENT_TYPE_AKTIVRA_REJECTED"):
        assert name in v.__all__


# ------------------------------------------------------------------- retention


def test_rows_older_than_30_days_are_swept_and_notifications_stay() -> None:
    factory = _factory()
    now = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    with factory() as db:
        service.accept(
            db, event_id="old-event-001", title="t", summary="", severity="info",
            now=now - timedelta(days=31),
        )
        service.accept(
            db, event_id="new-event-001", title="t", summary="", severity="info",
            now=now - timedelta(days=29),
        )
        assert service.sweep_expired(db, now=now) == 1
    assert [r.event_id for r in _rows(factory, AktivraEventRow)] == ["new-event-001"]
    assert len(_rows(factory, NotificationRow)) == 2


def test_the_real_application_runs_the_sweep() -> None:
    """main.py names the sweep (the one line this card asks of it)."""
    app = create_app(Settings(_env_file=None))
    assert "aktivra_events" in app.state.retention_sweeper.names


# ------------------------------------------------------------------- the contract


def _doc_fields() -> list[str]:
    text = PROTOCOL_DOC.read_text("utf-8")
    section = text.split("## Gövde şeması", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, flags=re.MULTILINE)


def test_the_protocol_document_counts_the_same_fields_as_the_model() -> None:
    assert sorted(_doc_fields()) == sorted(AktivraEvent.model_fields)
    text = PROTOCOL_DOC.read_text("utf-8")
    for needle in (EVENTS, "Authorization: Bearer", "4096", "120", "280", "429", "müşteri"):
        assert needle in text, needle


def test_the_models_bounds_are_the_documents() -> None:
    fields = AktivraEvent.model_fields
    bounds = {
        name: [m for m in field.metadata if hasattr(m, "max_length")]
        for name, field in fields.items()
    }
    assert bounds["title"][0].max_length == 120
    assert bounds["summary"][0].max_length == 280
    assert bounds["event_id"][0].max_length == 64
    assert AktivraEvent.model_config.get("extra") == "forbid"
