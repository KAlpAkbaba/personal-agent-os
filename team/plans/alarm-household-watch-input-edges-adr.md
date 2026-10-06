# ADR draft: input edges of /v1/alarms, /v1/watches, /v1/household (test-team findings)

Status: proposed (card alarm-household-watch-input-edges, cycle d20261006). Lead numbers it.

## Context
The test team's automatic rounds (t-d20261006, staging fba299af) found input edges that were
taken silently, answered in English, or escaped as a 500. A worker reproduced the 500s:
`9999-12-31` in a zone west of UTC raised `OverflowError`, and `POST /stop` on a SNOOZED
alarm let `IllegalAlarmTransition` escape.

## Decision (all at the route layer; the services are unchanged)
1. **Control characters.** On alarms, any Unicode category `Cc` character (C0, U+007F and
   the C1 block U+0080-U+009F, e.g. NEL - the inspector found U+0085 stored raw) in `label`,
   `greeting_text`, `media.url/title/remembered`, the wake song's `url/title` and a cancel
   `reason` gives a Turkish 422 ("okunamayan bir karakter", `validation_error`); nothing is
   written. A watch's `label/url/condition/selector` below U+0020 or U+007F gives a Turkish 422
   `control_character`. Household names were already refused by `service.clean_name`.
   `when_text` is left to the parser (it is never stored).
2. **Alarm dates.** A past date/time is a 422 with "geçmişte kaldı". Anything more than
   **366 days** ahead is a 422 with "bir yıldan daha ileriye alarm kuramam". This covers the
   overflow too: a date that cannot be moved to UTC gets the same answer.
3. **List limit.** `GET /v1/alarms?limit=` outside 1..200 is a Turkish 422. Before, the
   service silently clamped it to 200.
4. **Stop.** A stop is allowed from the ringing states, from STOPPED (idempotent, spec §3.8)
   and from SCHEDULED/ARMED (turns a waiting alarm off: "alarmı kapat"). It is refused with a
   Turkish 409 `lifecycle_violation` ("şu an çalmıyor") from SNOOZED, CANCELLED, COMPLETED
   and FAILED. Before, those answered 200 "Alarmı kapattım" (or a 500 for SNOOZED).
   Deviation from the card's literal "non-ringing → 409": SCHEDULED/ARMED stay 200, because
   `tests/unit/test_alarms_routes.py::test_stopping_an_idle_alarm_is_idempotent` (outside
   this card's area) pins that, and in Turkish "alarmı kapat" for an upcoming alarm means
   "turn it off". To flip this, widen the area to that test and drop SCHEDULED/ARMED from
   `_STOPPABLE`.
5. **Unknown alarm.** `GET /v1/alarms/{id}` answers 404 with the Turkish `owner_detail` body,
   not the English "alarm not found". A stopped alarm still reads back with 200.
6. **Household quantity.** A finite JSON number is a quantity: `2` is kept as "2", `2.5` as "2.5".
   bool, list, object and Infinity are still refused.
7. **Second removal (chosen: 200).** `DELETE /v1/household/list/{id}` for an item that exists
   but is not on the list answers **200** `{item, speech: "Bu ürün zaten listede değil: …",
   already: true}`, so the call is idempotent. The first removal answers `already: false`
   with "Listeden çıkardım: …". An id that was never an item answers 404 with "Bu ürün
   listede değil; zaten çıkarılmış ya da hiç eklenmemiş."

## Consequences
The voice path calls the services directly and is unchanged. The web client does not call
`/stop`. Proof: `tests/unit/test_input_edges_test_team.py`, with 12 mutations, each RED.
