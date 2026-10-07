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

1. **Direction words.** After "çeyrek" or after spoken minutes, the WHOLE word `kala`,
   `kalmadan` or `var` means "to the hour" (hour−1, 60−minutes); a word starting `geç`/`gec`
   means "past". The same for digits: `8'i 10 geçe`, `8'e 5 kala`, `8'e çeyrek kala` (one
   regex, after `HH:MM`). Not a prefix `kal`: that is also the verb "kalk-" (to get up), and
   "yarın sabah 7 30 kalkmam lazım" became 06:30 (inspection 1 of 9d1be05a).
2. **Round-vowel accusative** `u/ü/yu/yü` is a clock case suffix ("dokuzu", "üçü", "dördü";
   stem `dörd` added) — but only before a fraction or a direction word, because "onu" is also
   the pronoun "it".
3. **Afternoon.** `öğleden sonra` (the two words together) shifts hours < 12 like "akşam";
   "öğleden önce" does not ("öğleden önce onda" is 10:00, it was 22:00). `öğlen/öğle` shifts
   only 1..5 (a lunchtime). "gece" is unchanged (still evening; "gece ikide" stays an open item).
4. **Article "bir".** A bare "bir" with no suffix, fraction, minute or direction is the article;
   it is used as 01:00 only when the sentence names no other time ("saat bir" still works).
5. **Corrections.** The time after the LAST marker (`değil`, `değil de`, `yok yok`, `pardon`,
   `hayır hayır`) wins when that tail holds a time; a daypart or weekday said in the tail wins
   over one said before it ("öğleden" carries its "sonra"/"önce" into the tail). The day: the
   last "yarın"/"bugün" said after a marker wins ("yarın değil bugün akşam sekizde" and
   "akşam sekizde, yarın değil bugün" are today); with none after a marker, "yarın" anywhere
   means tomorrow, as before.
6. **Negated create.** A whole-word `kurma/kurmayın/kurmasın/uyandırma/...` makes the parse
   raise `UnparsedWhen` — no time, so nothing is scheduled. Not a negation: "kurmadan",
   "uyandırmayı unutma", and the nouns "uyandırma alarmı/saati" (corpus a.create.3 caught it)
   and "alarm kurma işi". The voice negation card owns the verb's routing; this is the time
   reader's floor.

## Consequences

- A negated create now answers with the existing "when unparsed" speech instead of setting
  an alarm; the negation card may give it a better sentence.
- `parse_when_text` has three more callers: `voice/realtime_sessions/tools_calendar.py`,
  `voice/realtime_sessions/tools_ambient.py` and `operator/plans.py`. The negated-create
  refusal and the correction rules reach them too: a calendar or ambient "when" that says
  "kurma" now raises `UnparsedWhen` (their existing "could not place the time" path), and a
  corrected time/day is read as for an alarm. No harm seen; their suites stay green.
- Correction markers are narrow on purpose; "yani" / "aslında" are not markers yet.
- Evidence: 51 unit cases (`tests/unit/test_alarm_tr_time_spoken.py`), 11 + 7 mutations each
  RED and restored byte-for-byte (the second set includes "first marker instead of last");
  the 146 alarm rows of the owner utterance corpus green after the fix.
