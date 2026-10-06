# ADR (draft): typed household input is a short noun phrase and a positive amount

Status: proposed (household-input-validation-and-suffix, cycle d20261006)

## Context
Test team round t-manual-20261006e (staging 72884b71, tester-2): `POST /v1/household/list`
kept `-3 paket` and `0` as quantities and a long Turkish sentence as an item name; `sütü`
was reported as not landing on `Süt`. The voice path never hands such input over (its parser
only extracts nouns and counts), but a form or a script reaches the route directly.

## Decision
1. `parse.quantity_problem`: a typed quantity is a number above zero - digits (`3`, `1,5`,
   `500gr`) or Turkish number words (`iki`, `on iki`, `yarım`) - and at most one unit word.
   No sign, no zero, no bare unit. Refusal: 422 `household_refused`, Turkish message.
2. `parse.name_problem`: a typed name is a short noun phrase - at most 40 characters and
   `MAX_ITEM_WORDS` (4) words; not a household sentence (`parse_words` reads nothing from it);
   no word the voice parser already knows as a verb; no finite-verb ending (progressive
   `-iyor…`, first-person past `-dım`, future `-acağım`, necessity `-malıyım`). Bare
   participles (`-miş`, `-ecek`) are NOT refused: `kuru yemiş`, `içecek` are goods.
   Refusal: "Bunu ürün adı olarak anlayamadım; …".
3. Both checks run in `routes.py` before `service`; the voice path is unchanged.
4. The fold (`parse.item_key`, shared by voice and HTTP) also strips the genitive after a
   consonant (`-ın/-in/-un/-ün`, `-ların/-lerin`): `sütün`, `sütlerin` -> `süt`. `sütü` and
   `sütleri` already folded to `süt` at 72884b71 (proven by the new test passing pre-fix);
   the reported miss is reproduced only for the genitive. No word-form table: suffix rules.

## Consequences
- Keys of unknown words ending in `-in/-un` change (`tahin` -> `tah`, as `tahini` already
  was). A row stored before this change under the old key is not found by its new key and a
  second row may be created. The table is one day old (0070); accepted, no migration.
- A typed possessive like `tuvalet kağıdım` (`-dım`) is refused; the owner types the noun.
