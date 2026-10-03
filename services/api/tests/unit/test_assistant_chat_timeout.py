"""A chat model that was too slow is told apart from one that could not be reached
(ADR-0255 'Not closed here' A).

``AnthropicChatProvider.answer`` caught every ``httpx.HTTPError`` into ``chat_unavailable``,
so the narrative's ledger note could not say "the model was too slow" - the latency budget
of ADR-0221 was unreadable from the record. A transport TIMEOUT (connect, read, write, pool)
is now its own class, ``timeout``; every other failure answers exactly what it did, and the
owner hears the same Turkish sentence either way.

The transport is httpx's own ``MockTransport`` under the real ``_http_send`` - no network.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import pytest

from app import assistant_chat as chat
from app.voice.providers_local_router import LocalRouterRealtimeProvider
from tests.unit.test_voice_local_mode import TRANSPORT_TEXT, _create, _say, wired  # noqa: F401

Handler = Callable[[httpx.Request], httpx.Response]


def _raise(exc_type: type[httpx.TransportError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("simulated", request=request)

    return handler


TIMEOUTS: dict[str, Handler] = {
    "read-timeout": _raise(httpx.ReadTimeout),
    "connect-timeout": _raise(httpx.ConnectTimeout),
    "pool-timeout": _raise(httpx.PoolTimeout),
}
OTHERS: dict[str, Handler] = {
    "connection-refused": _raise(httpx.ConnectError),
    "http-500": lambda request: httpx.Response(500, json={"error": {"type": "api_error"}}),
    "malformed-body": lambda request: httpx.Response(200, content=b"{not json"),
}

#: What the provider answered for each non-timeout case BEFORE this change (captured in the
#: red-first run with the unchanged provider) - it must not move by a byte.
UNCHANGED = chat.ChatAnswer(
    chat.SPEECH_FAILED, False, chat.ERROR_CHAT_UNAVAILABLE, chat.DEFAULT_MODEL, 0, 0
)


@pytest.fixture()
def transport(monkeypatch):
    """Route the real ``_http_send``'s ``httpx.post`` through a MockTransport client."""

    def use(handler: Handler) -> list[float]:
        timeouts: list[float] = []
        client = httpx.Client(transport=httpx.MockTransport(handler))

        def post(url, *, headers, json, timeout):
            timeouts.append(timeout)
            return client.post(url, headers=headers, json=json, timeout=timeout)

        monkeypatch.setattr(httpx, "post", post)
        return timeouts

    return use


def _answer() -> chat.ChatAnswer:
    provider = chat.AnthropicChatProvider("k-test", sleep=lambda _s: None)
    return provider.answer("Kuantum bilgisayar nedir?", history=[], now_tr="03.10.2026 05:00")


@pytest.mark.parametrize("case", list(TIMEOUTS))
def test_a_transport_timeout_is_its_own_class(transport, case) -> None:
    timeouts = transport(TIMEOUTS[case])
    answer = _answer()
    assert answer == chat.ChatAnswer(
        chat.SPEECH_FAILED, False, chat.ERROR_CHAT_TIMEOUT, chat.DEFAULT_MODEL, 0, 0
    )
    assert chat.ERROR_CHAT_TIMEOUT == "timeout"
    assert timeouts == [20.0], "one attempt, the timeout's length unchanged, no retry"


@pytest.mark.parametrize("case", list(OTHERS))
def test_every_other_failure_answers_exactly_what_it_did(transport, case) -> None:
    transport(OTHERS[case])
    answer = _answer()
    assert type(answer) is chat.ChatAnswer
    assert answer == UNCHANGED


def test_the_two_sets_do_not_mix(transport) -> None:
    classes: dict[str, set[str | None]] = {"timeouts": set(), "others": set()}
    for key, cases in (("timeouts", TIMEOUTS), ("others", OTHERS)):
        for handler in cases.values():
            transport(handler)
            classes[key].add(_answer().error_class)
    assert classes == {"timeouts": {"timeout"}, "others": {"chat_unavailable"}}


# ------------------------------------------------------------- through the real relay

#: The chat route's answer for a timed-out chat BEFORE this change (captured red-first):
#: the owner hears the same Turkish sentence and the route the same status code.
BEFORE_STATUS = 200
BEFORE_SPEECH = "Şu an yanıt alamadım efendim."


def test_a_timed_out_chat_says_the_same_sentence_to_the_owner(
    wired,  # noqa: F811
    transport,
) -> None:
    client, _identity, runtime, *_ = wired
    local = LocalRouterRealtimeProvider()
    runtime.providers[local.name] = local
    timeouts = transport(TIMEOUTS["read-timeout"])
    runtime.register_live(chat_provider=chat.AnthropicChatProvider("k-test"))
    sid = _create(client, transport=TRANSPORT_TEXT)["session_id"]
    said = _say(client, sid, "Kuantum bilgisayar nedir?", turn=1)
    assert said["resolved_intents"][0]["tool"] == "assistant.chat"

    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": f"local-{uuid.uuid4()}", "name": "assistant.chat", "arguments": {}},
    )
    assert response.status_code == BEFORE_STATUS, response.text
    body = response.json()
    assert body["status"] == "succeeded", body
    assert body["result"]["speech"].encode("utf-8") == BEFORE_SPEECH.encode("utf-8")
    assert body["result"]["answered"] is False
    assert len(timeouts) == 1
