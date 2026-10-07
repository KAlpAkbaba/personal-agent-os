# ADR draft — alarm-spoken-time-and-corrections: the alarm's Turkish time reader hears kala/geçe, the afternoon, a correction and a "kurma"

Status: proposed (worker-4, cycle d20261006). Number assigned by the lead at merge.

## Context

Test team round t-d20261006 on staging fba299af (22 cards merged into one) found
`app/alarms/tr_time.py::parse_when_text` misreading spoken times: "sekize çeyrek kala" set
08:15, "dokuzu çeyrek geçe" was not read at all, "öğleden sonra üçte" set 03:00, "8'i 10 geçe"
set 08:00, the article "bir" in a long sentence set 01:00, a spoken correction ("yedide değil
sekizde", "yok yok", "değil de") kept the first time, and "yarın sabah yedide alarm kurma"
SET an alarm.

## Decision

Pure-function changes in `tr_time.py` only; no caller changed.

1. **Direction words.** After "çeyrek" or after spoken minutes, a word starting `kal`/`var`
   means "to the hour" (hour−1, 60−minutes), `geç`/`gec` means "past". The same for digits:
   `8'i 10 geçe`, `8'e 5 kala`, `8'e çeyrek kala` (one regex, after `HH:MM`).
2. **Round-vowel accusative** `u/ü/yu/yü` is a clock case suffix ("dokuzu", "üçü", "dördü";
   stem `dörd` added) — but only before a fraction or a direction word, because "onu" is also
   the pronoun "it".
3. **Afternoon.** `öğleden (sonra)` shifts hours < 12 like "akşam"; `öğlen/öğle` shifts only
   1..5 (a lunchtime). "gece" is unchanged (still evening; "gece ikide" stays an open item).
4. **Article "bir".** A bare "bir" with no suffix, fraction, minute or direction is the article;
   it is used as 01:00 only when the sentence names no other time ("saat bir" still works).
5. **Corrections.** The time after the LAST marker (`değil`, `değil de`, `yok yok`, `pardon`,
   `hayır hayır`) wins when that tail holds a time; the date ("yarın") comes from the whole
   sentence; a daypart or weekday said in the tail wins over one said before it.
6. **Negated create.** A whole-word `kurma/kurmayın/kurmasın/uyandırma/...` makes the parse
   raise `UnparsedWhen` — no time, so nothing is scheduled. Not a negation: "kurmadan",
   "uyandırmayı unutma", and the noun "uyandırma alarmı/saati" (corpus a.create.3 caught it).
   The voice negation card owns the verb's routing; this is the time reader's floor.

## Consequences

- A negated create now answers with the existing "when unparsed" speech instead of setting
  an alarm; the negation card may give it a better sentence.
- Correction markers are narrow on purpose; "yani" / "aslında" are not markers yet.
- Evidence: 38 unit cases (`tests/unit/test_alarm_tr_time_spoken.py`), 11 mutations each RED
  and restored byte-for-byte; the 146 alarm rows of the owner utterance corpus green.
