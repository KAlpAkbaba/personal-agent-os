# ADR (home-stock-list): the house's stock, the shopping list and the "bitmeden" reminder

Status: proposed by worker-1, cycle d20261005. Roadmap: JARVIS order step 5 ("Keeps the house's
stock, down to the toilet paper", the owner, 2026-10-05).

## Decision

1. **No device, the owner's words.** An item has a level - `var`, `azaldı`, `bitti` (NULL for an
   item only ever listed) - and a list flag with an optional quantity. Running low or out puts it
   on the list; "aldım" sets `var` and takes it off. Tables `household_items` (unique folded
   `key`) and `household_events` (`depleted` / `restocked`, dated), migration
   `alembic/versions/household_stock.py`, expand-only, down = drop both.
2. **One item under all its suffixes.** `app.household.parse.item_key` folds Turkish letters and
   strips the last word's case/possessive/plural ending, preferring a reading that is a known
   household word ("çayı" -> "çay", not "ça"; "suyu" -> "su"; "kağıdını" -> "kağıt"). The kept
   name is the vocabulary's nominative ("sütü" -> "süt", "kedi mamasını" -> "kedi maması").
3. **Anchored words, a gate on the everyday verbs.** "bitti", "aldım", "azaldı" are everyday
   verbs ("Toplantı bitti", "Mesajını aldım", "Telefonun şarjı azaldı"). A level sentence must
   end in the verb, carry no question word, and name a household-vocabulary item or say where
   ("evde", "mutfakta"). List edits need `listeye`/`listeden` + an exact verb; reads are
   "ne almam lazım", "listeyi oku", "listede ne var", "markete gidiyorum", "evde ne eksik".
   The router block sits before the operator's typing ("listeye süt YAZ") and the artifact
   factory's "liste" (dataset).
4. **The rhythm is computed from the events only.** A depletion is a move from `var` (or
   nothing) to `azaldı`/`bitti` - "azaldı" then "bitti" is one. `cycle_days` = mean of the
   last 5 gaps between depletions (gaps < 1 day ignored), needing 2 gaps; never derived from
   the previous rhythm, so recomputing changes nothing (tested twice-in-a-row).
5. **The reminder: once per cycle, a few days before, never nagging.** Due when the item is
   `var`, not on the list, and now is within `lead_days` (round(cycle/7) clamped 1..3) of
   `depleted_at + cycle_days`, and not more than one cycle late. One notification
   (`household.reminder`, via `notifications.record`) per cycle, stamped `reminded_at`; a new
   depletion opens the next cycle. Nothing is put on the list on the owner's behalf; the list
   read says "Yakında bitebilir: ..." instead. Hourly `ReminderLoop` in the API process,
   on `/v1/system/health` as `household_reminders`.
6. **Voice tools.** `household.level`, `household.list_add`, `household.list_remove` (actions),
   `household.list_read` (query). The router's `household_item/level/quantity` win over the
   model's `item/level/quantity`; no item is a question ("Hangi ürün efendim?").

## Second round (inspector, 2026-10-06)

- A cycle is counted from the last depletion, or from the last purchase when that came after
  the reminder window had opened (`service.cycle_start`): bought late, the next run-out is a
  cycle after the purchase, and a reminder sent before the late purchase does not silence the
  next cycle. Alternative rejected: "never remind within N hours of a purchase" - it only moves
  the false reminder to hour N+1.
- "X listesine / listesinden" is the shopping list only when X is alışveriş/market/bakkal/
  pazar/ev/mutfak; any other qualifier (çalma, yapılacaklar, oynatma) is another list.
- A bounded deny-list of things a house does not stock (`_NOT_GOODS`: people, money,
  utilities, notes, tasks, songs...) closes the place gate ("evde kimse kalmadı") and the bare
  "listeye not ekle". An allow-list would refuse "evde lazer toner bitti", which the owner may say.
- A count in a level sentence is not the name ("Bir kahve aldım" -> kahve).

## Needs outside this card's area (ALAN_ISTEGI)

- `app/voice/realtime_sessions/service.py`: copy `household_item`, `household_level`,
  `household_quantity` into `last_utterance` (the free local mode sends no arguments).
- `app/security/step_up.py`: tiers - `household.list_read` OPEN; the three writes (reversible
  rows of the owner's own list) proposed OPEN as well.
- `tests/unit/test_voice_realtime_sessions.py`: the four names in the tool-set contract.
- `tests/voice_corpus/harness.py`: `HouseholdItem.__table__`, `HouseholdEvent.__table__` in
  `TABLES`.
- `app/voice/understanding/exemplars.json`: regenerate (`services/api/scripts/export_understanding_exemplars.py`).

## Not in this card

Receipts/orders from mail (after mail-accounts-connect), Home Assistant sensors, "evde süt var
mı?" as a stock question, several items in one sentence ("süt ve ekmek aldım").
