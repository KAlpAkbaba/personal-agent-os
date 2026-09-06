"""The eye actions and the refused release, over the real tool relay
(docs/M18_ACTION_CONTRACT.md §5, §2; tests §8).

The owner's defect (2026-09-06): "Gözünü kapat." really disabled the eye and the model said
"öyle olmuş gibi düşün"; "Gözünü aç" did nothing. Every case below runs through
``handle_tool_call`` on SQLite with the real ``app.presence.eye`` module, and asserts the
receipt, its exact speech, the ``action.receipt`` ledger row and what ``session_activity``
exposes - never a transcript.
"""

# ruff: noqa: F811 - the shared `wired` fixture is imported and then named as a parameter
from __future__ import annotations

import uuid

import pytest

from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_ACTION_RECEIPT,
    EVENT_TYPE_EYE_DISABLED,
    SUBSYSTEM_DEPLOYMENT,
    SUBSYSTEM_PRESENCE,
)
from app.presence.engine import PresenceFusionEngine, get_engine, set_engine
from app.presence.eye import disable_eye, is_eye_enabled
from app.presence.service import reset_heartbeat
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.models import RealtimeSessionRow
from tests.unit.test_voice_realtime_sessions import _create, wired  # noqa: F401


@pytest.fixture(autouse=True)
def _fresh_presence():
    previous = get_engine()
    set_engine(PresenceFusionEngine())
    reset_heartbeat()
    try:
        yield
    finally:
        set_engine(previous)
        reset_heartbeat()


def _local(state: str, *, changed: bool = True, error_class: str | None = None) -> dict:
    return {
        "local": {
            "state": state,
            "running": state == "ACTIVE",
            "camera_label": "Integrated Camera" if state == "ACTIVE" else None,
            "error_class": error_class,
            "observed_at": "2026-09-06T12:00:00Z",
            "changed": changed,
        }
    }


def _call(client, sid: str, call_id: str, name: str, **arguments) -> dict:
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id, "name": name, "arguments": arguments},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded", body
    return body["result"]


def _receipts(runtime, subsystem: str) -> list:
    with runtime.session() as db:
        return ledger_service.query(
            db, subsystems=[subsystem], event_types=[EVENT_TYPE_ACTION_RECEIPT], limit=50
        )


def _eye_enabled(runtime) -> bool:
    with runtime.session() as db:
        return is_eye_enabled(db)


def _activity(runtime, sid: str) -> dict:
    with runtime.session() as db:
        return service.session_activity(db, db.get(RealtimeSessionRow, uuid.UUID(sid)))


# ------------------------------------------------------------------ manifest


def test_the_tools_are_in_the_manifest_and_not_long_running(wired) -> None:
    client, *_ = wired
    created = _create(client)
    by_name = {t["name"]: t for t in created["tools"]}
    for name in ("eye.enable", "eye.disable", "release.promote", "state.now"):
        assert name in by_name, name
        assert by_name[name]["long_running"] is False
        assert "preamble" not in by_name[name]
    assert set(by_name["eye.enable"]["parameters"]["properties"]) == {
        "utterance",
        "observed_after",
    }
    for name in ("state.now", "eye.enable", "eye.disable", "release.promote"):
        assert name in created["instructions"]


# ------------------------------------------------------------------ disable


def test_disable_verified_is_the_read_back_not_the_intent(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    assert _eye_enabled(runtime) is True

    result = _call(
        client,
        sid,
        "c1",
        "eye.disable",
        utterance="Gözünü kapat.",
        observed_after=_local("DISABLED"),
    )
    assert result["capability"] == "eye.disable"
    assert result["requested_state"] == "disabled"
    assert result["terminal_status"] == "verified"
    assert result["execution_status"] == "executed"
    assert result["error_class"] is None
    assert result["speech"] == "Gözümü kapattım efendim."
    assert result["observed_after"]["server"]["eye_enabled"] is False
    assert result["observed_after"]["server"]["was_enabled"] is True
    assert result["observed_after"]["local"]["state"] == "DISABLED"
    assert result["action_id"] == "c1"
    kinds = {r["kind"] for r in result["evidence_refs"]}
    assert kinds == {"ledger_event", "realtime_session"}
    assert _eye_enabled(runtime) is False

    # the ledger: the eye.disabled row (reason = the voice command) and the receipt
    with runtime.session() as db:
        eye_rows = ledger_service.query(
            db, subsystems=[SUBSYSTEM_PRESENCE], event_types=[EVENT_TYPE_EYE_DISABLED]
        )
        assert len(eye_rows) == 1
        assert eye_rows[0].detail_json["reason"] == "voice:gözünü kapat"
    receipts = _receipts(runtime, SUBSYSTEM_PRESENCE)
    assert len(receipts) == 1
    assert receipts[0].action == "eye.disable"
    assert receipts[0].status == "verified"
    assert receipts[0].detail_json["execution_status"] == "executed"
    assert "speech" not in receipts[0].detail_json

    # session_activity exposes the receipt fields
    call = _activity(runtime, sid)["tool_calls"][0]
    assert call["name"] == "eye.disable"
    assert call["capability"] == "eye.disable"
    assert call["terminal_status"] == "verified"
    assert call["execution_status"] == "executed"
    assert call["error_class"] is None
    assert call["speech_head"] == "Gözümü kapattım efendim."


def test_disable_already_when_nothing_changed_anywhere(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        disable_eye(db, reason="owner earlier")
    # ... a while ago: no safety-net note in this session's context.
    result = _call(
        client,
        sid,
        "c2",
        "eye.disable",
        utterance="Kamerayı kapat.",
        observed_after=_local("DISABLED", changed=False),
    )
    assert result["terminal_status"] == "already"
    assert result["execution_status"] == "noop"
    assert result["speech"] == "Gözüm zaten kapalı efendim."
    assert result["observed_after"]["server"]["changed"] is False
    # idempotent durable write: still exactly one eye.disabled row
    with runtime.session() as db:
        rows = ledger_service.query(db, event_types=[EVENT_TYPE_EYE_DISABLED])
        assert len(rows) == 1
    assert _receipts(runtime, SUBSYSTEM_PRESENCE)[0].status == "already"


def test_disable_unverified_when_the_client_says_the_camera_still_runs(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    result = _call(
        client, sid, "c3", "eye.disable", utterance="Gözünü kapat.", observed_after=_local("ACTIVE")
    )
    assert result["terminal_status"] == "unverified"
    assert result["execution_status"] == "executed"  # the durable flag DID change
    assert result["error_class"] == "state_mismatch"
    assert result["speech"] == "Kamerayı kapatamadım; işlem doğrulanmadı."
    assert _eye_enabled(runtime) is False  # privacy: the server side is off regardless
    receipt = _receipts(runtime, SUBSYSTEM_PRESENCE)[0]
    assert receipt.status == "unverified" and receipt.severity == "warning"


def test_disable_without_observed_after_is_failed_capability_missing(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    result = _call(client, sid, "c4", "eye.disable", utterance="Beni izleme.")
    assert result["terminal_status"] == "failed"
    assert result["error_class"] == "capability_missing"
    assert result["observed_after"]["local"]["state"] == "ERROR"
    assert result["speech"] == "Kamerayı kapatamadım; işlem doğrulanmadı."
    assert _eye_enabled(runtime) is False


def test_the_safety_net_in_the_same_turn_makes_the_tool_call_verified(wired) -> None:
    """Contract §5.3: record_client_events disabled the eye deterministically on the
    utterance; the model then calls eye.disable for the same command. That call IS the
    command that closed the eye - "verified", not "already"."""
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 100, "turn": 3, "text": "gözünü kapat"}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["resolved_intents"][0]["intent"] == "eye_disable"
    assert r.json()["resolved_intents"][0]["klass"] == "action"
    assert r.json()["resolved_intents"][0]["capability"] == "eye.disable"
    assert _eye_enabled(runtime) is False
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        note = row.context_json["eye_safety"]
        assert note["action"] == "disable" and note["turn"] == 3 and note["changed"] is True
        assert note["applied_at"].endswith("Z")

    result = _call(
        client,
        sid,
        "c5",
        "eye.disable",
        utterance="Gözünü kapat.",
        observed_after=_local("DISABLED", changed=False),
    )
    assert result["terminal_status"] == "verified"
    assert result["execution_status"] == "executed"
    assert result["observed_after"]["server"]["safety_net"] is True
    assert result["observed_after"]["server"]["changed"] is False
    assert result["speech"] == "Gözümü kapattım efendim."

    # the intent record says which class of thing was asked
    intents = _activity(runtime, sid)["intents"]
    assert intents[0]["klass"] == "action" and intents[0]["capability"] == "eye.disable"
    assert intents[0]["eye_disable"] == "applied"


def test_a_repeated_safety_net_that_changed_nothing_does_not_fake_verified(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    for turn in (1, 2):
        r = client.post(
            f"/v1/voice/realtime/sessions/{sid}/events",
            json={
                "events": [
                    {"kind": "utterance", "t_ms": 100, "turn": turn, "text": "kamerayı kapat"}
                ]
            },
        )
        assert r.status_code == 200
    with runtime.session() as db:
        note = db.get(RealtimeSessionRow, uuid.UUID(sid)).context_json["eye_safety"]
        assert note["turn"] == 2 and note["changed"] is False
    result = _call(
        client,
        sid,
        "c6",
        "eye.disable",
        utterance="Kamerayı kapat.",
        observed_after=_local("DISABLED", changed=False),
    )
    assert result["terminal_status"] == "already"
    assert result["speech"] == "Gözüm zaten kapalı efendim."


# ------------------------------------------------------------------ enable


def test_enable_verified_only_after_the_camera_actually_opened(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        disable_eye(db, reason="owner earlier")
    result = _call(
        client, sid, "e1", "eye.enable", utterance="Gözünü aç.", observed_after=_local("ACTIVE")
    )
    assert result["capability"] == "eye.enable"
    assert result["requested_state"] == "active"
    assert result["terminal_status"] == "verified"
    assert result["execution_status"] == "executed"
    assert result["speech"] == "Gözümü açtım efendim."
    assert result["observed_after"]["server"] == {
        "eye_enabled": True,
        "was_enabled": False,
        "changed": True,
        "safety_net": False,
    }
    assert result["observed_after"]["local"]["camera_label"] == "Integrated Camera"
    assert _eye_enabled(runtime) is True
    receipt = _receipts(runtime, SUBSYSTEM_PRESENCE)[0]
    assert receipt.action == "eye.enable" and receipt.status == "verified"
    call = _activity(runtime, sid)["tool_calls"][0]
    assert (call["capability"], call["terminal_status"]) == ("eye.enable", "verified")


def test_enable_permission_denied_never_sets_the_flag(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        disable_eye(db, reason="owner earlier")
    result = _call(
        client,
        sid,
        "e2",
        "eye.enable",
        utterance="Kamerayı aç.",
        observed_after=_local("ERROR", changed=False, error_class="permission_denied"),
    )
    assert result["terminal_status"] == "failed"
    assert result["execution_status"] == "failed"
    assert result["error_class"] == "permission_denied"
    assert result["speech"] == "Kamerayı açamadım; tarayıcı kamera izni vermedi."
    assert _eye_enabled(runtime) is False
    call = _activity(runtime, sid)["tool_calls"][0]
    assert call["status"] == "succeeded"  # the TOOL succeeded; the ACTION failed
    assert call["error_class"] == "permission_denied"
    assert call["terminal_status"] == "failed"


def test_enable_device_unavailable_speech(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        disable_eye(db, reason="owner earlier")
    result = _call(
        client,
        sid,
        "e3",
        "eye.enable",
        utterance="Beni tekrar izle.",
        observed_after=_local("ERROR", changed=False, error_class="device_unavailable"),
    )
    assert result["speech"] == "Kamerayı açamadım; kamera bulunamadı ya da meşgul."
    assert _eye_enabled(runtime) is False


def test_enable_without_observed_after_never_sets_the_flag(wired) -> None:
    """A non-web client relays without observed_after: capability_missing, and the
    Cloud Core is never told perception is on for a camera nobody opened."""
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        disable_eye(db, reason="owner earlier")
    result = _call(client, sid, "e4", "eye.enable", utterance="Gözünü aç.")
    assert result["terminal_status"] == "failed"
    assert result["execution_status"] == "failed"
    assert result["error_class"] == "capability_missing"
    assert result["speech"] == "Kamerayı açamadım; işlem doğrulanmadı."
    assert _eye_enabled(runtime) is False
    assert _receipts(runtime, SUBSYSTEM_PRESENCE)[0].status == "failed"


def test_enable_already_when_the_eye_was_on_and_the_camera_was_open(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    assert _eye_enabled(runtime) is True
    result = _call(
        client,
        sid,
        "e5",
        "eye.enable",
        utterance="Gözünü tekrar aç.",
        observed_after=_local("ACTIVE", changed=False),
    )
    assert result["terminal_status"] == "already"
    assert result["execution_status"] == "noop"
    assert result["speech"] == "Gözüm zaten açık efendim."


def test_enable_is_verified_when_the_camera_opened_even_if_the_flag_was_on(wired) -> None:
    """The durable default is "enabled" and the browser had no camera open; the owner
    says "kamerayı aç", the camera opens (local changed=true). That is a change in this
    command: "Gözümü açtım", not "zaten açık"."""
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    result = _call(
        client, sid, "e6", "eye.enable", utterance="Kamerayı aç.", observed_after=_local("ACTIVE")
    )
    assert result["terminal_status"] == "verified"
    assert result["observed_after"]["server"]["changed"] is False
    assert result["observed_after"]["local"]["changed"] is True
    assert result["speech"] == "Gözümü açtım efendim."


def test_disable_then_enable_round_trip_leaves_two_receipts(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    _call(
        client,
        sid,
        "r1",
        "eye.disable",
        utterance="Gözünü kapat.",
        observed_after=_local("DISABLED"),
    )
    _call(client, sid, "r2", "eye.enable", utterance="Gözünü aç.", observed_after=_local("ACTIVE"))
    assert _eye_enabled(runtime) is True
    receipts = _receipts(runtime, SUBSYSTEM_PRESENCE)
    assert [(r.action, r.status) for r in receipts] == [
        ("eye.enable", "verified"),
        ("eye.disable", "verified"),
    ]  # newest first
    calls = _activity(runtime, sid)["tool_calls"]
    assert [c["capability"] for c in calls] == ["eye.disable", "eye.enable"]


# ------------------------------------------------------------------ release


def test_release_promote_by_voice_is_always_refused_and_recorded(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    result = _call(client, sid, "p1", "release.promote", utterance="Bunu canlıya al.")
    assert result["capability"] == "release.promote"
    assert result["execution_status"] == "refused"
    assert result["terminal_status"] == "failed"
    assert result["error_class"] == "owner_authorization_required"
    assert result["speech"] == (
        "Canlıya alma kararı sizin efendim; onayı Core'daki Onay Merkezi'nden verirsiniz. "
        "Ben kendi başıma canlıya almam."
    )
    assert result["observed_after"]["server"]["promoted"] is False
    receipts = _receipts(runtime, SUBSYSTEM_DEPLOYMENT)
    assert len(receipts) == 1
    assert receipts[0].action == "release.promote"
    assert receipts[0].status == "failed" and receipts[0].severity == "notice"
    assert receipts[0].detail_json["execution_status"] == "refused"
    call = _activity(runtime, sid)["tool_calls"][0]
    assert call["capability"] == "release.promote"
    assert call["execution_status"] == "refused"
    assert call["error_class"] == "owner_authorization_required"


def test_tool_calls_validate_the_utterance(wired) -> None:
    client, *_ = wired
    sid = _create(client)["session_id"]
    for name in ("eye.enable", "eye.disable", "release.promote"):
        r = client.post(
            f"/v1/voice/realtime/sessions/{sid}/tool-calls",
            json={"call_id": f"v-{name}", "name": name, "arguments": {}},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "failed"
        assert r.json()["error"]["error_class"] == "validation_error"
