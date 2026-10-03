# ADR-0255 addendum (draft, the lead numbers it): a chat model that timed out is recorded as `timeout`

**Context.** ADR-0255 'Not closed here' A: the narrative's ledger note names why the model
narrator did not speak (`narrator_reason`). `AnthropicChatProvider.answer` caught every
`httpx.HTTPError` - a timeout among them - into `chat_unavailable`, so over the real provider
"the model was too slow" and "the model could not be reached" were one word in the record, and
ADR-0221's latency budget could not be judged from it.

**Decision.** `answer` catches `httpx.TimeoutException` (connect, read, write, pool) BEFORE the
other transport errors and returns `ChatAnswer(SPEECH_FAILED, False, ERROR_CHAT_TIMEOUT, model)`,
`ERROR_CHAT_TIMEOUT = "timeout"` - the same word the note's writer
(`app/voice/realtime_sessions/tools.py`, unchanged) already records for a provider that raises a
timeout, so the note now reads `rule / timeout`. Its own log event is
`assistant_chat_transport_timeout`. No retry, no new setting, the timeout's length unchanged.

**Callers.** The narrative (model_narrator -> note): `timeout` instead of `chat_unavailable` - the
point of the change. The `assistant.chat` tool (the chat route): the owner hears the same
sentence ("Şu an yanıt alamadım efendim.") with the same HTTP status and `answered: false`
(pinned byte-equal through the real application object); its result's `error_class` field
reads `timeout` instead of `chat_unavailable` for a timed-out chat - a diagnostic field no
code branches on (grep: nothing compares a chat answer's error_class to `chat_unavailable`).
The tool call itself stays `succeeded`, so its result's class never reaches
`app/errors/catalog.py`'s owner sentence (which does have a `timeout` entry); no client under
`apps/` names `assistant.chat`. explain/service does not read the class. Every non-timeout failure (refused connection, HTTP
500, malformed body, 404, 429/529, refusal) answers exactly what it did.

**Evidence.** tests/unit/test_assistant_chat_timeout.py (httpx MockTransport under the real
`_http_send`, six cases, two outcomes that do not mix); the wiring test's real-provider case is
parametrized timeout -> `rule / timeout`, refused -> `rule / chat_unavailable`. Not run: a real
timeout of the real model (the narrator is OFF in production).
