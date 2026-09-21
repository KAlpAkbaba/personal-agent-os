"""el hareketiyle kumanda, Stage 1 (ADR-0198): a DISCRETE gesture from the browser-side
hand tracker turns into the SAME tool calls a spoken utterance already produces, through
the REAL relay (TestClient, no mocking of app.voice.gestures.resolve_gesture) - the same
``_wired()``/``_create``/``_tool`` path ``tests/unit/test_operator_tools.py`` uses, since
a gesture rides ``ctx["last_utterance"]`` exactly the way an utterance does and the tools
(``operator.key``, ``media.volume``) are gesture-agnostic.

Covers: each swipe direction -> operator.key with the right arrow key; rotate_cw/ccw ->
media.volume up/down; spread -> operator.key "f" (fullscreen); pinch_start/pinch_release
accepted and recorded but naming no tool (Stage 2 wires those); an unknown gesture name
refused 422 at the route, before app.voice.gestures ever sees it; and the ~5/s/session
rate bound (server wall-clock, never the client's own t_ms).
"""

from __future__ import annotations

import uuid

import pytest

from app.media.models import PLAYBACK_STATUS_PLAYING, OwnerMediaPlaybackRow
from app.media.playback_service import SESSION_PREFIX
from app.voice.realtime_sessions.models import RealtimeSessionRow
from tests.alarms_support import window_id as window_id_for
from tests.unit.test_operator_tools import _create, _focus_window, _tool, _wired


def _gesture(client, sid: str, gesture: str, *, turn: int = 1, t_ms: int = 1000) -> dict:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "gesture", "t_ms": t_ms, "turn": turn, "gesture": gesture}]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _context(factory, sid: str) -> dict:
    with factory() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        return dict(row.context_json or {})


def _seed_live_playback(factory) -> None:
    """The B27 recipe (``tests/voice_corpus/harness.py``'s ``CTX_MEDIA_PLAYING``): a
    playback THIS service opened and still live - the row ``live_playback`` reads before
    ``media.volume`` can act on anything at all. ``_wired()``'s own TABLES does not
    include this model (only the operator-family fixtures do), so it is created here,
    once, on the SAME engine/session-factory ``_wired()`` already built."""
    with factory() as db:
        engine = db.get_bind()
    OwnerMediaPlaybackRow.__table__.create(engine, checkfirst=True)
    with factory() as db:
        playback = OwnerMediaPlaybackRow(
            id=uuid.uuid4(),
            request_text="",
            query="",
            video_id="dQw4w9WgXcQ",
            video_title="Test video",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            session_id="",
            status=PLAYBACK_STATUS_PLAYING,
            receipt_json={"playing": True, "verified": True},
        )
        playback.session_id = f"{SESSION_PREFIX}{playback.id}"
        db.add(playback)
        db.commit()


# ------------------------------------------------------------------------- swipes


def test_swipe_left_presses_the_left_arrow_key_and_the_events_response_says_so() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)

    events = _gesture(client, sid, "swipe_left")
    assert events["accepted"] == 1
    entry = events["resolved_intents"][0]
    assert entry["gesture"] == "swipe_left"
    assert entry["tool"] == "operator.key"
    assert entry["intent"] == "operator_key"

    call = _tool(client, sid, "operator.key", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["execution_status"] == "executed"
    assert body["terminal_status"] == "verified"
    assert device.capabilities_called() == ["window.list", "window.activate", "keyboard.key"]
    assert all(
        c["payload"] == {"window_id": window_id_for(1), "key": "left"}
        for c in device.calls
        if c["capability"] == "keyboard.key"
    )


def test_swipe_up_presses_the_up_arrow_key() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)

    _gesture(client, sid, "swipe_up")
    call = _tool(client, sid, "operator.key", {})
    assert call["status"] == "succeeded", call
    assert all(
        c["payload"] == {"window_id": window_id_for(1), "key": "up"}
        for c in device.calls
        if c["capability"] == "keyboard.key"
    )


# --------------------------------------------------------------------------- spread


def test_spread_presses_f_for_fullscreen() -> None:
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)

    events = _gesture(client, sid, "spread")
    assert events["resolved_intents"][0]["tool"] == "operator.key"

    call = _tool(client, sid, "operator.key", {})
    assert call["status"] == "succeeded", call
    assert all(
        c["payload"] == {"window_id": window_id_for(1), "key": "f"}
        for c in device.calls
        if c["capability"] == "keyboard.key"
    )


# --------------------------------------------------------------------------- rotate


def test_rotate_cw_raises_the_volume() -> None:
    client, factory, device, _operator = _wired()
    _seed_live_playback(factory)
    sid = _create(client)

    events = _gesture(client, sid, "rotate_cw")
    assert events["resolved_intents"][0]["tool"] == "media.volume"

    call = _tool(client, sid, "media.volume", {"direction": "down"})
    assert call["status"] == "succeeded", call
    assert call["result"]["requested_state"] == "up", "the gesture said up; the model said down"


def test_rotate_ccw_lowers_the_volume() -> None:
    client, factory, device, _operator = _wired()
    _seed_live_playback(factory)
    sid = _create(client)

    _gesture(client, sid, "rotate_ccw")
    call = _tool(client, sid, "media.volume", {"direction": "up"})
    assert call["status"] == "succeeded", call
    assert call["result"]["requested_state"] == "down", "the gesture said down; the model said up"


@pytest.mark.parametrize(("gesture", "key"), [("rotate_cw", "up"), ("rotate_ccw", "down")])
def test_a_rotate_with_no_owner_media_session_is_the_players_own_volume_key(
    gesture: str, key: str
) -> None:
    """First live trial (2026-09-21): the owner turned the cap at a video they had opened
    by hand and every rotate was refused "volume_failed" - media.volume only knows the
    session THIS service opened. With none live, a rotate is the focused player's own
    volume key (YouTube and most web players: the arrows), never a refusal."""
    from tests.unit.test_operator_tools import _focus_window

    client, factory, device, _operator = _wired()
    with factory() as db:
        OwnerMediaPlaybackRow.__table__.create(db.get_bind(), checkfirst=True)  # exists, empty
    _focus_window(factory, device=device)
    sid = _create(client)
    events = _gesture(client, sid, gesture)
    resolved = events["resolved_intents"][0]
    assert resolved["tool"] == "operator.key" and resolved["gesture"] == gesture
    call = _tool(client, sid, "operator.key", {})
    assert call["status"] == "succeeded", call
    assert call["result"]["execution_status"] == "executed"
    assert device.payload_for("keyboard.key")["key"] == key
    assert "media.volume" not in [c["capability"] for c in device.calls]


# ---------------------------------------------------------------------------- pinch


@pytest.mark.parametrize("gesture", ["pinch_start", "pinch_release"])
def test_pinch_is_accepted_and_recorded_but_names_no_tool(gesture: str) -> None:
    client, factory, device, _operator = _wired()
    sid = _create(client)

    events = _gesture(client, sid, gesture)
    assert events["accepted"] >= 1
    entry = events["resolved_intents"][0]
    assert entry["gesture"] == gesture
    assert entry["tool"] is None
    # There is no tool to call: Stage 2 (pinch-mouse) is what will give this meaning.
    assert device.capabilities_called() == []


# ------------------------------------------------------------------ unknown gesture


def test_an_unknown_gesture_name_is_refused_422_before_it_reaches_the_service() -> None:
    client, factory, device, _operator = _wired()
    sid = _create(client)

    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={
            "events": [{"kind": "gesture", "t_ms": 1000, "turn": 1, "gesture": "swipe_diagonally"}]
        },
    )
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------- the rate bound


def test_more_than_five_gestures_per_second_are_dropped_and_counted() -> None:
    """Spec: max ~5 gesture events/s/session - drop extras, count them. ``now`` (SERVER
    wall-clock) is computed ONCE per request in ``record_client_events``, so all 7
    events in this one batch share the same instant: the first 5 are resolved, the
    6th and 7th are dropped and counted in ``gesture_dropped_total`` on the row's own
    durable context, exactly like ``test_operator_repeat.py`` reads durable state the
    HTTP response does not carry."""
    client, factory, device, _operator = _wired()
    _focus_window(factory, device=device)
    sid = _create(client)

    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={
            "events": [
                {"kind": "gesture", "t_ms": 1000 + i, "turn": 1, "gesture": "swipe_left"}
                for i in range(7)
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["resolved_intents"]) == 5
    assert body["accepted"] == 7  # every event was AUDITED; only 5 were RESOLVED

    ctx = _context(factory, sid)
    assert ctx.get("gesture_dropped_total") == 2

    # The resolver only ran 5 times: the accepted keys reached the device once each
    # after the tool call below, never more than 5.
    call = _tool(client, sid, "operator.key", {})
    assert call["status"] == "succeeded", call
    assert device.capabilities_called().count("keyboard.key") == 1
