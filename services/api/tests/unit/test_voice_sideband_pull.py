"""The web shell's sideband pull: GET /v1/voice/realtime/sessions/{id}/sideband.

A web session is pull-only. Before this endpoint the shell drained ``pending_sideband``
only when the owner spoke (POST /events) or re-attached, so a briefing queued for an open,
silent tab waited for the owner's next sentence. The pull closes that wait, under three
rules asserted here:

- a frame is drained by the request that actually takes it, and THAT request stamps the
  briefing delivered ("a queue is not a delivery");
- an empty pull writes nothing at all - not the context, not ``updated_at``, not an audit
  row - so a forgotten tab polling every 15 s never keeps a dead session alive past the
  idle sweep;
- the identity rules are /events' own: wrong leg -> 409, a session that is over -> 410.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.broker.models import AuditEvent
from app.ledger import briefing as briefing_service
from app.ledger import service as ledger_service
from app.ledger.models import PendingBriefingRow
from app.ledger.vocabulary import EVENT_TYPE_RESEARCH_COMPLETED
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.models import RealtimeSessionRow
from app.voice.realtime_sessions.sideband import SB_SAY, sideband_frame
from tests.identity_support import bearer
from tests.unit.test_voice_realtime_sessions import _create, wired  # noqa: F401 - fixture


def _url(sid: str) -> str:
    return f"/v1/voice/realtime/sessions/{sid}/sideband"


def _queue_briefing(runtime) -> uuid.UUID:
    with runtime.session() as db:
        event = ledger_service.record(
            db,
            ledger_service.ActivityEvent(
                event_type=EVENT_TYPE_RESEARCH_COMPLETED,
                subsystem="research",
                action="completed",
                factual_summary="Araştırma tamamlandı.",
                occurred_at=datetime.now(UTC),
                detail_json={"findings": 2},
                source="live",
                source_ref=f"test:{uuid.uuid4()}",
            ),
        )
        queued = briefing_service.queue_briefing(db, event)
        assert queued is not None
        return queued.briefing_id


def _queue_frames(runtime, sid: str, briefing_id: uuid.UUID) -> None:
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        service.queue_sideband_frame(
            db,
            row,
            sideband_frame(
                row.id,
                SB_SAY,
                {"text": "Efendim, araştırma tamamlandı.", "briefing_ids": [str(briefing_id)]},
            ),
        )
        service.queue_sideband_frame(
            db, row, sideband_frame(row.id, "tool_completed", {"call_id": "r", "result": {}})
        )


def _audit_count(runtime, sid: str) -> int:
    with runtime.session() as db:
        return db.execute(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.subject_ref == sid)
        ).scalar_one()


def _snapshot(runtime, sid: str) -> tuple[dict, datetime, int]:
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        ctx, updated = dict(row.context_json or {}), row.updated_at
    return ctx, updated, _audit_count(runtime, sid)


def test_a_pull_takes_every_pending_frame_once_and_stamps_the_briefing(wired) -> None:  # noqa: F811
    client, _identity, runtime, _sideband, _issued, _engine = wired
    sid = _create(client)["session_id"]
    briefing_id = _queue_briefing(runtime)
    _queue_frames(runtime, sid, briefing_id)
    with runtime.session() as db:
        assert db.get(PendingBriefingRow, briefing_id).delivered_at is None, "queue != delivery"

    first = client.get(_url(sid))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["session_id"] == sid
    assert [f["event"] for f in body["pending_sideband"]] == [SB_SAY, "tool_completed"]
    # the frame shape is /events' own (contract.ts SidebandFrame)
    assert set(body["pending_sideband"][0]) == {"type", "session_id", "event", "payload", "at"}
    assert body["pending_sideband"][0]["payload"]["briefing_ids"] == [str(briefing_id)]

    with runtime.session() as db:
        stamped = db.get(PendingBriefingRow, briefing_id)
        assert stamped.delivered_at is not None
        assert stamped.delivered_via == briefing_service.VIA_VOICE
        assert db.get(RealtimeSessionRow, uuid.UUID(sid)).context_json["pending_sideband"] == []

    second = client.get(_url(sid))
    assert second.status_code == 200
    assert second.json()["pending_sideband"] == []


def test_an_empty_pull_writes_nothing_and_never_keeps_a_session_alive(wired) -> None:  # noqa: F811
    client, _identity, runtime, _sideband, _issued, _engine = wired
    sid = _create(client)["session_id"]
    # quiet for longer than the idle sweep's threshold
    quiet_since = service.utcnow() - service.IDLE_SESSION_AFTER - timedelta(minutes=5)
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        row.updated_at = quiet_since
        db.commit()

    before = _snapshot(runtime, sid)
    for _ in range(3):
        response = client.get(_url(sid))
        assert response.status_code == 200, response.text
        assert response.json() == {"session_id": sid, "pending_sideband": []}
    after = _snapshot(runtime, sid)

    assert after[0] == before[0], "context_json changed on an empty pull"
    assert after[1] == before[1], "updated_at changed on an empty pull"
    assert after[2] == before[2], "an empty pull wrote an audit row"

    with runtime.session() as db:
        swept = service.sweep_idle_sessions(db)
    assert uuid.UUID(sid) in swept, "empty pulls kept an idle session alive"


def test_the_pull_refuses_exactly_as_events_does(wired) -> None:  # noqa: F811
    client, identity, runtime, _sideband, _issued, _engine = wired
    sid = _create(client)["session_id"]
    events = {"events": [{"kind": "end_of_turn", "t_ms": 1}]}

    # another owner session that does not hold the leg
    other = identity.service.issue_session(client_kind="mobile", label="phone")
    pulled = client.get(_url(sid), headers=bearer(other.token))
    posted = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events", json=events, headers=bearer(other.token)
    )
    assert pulled.status_code == posted.status_code == 409
    assert pulled.json()["detail"]["error_class"] == posted.json()["detail"]["error_class"]

    # an unknown session id
    assert client.get(_url(str(uuid.uuid4()))).status_code == 404

    # a closed session
    client.post(f"/v1/voice/realtime/sessions/{sid}/close", json={"reason": "owner_hung_up"})
    closed_pull = client.get(_url(sid))
    closed_post = client.post(f"/v1/voice/realtime/sessions/{sid}/events", json=events)
    assert closed_pull.status_code == closed_post.status_code == 410
    assert closed_pull.json()["detail"] == closed_post.json()["detail"]

    # an expired one
    expired = _create(client, session_ttl_s=60)["session_id"]
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(expired))
        row.expires_at = service.utcnow() - service.timedelta(seconds=1)
        db.commit()
    assert client.get(_url(expired)).status_code == 410
