# ADR draft - money-amount-input-edges: Arabic-Indic digits are read; an absurd length is refused before int()

Status: proposed (worker-2, cycle d20261007). Card: money-amount-input-edges (test round
t-r10070152, cards test-fail-para-defteri-ee555d6edd / -6e93403410, staging da3e26b9).

## Context

`POST /v1/money/cash {"amount": "<5000 nines>"}` (and 4301 nines) answered **500**: Python's
int-from-string refuses more than 4300 digits with a `ValueError`, which `parse_amount` never
caught. `{"amount": "٧٥٠"}` answered **200** by accident: `\d` and `int()` take any Unicode
decimal digit, so fullwidth `７５０` or Devanagari `७५०` were booked too - nothing decided it.

## Decision

1. `app/money/amounts.parse_amount` refuses a stripped input longer than
   `MAX_AMOUNT_CHARS = 64` characters **before** any other work (the largest amount a bank
   writes, `99.999.999,99 TL`, is 16). It returns `None`, so the route's existing Turkish
   refusal answers: 422 `money_refused`, "Tutarı anlayamadım; '750' ya da '1.234,56' gibi yaz."
   A long-but-sane number (e.g. 30 digits) still parses and the ledger's `MAX_KURUS` refuses
   it with "fazla büyük" as before.
2. Arabic-Indic (U+0660-0669) and Persian / extended Arabic-Indic (U+06F0-06F9) digits are
   **read** (translated to ASCII): `٧٥٠` -> 75000 kuruş. A phone keyboard or a pasted bank SMS
   can carry them, and the meaning is unambiguous. The card allowed either; reading is the
   owner-friendlier choice and costs one translate table.
3. Every other non-ASCII digit is **refused** (`[0-9]` instead of `\d`): we read what we
   decided to read, nothing by accident.
4. The Arabic decimal / group marks (`٫` U+066B, `٬` U+066C) are not mapped: Turkish `,`/`.`
   are expected; a string with them is refused, never guessed.

## Consequences

- Voice path (`money_in`) gets the same reader: a sentence with a 5000-digit token finds no
  amount and does not raise; `٧٥٠ lira verdim` finds 750 TL.
- The test team's case "Arap rakamı ٧٥٠" expected 422; with this decision the expectation
  should be 200 / `"amount_kurus":75000` - the Test PY updates the scenario file.
- Routes are untouched: the fix is in the reader, so every caller (cash, answer-yes, voice,
  bank mails) is covered.
