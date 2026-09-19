"""The eye actions and the refused release, over the real tool relay
(docs/M18_ACTION_CONTRACT.md §5, §2; tests §8).

The owner's defects (2026-09-06): "Gözünü kapat." really disabled the eye and the model
said "öyle olmuş gibi düşün"; "Gözünü aç" did nothing; and in session 3eb6fee7 a hidden
utterance hook closed the camera 7 ms after the tool call with no receipt while every
receipt said failed, and a camera the browser HAD closed was narrated as "kapatamadım".
Every case below runs through ``handle_tool_call`` on SQLite with the real
``app.presence.eye`` module, and asserts the receipt, its exact speech, the
``action.receipt`` ledger row and what ``session_activity`` exposes - never a transcript.
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


def _local(
    state: str,
    *,
    changed: bool = True,
    error_class: str | None = None,
    track: str | None = None,
    trace: list[str] | None = None,
) -> dict:
    local = {
        "state": state,
        "running": state == "ACTIVE",
        "camera_label": "Integrated Camera" if state == "ACTIVE" else None,
        "error_class": error_class,
        "observed_at": "2026-09-06T12:00:00Z",
        "changed": changed,
    }
    if track is not None:
        local["media_track_ready_state"] = track
    if trace is not None:
        local["action_trace"] = trace
    return {"local": local}


def _call(client, sid: str, call_id: str, name: str, **arguments) -> dict:
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id, "name": name, "arguments": arguments},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded", body
    return body["result"]


def _utter(client, sid: str, text: str, *, turn: int = 1) -> dict:
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 100, "turn": turn, "text": text}]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _receipts(runtime, subsystem: str) -> list:
    with runtime.session() as db:
        return ledger_service.query(
            db, subsystems=[subsystem], event_types=[EVENT_TYPE_ACTION_RECEIPT], limit=50
        )


def _eye_enabled(runtime) -> bool:
    with runtime.session() as db:
        return is_eye_enabled(db)


def _eye_disabled_rows(runtime) -> list:
    with runtime.session() as db:
        return ledger_service.query(
            db, subsystems=[SUBSYSTEM_PRESENCE], event_types=[EVENT_TYPE_EYE_DISABLED]
        )


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


# ------------------------------------------------------------------ one mutation path


def test_an_utterance_never_mutates_the_eye_only_the_tool_does(wired) -> None:
    """Contract §5.3: the utterance is resolved and audited - intent, klass, capability -
    and the eye is exactly as it was. Session 3eb6fee7 had a second, receipt-less path
    here; this pins that it is gone."""
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    assert _eye_enabled(runtime) is True

    body = _utter(client, sid, "gözünü kapat", turn=3)
    resolved = body["resolved_intents"][0]
    assert resolved["intent"] == "eye_disable"
    assert resolved["klass"] == "action"
    assert resolved["capability"] == "eye.disable"

    # still enabled: no durable write, no ledger row, no bookkeeping in the context
    assert _eye_enabled(runtime) is True
    assert _eye_disabled_rows(runtime) == []
    assert _receipts(runtime, SUBSYSTEM_PRESENCE) == []
    with runtime.session() as db:
        ctx = db.get(RealtimeSessionRow, uuid.UUID(sid)).context_json
        assert "eye_safety" not in ctx
        assert ctx["last_intent"] == "eye_disable"

    # the audit says what CLASS of thing was asked, and nothing about a mutation
    intents = _activity(runtime, sid)["intents"]
    assert intents[0]["intent"] == "eye_disable"
    assert intents[0]["klass"] == "action" and intents[0]["capability"] == "eye.disable"
    assert "eye_disable" not in intents[0]

    # every disable phrasing, and the enable phrasings, behave the same
    for phrase in ("kamerayı kapat", "beni izleme"):
        assert _utter(client, sid, phrase)["resolved_intents"][0]["intent"] == "eye_disable"
    assert _eye_enabled(runtime) is True
    with runtime.session() as db:
        disable_eye(db, reason="owner earlier")
    for phrase in ("gözünü aç", "kamerayı aç", "beni izle"):
        assert _utter(client, sid, phrase)["resolved_intents"][0]["intent"] == "eye_enable"
    assert _eye_enabled(runtime) is False

    # ... and then the tool, for the same command, is the ONE path: verified.
    with runtime.session() as db:
        from app.presence.eye import enable_eye

        enable_eye(db, reason="owner earlier")
    rows_before = len(_eye_disabled_rows(runtime))
    result = _call(
        client,
        sid,
        "c0",
        "eye.disable",
        utterance="Gözünü kapat.",
        observed_after=_local("DISABLED"),
    )
    assert result["terminal_status"] == "verified"
    assert result["speech"] == "Gözümü kapattım efendim."
    assert _eye_enabled(runtime) is False
    assert len(_eye_disabled_rows(runtime)) == rows_before + 1  # the tool's write, exactly one


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
    assert result["observed_after"]["server"]["write_error"] is None
    assert result["observed_after"]["local"]["state"] == "DISABLED"
    assert result["observed_after"]["local"]["media_track_ready_state"] is None
    assert result["action_id"] == "c1"
    assert result["session_id"] == sid
    assert result["observed_at"].endswith("Z")
    assert result["action_trace"] == []
    kinds = {r["kind"] for r in result["evidence_refs"]}
    assert kinds == {"ledger_event", "realtime_session"}
    assert _eye_enabled(runtime) is False

    # the ledger: the eye.disabled row (reason = the voice command) and the receipt
    eye_rows = _eye_disabled_rows(runtime)
    assert len(eye_rows) == 1
    assert eye_rows[0].detail_json["reason"] == "voice:gözünü kapat"
    receipts = _receipts(runtime, SUBSYSTEM_PRESENCE)
    assert len(receipts) == 1
    assert receipts[0].action == "eye.disable"
    assert receipts[0].status == "verified"
    assert receipts[0].detail_json["execution_status"] == "executed"
    assert receipts[0].detail_json["session_id"] == sid  # correlate by session
    assert receipts[0].detail_json["observed_at"] == result["observed_at"]
    assert "speech" not in receipts[0].detail_json

    # session_activity exposes the receipt fields
    call = _activity(runtime, sid)["tool_calls"][0]
    assert call["name"] == "eye.disable"
    assert call["session_id"] == sid
    assert call["capability"] == "eye.disable"
    assert call["terminal_status"] == "verified"
    assert call["execution_status"] == "executed"
    assert call["error_class"] is None
    assert call["speech_head"] == "Gözümü kapattım efendim."
    assert call["observed_after"]["local"]["state"] == "DISABLED"
    assert call["observed_after"]["server"]["eye_enabled"] is False
    assert call["observed_at"] == result["observed_at"]


def test_disable_verified_with_the_track_ended_and_the_server_read_back_false(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    result = _call(
        client,
        sid,
        "c1b",
        "eye.disable",
        utterance="Gözünü kapat.",
        observed_after=_local("DISABLED", track="ended", trace=["disable", "loop_stopped"]),
    )
    assert result["terminal_status"] == "verified"
    assert result["speech"] == "Gözümü kapattım efendim."
    assert result["observed_after"]["server"]["eye_enabled"] is False
    assert result["observed_after"]["local"]["media_track_ready_state"] == "ended"
    assert result["action_trace"] == ["disable", "loop_stopped"]
    call = _activity(runtime, sid)["tool_calls"][0]
    assert call["observed_after"]["local"]["media_track_ready_state"] == "ended"
    assert call["action_trace"] == ["disable", "loop_stopped"]


def test_disable_already_when_nothing_changed_anywhere(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        disable_eye(db, reason="owner earlier")
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
    assert len(_eye_disabled_rows(runtime)) == 1
    assert _receipts(runtime, SUBSYSTEM_PRESENCE)[0].status == "already"


def test_disable_is_verified_when_the_client_did_the_durable_write_first(wired) -> None:
    """The web EyeStore POSTs eye/disable itself and then relays changed=true; the
    handler's own write is an idempotent no-op. Still THIS command closed it."""
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        disable_eye(db, reason="voice:gözünü kapat")  # the client's own durable call
    result = _call(
        client,
        sid,
        "c2b",
        "eye.disable",
        utterance="Gözünü kapat.",
        observed_after=_local("DISABLED", changed=True),
    )
    assert result["terminal_status"] == "verified"
    assert result["observed_after"]["server"]["changed"] is False
    assert result["speech"] == "Gözümü kapattım efendim."


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


def test_disable_where_the_camera_closed_but_the_durable_write_raised_is_truthful(
    wired, monkeypatch
) -> None:
    """The browser says DISABLED; the Cloud Core's write blew up. The camera IS off, so
    "kapatamadım" would be false; the record is unverified, so "kapattım" would be
    ungrounded. The sentence says exactly what is known."""
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]

    def exploding_disable(db, **kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr("app.presence.eye.disable_eye", exploding_disable)
    result = _call(
        client,
        sid,
        "c4",
        "eye.disable",
        utterance="Gözünü kapat.",
        observed_after=_local("DISABLED", track="ended"),
    )
    assert result["terminal_status"] == "unverified"
    assert result["execution_status"] == "failed"
    assert result["error_class"] == "durable_write_failed"
    assert result["speech"] == "Kamera kapandı ancak işlem kaydını doğrulayamadım."
    assert result["observed_after"]["server"]["write_error"] == "RuntimeError"
    assert result["observed_after"]["server"]["eye_enabled"] is True  # honest read-back
    assert result["observed_after"]["local"]["state"] == "DISABLED"
    receipt = _receipts(runtime, SUBSYSTEM_PRESENCE)[0]
    assert receipt.status == "unverified" and receipt.detail_json["session_id"] == sid
    # the tool call row survived the handler's failure (replay stays idempotent)
    call = _activity(runtime, sid)["tool_calls"][0]
    assert call["status"] == "succeeded" and call["terminal_status"] == "unverified"


def test_disable_without_observed_after_is_failed_capability_missing(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    result = _call(client, sid, "c5", "eye.disable", utterance="Beni izleme.")
    assert result["terminal_status"] == "failed"
    assert result["execution_status"] == "executed"  # the durable flag was written
    assert result["error_class"] == "capability_missing"
    assert result["observed_after"]["local"]["state"] == "ERROR"
    assert result["speech"] == "Kamerayı kapatamadım; işlem doğrulanmadı."
    assert _eye_enabled(runtime) is False


# ------------------------------------------------------------------ enable


def test_enable_verified_only_after_the_camera_actually_opened(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        disable_eye(db, reason="owner earlier")
    result = _call(
        client,
        sid,
        "e1",
        "eye.enable",
        utterance="Gözünü aç.",
        observed_after=_local("ACTIVE", track="live"),
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
        "write_error": None,
    }
    assert result["observed_after"]["local"]["camera_label"] == "Integrated Camera"
    assert result["observed_after"]["local"]["media_track_ready_state"] == "live"
    assert _eye_enabled(runtime) is True
    receipt = _receipts(runtime, SUBSYSTEM_PRESENCE)[0]
    assert receipt.action == "eye.enable" and receipt.status == "verified"
    call = _activity(runtime, sid)["tool_calls"][0]
    assert (call["capability"], call["terminal_status"]) == ("eye.enable", "verified")
    assert call["observed_after"]["local"]["media_track_ready_state"] == "live"


def test_enable_with_the_track_ended_is_failed_and_never_sets_the_flag(wired) -> None:
    """ACTIVE by the store's account, but the media track is already ended: no camera is
    open, whatever the state machine says. The Cloud Core is not told perception is on."""
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
        observed_after=_local("ACTIVE", track="ended"),
    )
    assert result["terminal_status"] == "failed"
    assert result["execution_status"] == "failed"
    assert result["error_class"] == "stream_created_but_track_ended"
    assert result["speech"] == "Kamera açıldı ama görüntü akışı hemen kesildi."
    assert _eye_enabled(runtime) is False
    assert _receipts(runtime, SUBSYSTEM_PRESENCE)[0].status == "failed"


def test_enable_where_the_camera_opened_but_the_durable_write_raised_is_truthful(
    wired, monkeypatch
) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        disable_eye(db, reason="owner earlier")

    def exploding_enable(db, **kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr("app.presence.eye.enable_eye", exploding_enable)
    result = _call(
        client,
        sid,
        "e3",
        "eye.enable",
        utterance="Gözünü aç.",
        observed_after=_local("ACTIVE", track="live"),
    )
    assert result["terminal_status"] == "unverified"
    assert result["error_class"] == "durable_write_failed"
    assert result["speech"] == "Kamera açıldı ancak işlem kaydını doğrulayamadım."
    assert _eye_enabled(runtime) is False


@pytest.mark.parametrize(
    ("error_class", "expected"),
    [
        ("permission_denied", "Kamerayı açamadım; tarayıcı kamera izni vermedi."),
        ("device_not_found", "Kamerayı açamadım; kamera bulunamadı."),
        (
            "device_busy",
            "Kamerayı açamadım; kamera başka bir uygulama tarafından kullanılıyor.",
        ),
        ("device_unavailable", "Kamerayı açamadım; kamera bulunamadı ya da meşgul."),
        ("get_user_media_failed", "Kamerayı açamadım; tarayıcı kamera akışını başlatamadı."),
        ("perception_start_failed", "Kamera açıldı ama algılama döngüsü başlatılamadı."),
        ("state_transition_failed", "Kamerayı açamadım; durum geçişi tamamlanamadı."),
        ("timeout", "Kamerayı açamadım; işlem doğrulanmadı."),
    ],
)
def test_enable_failures_are_named_by_the_clients_error_class(wired, error_class, expected):
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    with runtime.session() as db:
        disable_eye(db, reason="owner earlier")
    result = _call(
        client,
        sid,
        f"e-{error_class}",
        "eye.enable",
        utterance="Kamerayı aç.",
        observed_after=_local("ERROR", changed=False, error_class=error_class),
    )
    assert result["terminal_status"] == "failed"
    assert result["execution_status"] == "failed"
    assert result["error_class"] == error_class
    assert result["speech"] == expected
    assert _eye_enabled(runtime) is False
    call = _activity(runtime, sid)["tool_calls"][0]
    assert call["status"] == "succeeded"  # the TOOL succeeded; the ACTION failed
    assert call["error_class"] == error_class
    assert call["terminal_status"] == "failed"


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

    # BOTH receipts, each verified. Asserted as a set rather than as a list, because the
    # ledger orders by `occurred_at` and two receipts written inside the same clock tick
    # share it - there is no order between them for the query to return, and demanding one
    # asserts something this store does not promise.
    #
    # Found 2026-09-13 (B14): this passed for months only because `test_voice_eye_tools`
    # sorts before `test_voice_step_up` alphabetically, so it always ran cold and the two
    # calls landed ~16 ms apart. Running the two files in the other order makes them land
    # together and the list flips. A test whose green depends on filename ordering is a
    # test that will fail in CI on the day somebody adds a file.
    assert {(r.action, r.status) for r in receipts} == {
        ("eye.enable", "verified"),
        ("eye.disable", "verified"),
    }
    # And the claim that survives a tie: the later act was not recorded as the earlier one.
    by_action = {r.action: r.occurred_at for r in receipts}
    assert by_action["eye.enable"] >= by_action["eye.disable"]
    assert {r.detail_json["session_id"] for r in receipts} == {sid}
    calls = _activity(runtime, sid)["tool_calls"]
    assert [c["capability"] for c in calls] == ["eye.disable", "eye.enable"]
    assert {c["session_id"] for c in calls} == {sid}


# ------------------------------------------------------------------ client evidence


def test_media_track_and_action_trace_are_optional_and_bounded(wired) -> None:
    client, _, runtime, *_ = wired
    sid = _create(client)["session_id"]
    # unknown track value -> None; 20 steps -> 12; a long step -> 80 chars; non-strings dropped
    trace = [f"step-{n}" for n in range(19)] + ["x" * 200] + [7, None]
    local = _local("DISABLED", track="weird", trace=trace)["local"]
    result = _call(
        client,
        sid,
        "t1",
        "eye.disable",
        utterance="Gözünü kapat.",
        observed_after={"local": local},
    )
    assert result["terminal_status"] == "verified"
    assert result["observed_after"]["local"]["media_track_ready_state"] is None
    assert len(result["action_trace"]) == 12
    assert result["action_trace"][:2] == ["step-0", "step-1"]
    assert all(len(step) <= 80 for step in result["action_trace"])
    receipt = _receipts(runtime, SUBSYSTEM_PRESENCE)[0]
    assert len(receipt.detail_json["action_trace"]) == 12
    call = _activity(runtime, sid)["tool_calls"][0]
    assert len(call["action_trace"]) == 12
    # a trace that is not a list is simply empty
    result = _call(
        client,
        sid,
        "t2",
        "eye.enable",
        utterance="Gözünü aç.",
        observed_after={"local": {**_local("ACTIVE")["local"], "action_trace": "not-a-list"}},
    )
    assert result["action_trace"] == []


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
    assert result["session_id"] == sid
    receipts = _receipts(runtime, SUBSYSTEM_DEPLOYMENT)
    assert len(receipts) == 1
    assert receipts[0].action == "release.promote"
    assert receipts[0].status == "failed" and receipts[0].severity == "notice"
    assert receipts[0].detail_json["execution_status"] == "refused"
    assert receipts[0].detail_json["session_id"] == sid
    call = _activity(runtime, sid)["tool_calls"][0]
    assert call["capability"] == "release.promote"
    assert call["execution_status"] == "refused"
    assert call["error_class"] == "owner_authorization_required"
    assert call["session_id"] == sid


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


# ------------------------------------------- ADR-0173: the local mode has no model


def _local_call(client, sid: str, call_id: str, name: str, **arguments) -> dict:
    """What the LOCAL mode posts: the tool the router named, no ``utterance`` argument."""
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id, "name": name, "arguments": arguments},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_the_local_mode_opens_the_camera_without_a_model_to_write_the_utterance(
    wired,
) -> None:
    """Owner, 2026-09-20: "kamerayı sesli açma kapama yerel modda kapalı". In the local mode
    (ADR-0173) the router names the tool and the browser posts it with no ``utterance``
    argument - no model writes one - and the call was refused on that missing argument, so
    the camera never opened. The owner's own sentence is on the turn record; it is read
    from there, exactly like every other local-mode tool."""
    client, runtime, *_ = wired
    sid = _create(client)["session_id"]
    _utter(client, sid, "Kamerayı aç.")

    body = _local_call(client, sid, "c-local-eye-1", "eye.enable", observed_after=_local("ACTIVE"))

    assert body["status"] == "succeeded", body
    assert _eye_enabled(runtime) is True
    receipt = body["result"]
    assert receipt["terminal_status"] == "verified", receipt
    assert receipt["observed_after"]["server"]["eye_enabled"] is True


def test_the_local_mode_closes_the_camera_the_same_way(wired) -> None:
    client, runtime, *_ = wired
    sid = _create(client)["session_id"]
    _utter(client, sid, "Kamerayı aç.")
    _local_call(client, sid, "c-local-eye-2", "eye.enable", observed_after=_local("ACTIVE"))
    _utter(client, sid, "Kamerayı kapat.", turn=2)

    body = _local_call(
        client, sid, "c-local-eye-3", "eye.disable", observed_after=_local("DISABLED")
    )

    assert body["status"] == "succeeded", body
    assert _eye_enabled(runtime) is False


def test_a_camera_call_the_owner_never_asked_for_is_still_refused(wired) -> None:
    """The fallback reads the TURN, not the tool's name: a call arriving with no utterance
    argument and no camera sentence behind it has no owner's words to stand on."""
    client, runtime, *_ = wired
    sid = _create(client)["session_id"]
    # The eye starts enabled by default, so the claim is made against a camera that is OFF:
    # a refused call must not be able to turn it back on.
    _utter(client, sid, "Kamerayı kapat.")
    _local_call(client, sid, "c-local-eye-5", "eye.disable", observed_after=_local("DISABLED"))
    assert _eye_enabled(runtime) is False
    _utter(client, sid, "Saat kaç?", turn=2)

    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={
            "call_id": "c-local-eye-4",
            "name": "eye.enable",
            "arguments": {"observed_after": _local("ACTIVE")},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed", r.json()
    assert _eye_enabled(runtime) is False
