"""stt-engine-on-turn-audit (ADR-0249 D4): the recogniser's name of a local-mode turn is kept.

The web client's local mode posts every utterance with ``payload: { stt_engine }`` - one of
the three names of ``SttEngine`` in ``apps/web/app/lib/voice/localMode.ts``. Until this card
the server accepted the key and dropped it. Pinned here, through the real application
object and the real relay (the ``wired`` fixture, a ``transport="text"`` session):

* the name lands on the turn's ``voice_intent_resolved`` audit row, the session's
  ``last_utterance`` turn record and the ``/activity`` intents - and nothing else changes:
  the response of the turn is the same with and without it (it decides nothing);
* anything that is not exactly one of the three names is stored as null;
* the two halves read each other: the client's type is the server's set.
"""

# ruff: noqa: F811 - the shared `wired` fixture is imported and then named as a parameter
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.voice import stt_engine
from app.voice.providers import TRANSPORT_TEXT
from app.voice.realtime_sessions import service
from tests.unit.test_voice_local_mode import _register_local
from tests.unit.test_voice_realtime_sessions import _audit_rows, _create, wired  # noqa: F401

WEB_LOCAL_MODE = (
    Path(__file__).resolve().parents[4] / "apps" / "web" / "app" / "lib" / "voice" / "localMode.ts"
)

#: A sentence the router understands (a query: no tool mutates anything on its own).
UNDERSTOOD = "Alarmlarla neler yapabilirsin?"

#: Not one of the three names, each for its own reason (seven cases, null every time).
UNKNOWN: dict[str, Any] = {
    "no_payload": ...,  # the key `payload` itself is absent
    "empty_payload": None,  # `payload: {}`
    "another_word": "google",
    "too_long": "chrome-bulut" + "x" * 53,  # 65 characters, starting with a real name
    "a_number": 3,
    "a_list": ["chrome-bulut"],
    "null": None,
}


def _local_session(client: Any, runtime: Any) -> str:
    _register_local(runtime)
    return _create(client, transport=TRANSPORT_TEXT)["session_id"]


def _post(client: Any, sid: str, text: str, *, turn: int, payload: Any = ...) -> Any:
    """The client's utterance event as ``localMode.ts`` posts it; ``...`` = no payload key."""
    event: dict[str, Any] = {"kind": "utterance", "t_ms": 1000 * turn, "turn": turn, "text": text}
    if payload is not ...:
        event["payload"] = payload
    response = client.post(f"/v1/voice/realtime/sessions/{sid}/events", json={"events": [event]})
    assert response.status_code == 200, response.text
    return response


def _say(client: Any, sid: str, text: str, *, turn: int, engine: Any = ...) -> dict[str, Any]:
    payload = ... if engine is ... else {"stt_engine": engine}
    return _post(client, sid, text, turn=turn, payload=payload).json()


def _resolved_rows(runtime: Any, sid: str) -> list[Any]:
    return _audit_rows(runtime, sid, service.ACTION_INTENT_RESOLVED)


def _turn_record(runtime: Any, sid: str) -> dict[str, Any]:
    """``last_utterance`` as the database holds it, read through a FRESH session."""
    with runtime.session() as db:
        row = service.get_session(db, uuid.UUID(sid))
        return dict((row.context_json or {})["last_utterance"])


# ------------------------------------------------------------ (8) normalise


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("chrome-cihaz-ici", "chrome-cihaz-ici"),
        ("chrome-bulut", "chrome-bulut"),
        ("bilinmiyor", "bilinmiyor"),
        ("google", None),
        ("realtime", None),
        ("Chrome-Bulut", None),  # the spelling is the client's, letter for letter
        (" chrome-bulut", None),
        ("", None),
        ("chrome-bulut" + "x" * 53, None),  # longer than MAX_LEN: never cut back into a name
        ("x" * (stt_engine.MAX_LEN + 1), None),
        (3, None),
        (1.5, None),
        (True, None),
        (["chrome-bulut"], None),
        ({"stt_engine": "chrome-bulut"}, None),
        (None, None),
    ],
)
def test_normalise_returns_one_of_the_three_names_or_none(value: Any, expected: Any) -> None:
    assert stt_engine.normalise(value) == expected


def test_the_three_names_and_the_bound() -> None:
    assert stt_engine.STT_ENGINES == frozenset({"chrome-cihaz-ici", "chrome-bulut", "bilinmiyor"})
    assert stt_engine.MAX_LEN == 64
    assert all(len(name) <= stt_engine.MAX_LEN for name in stt_engine.STT_ENGINES)


# ------------------------------------------------- (1) the name on the audit row


def test_each_name_leaves_one_audit_row_carrying_it(wired) -> None:
    client, _identity, runtime, *_ = wired
    stored: list[Any] = []
    for name in ("chrome-cihaz-ici", "chrome-bulut", "bilinmiyor"):
        sid = _local_session(client, runtime)
        _say(client, sid, UNDERSTOOD, turn=1, engine=name)
        rows = _resolved_rows(runtime, sid)
        assert len(rows) == 1, rows
        assert rows[0].metadata_json["stt_engine"] == name
        stored.append(rows[0].metadata_json["stt_engine"])
    # Three values, three rows, three distinct stored outcomes.
    assert len(set(stored)) == 3


# --------------------------------------------------- (2) everything else is null


@pytest.mark.parametrize("case", list(UNKNOWN))
def test_anything_else_stores_null_and_the_turn_is_unchanged(wired, case: str) -> None:
    client, _identity, runtime, *_ = wired
    if case == "no_payload":
        payload: Any = ...
    elif case == "empty_payload":
        payload = {}
    else:
        payload = {"stt_engine": UNKNOWN[case]}

    baseline_sid = _local_session(client, runtime)
    baseline = _say(client, baseline_sid, UNDERSTOOD, turn=1)

    sid = _local_session(client, runtime)
    response = _post(client, sid, UNDERSTOOD, turn=1, payload=payload)
    assert response.status_code == 200
    assert response.json()["resolved_intents"] == baseline["resolved_intents"]
    rows = _resolved_rows(runtime, sid)
    assert len(rows) == 1
    assert "stt_engine" in rows[0].metadata_json and rows[0].metadata_json["stt_engine"] is None
    assert _turn_record(runtime, sid)["stt_engine"] is None


# ------------------------------------------------------ (3) the session activity


def test_activity_names_the_engine_of_each_turn(wired) -> None:
    client, _identity, runtime, *_ = wired
    sid = _local_session(client, runtime)
    _say(client, sid, UNDERSTOOD, turn=1, engine="chrome-cihaz-ici")
    _say(client, sid, UNDERSTOOD, turn=2)
    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity")
    assert activity.status_code == 200, activity.text
    intents = activity.json()["intents"]
    assert [entry["t_ms"] for entry in intents] == [1000, 2000]
    assert [entry["stt_engine"] for entry in intents] == ["chrome-cihaz-ici", None]


# --------------------------------------------------------- (4) the turn record


def test_the_turn_record_carries_the_latest_utterances_engine(wired) -> None:
    client, _identity, runtime, *_ = wired
    sid = _local_session(client, runtime)
    _say(client, sid, UNDERSTOOD, turn=1, engine="chrome-cihaz-ici")
    assert _turn_record(runtime, sid)["stt_engine"] == "chrome-cihaz-ici"
    _say(client, sid, UNDERSTOOD, turn=2, engine="chrome-bulut")
    record = _turn_record(runtime, sid)
    assert (record["turn"], record["stt_engine"]) == (2, "chrome-bulut")


# ------------------------------------------------------- (5) it decides nothing


_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


def _canonical(body: bytes, sid: str) -> bytes:
    """The response with what two sessions cannot share (their id and the clock) masked."""
    return _ISO.sub("<at>", body.decode("utf-8").replace(sid, "<sid>")).encode("utf-8")


def test_the_response_is_byte_equal_with_and_without_the_name(wired) -> None:
    client, _identity, runtime, *_ = wired
    without_sid = _local_session(client, runtime)
    without = _post(client, without_sid, UNDERSTOOD, turn=1).content
    with_sid = _local_session(client, runtime)
    named = _post(
        client, with_sid, UNDERSTOOD, turn=1, payload={"stt_engine": "chrome-cihaz-ici"}
    ).content
    assert _canonical(named, with_sid) == _canonical(without, without_sid)
    assert b"stt_engine" not in named and b"chrome-cihaz-ici" not in named


# ---------------------------------------------------- (6) contract halves


def test_the_client_type_is_the_server_set_and_it_posts_the_key() -> None:
    source = WEB_LOCAL_MODE.read_text(encoding="utf-8")
    declared = re.search(r"export type SttEngine\s*=\s*([^;]+);", source)
    assert declared is not None, "SttEngine is no longer declared in localMode.ts"
    members = set(re.findall(r'"([^"]+)"', declared.group(1)))
    assert members == stt_engine.STT_ENGINES
    # The utterance event carries the key inside `payload` (turnBody).
    assert re.search(
        r'kind:\s*"utterance"[^\n]*payload:\s*\{\s*stt_engine:\s*engine\s*\}', source
    ), "localMode.ts no longer posts payload.stt_engine on the utterance event"


# ----------------------------------------------- (7) the name, never the sentence


UNROUTED = "Kırmızı balonlar neden gökyüzünde dans ediyor?"


def test_the_records_hold_no_more_of_the_sentence_than_before(wired, caplog) -> None:
    client, _identity, runtime, *_ = wired
    caplog.set_level(logging.DEBUG)
    for sentence in (UNDERSTOOD, UNROUTED):
        without_sid = _local_session(client, runtime)
        _say(client, without_sid, sentence, turn=1)
        with_sid = _local_session(client, runtime)
        caplog.clear()
        said = _say(client, with_sid, sentence, turn=1, engine="chrome-bulut")
        assert sentence not in caplog.text

        named_row = _resolved_rows(runtime, with_sid)[0].metadata_json
        plain_row = _resolved_rows(runtime, without_sid)[0].metadata_json
        assert sentence not in json.dumps(named_row, ensure_ascii=False)
        assert {k: v for k, v in named_row.items() if k != "stt_engine"} == {
            k: v for k, v in plain_row.items() if k != "stt_engine"
        }

        named_turn = _turn_record(runtime, with_sid)
        plain_turn = _turn_record(runtime, without_sid)
        assert named_turn["stt_engine"] == "chrome-bulut"
        assert {k: v for k, v in named_turn.items() if k not in ("stt_engine", "at")} == {
            k: v for k, v in plain_turn.items() if k not in ("stt_engine", "at")
        }
        # The local-mode chat_question rule, unchanged: the sentence the router understood
        # nothing of is kept for this one turn; an understood one is not kept at all.
        if said["resolved_intents"][0]["intent"] == "none":
            assert named_turn["chat_question"] == sentence
        else:
            assert named_turn["chat_question"] is None
            assert sentence not in json.dumps(named_turn, ensure_ascii=False)
    assert said["resolved_intents"][0]["intent"] == "none", "UNROUTED must reach the chat rule"
