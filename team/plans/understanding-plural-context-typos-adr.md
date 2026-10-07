# ADR draft: layer 1 word repairs - plural, one typo, fused household sentence (card understanding-plural-context-typos)

Status: proposed (worker-1, cycle d20261006). Number: the lead's.

## Context

The test team (rounds t-d20261006, staging fba299af, 30 findings) found the singular of each
sentence routed and the form the owner says did not: "alarmları kapat", "alarmlarımı sil",
"nöbetlerimden birini kaldır", "sütt bitti", "sütbitti", "alrmı kapat", "alarmmı kapat".

Putting the repairs into the reading the router already takes (`lemma_reading`, as `invented`)
was measured and refused: the tables read most plurals as they are ("Ekranları kapatın.",
"Maillerime bakın.", "Haberleri açın."), and the repair lowered those from the suffix-dropped
0.9 to the repaired-word 0.75; six `test_understanding_rules_read_lemmas` cases and
`test_no_sentence_of_the_owner_utterance_suite_holds_an_invented_ending` went RED.

## Decision

1. `normalize.lemma_reading(text, keep=..., repair_words=True)` adds three WORD repairs, each
   from the module's own grammar or one edit, never from a list of forms. They go in the new
   `LemmaReading.repaired` field (a fused household sentence goes in `splits`):
   * a known noun with a plural ending is its singular in the same case (`_singular`, read by
     the suffix grammar: "alarmlarımızı" -> "alarmı"); a plural ablative followed by the
     partitive the grammar builds ("bir" + 3sg + acc = "birini") is the one accusative
     ("nöbetlerimden birini" -> "nöbeti"), unless punctuation stands between them;
   * one slip undone (`_typo`): a doubled letter collapsed into a known noun form or a household
     item's bare name ("alarmmı", "sütt"), else one VOWEL put back into a known noun form whose
     stem has >= 5 letters ("alrmı"). Never a deletion ("hafta" is not "hata"), never a
     consonant ("takim" is not "takvim"), never an inflected household form ("etti" is not
     "eti"), never two candidates ("pencerey" is "pencereye" or "pencereyi": left whole), and
     never a token longer than `_TYPO_MAX_LEN` = the longest noun form the grammar builds
     ("hatırlatıcılarımızdan", 21) + 1 doubled letter: a 1200-letter STT token took 12 s in the
     position x vowel loop (inspector, 2026-10-07), now 0 lookups;
   * a token that is a household item's bare name + words the household parser itself reads
     as a command with it, one way only ("sütbitti" -> "süt bitti").
2. Default `repair_words=False`: today's reading is byte-for-byte what it was (all guards green).
3. "nöbet" joins the noun list (the watch family's noun).
4. The router (outside this area) asks for that reading only when the words as heard reached
   NOTHING, and takes it at the repaired-word confidence 0.75 (MEDIUM: read back in the
   receipt, never a second question). Verified in a scratch script, not in `intents.py`:
   all seven sentences reach the singular's intent at 0.75, "Alarmlarımı silebilir misin?"
   reaches alarm_cancel, "alarmları kapatma" stays none, "bu hafta ne var" stays as it was.
   Proposed wiring, at the end of `resolve_intent` before `_taught_app_open`, when
   `first.intent is Intent.NONE`:
   ```python
   reading = layer_one.lemma_reading(text, keep=_POLITE_NOT_A_REQUEST, repair_words=True)
   if reading is not None and (reading.repaired or reading.splits):
       second = _resolve_intent_rules(reading.text, **state)
       if owned_by_a_table(second) and not second.intent.value.startswith(_REPAIR_NEVER_PREFIXES):
           return replace(second, route_repair="repaired",
                          confidence=min(second.confidence, _REPAIRED_WORD_CONFIDENCE))
   ```
   `policy._REPAIRED_WORD_LABELS` already holds "repaired" (this card), so the relay's adapter
   reads that label at the confusion confidence 0.75, as it reads "fused" and "invented".

## Not decided here (outside the area; RED tests committed)

* Context ("onu yedi buçuğa al", "bir öncekini sil", "aynısını yarın için"): the session needs
  the last object per family (`realtime_sessions/service.py`) and the router a slot for it.
* "Şu an çalan alarmı kapatır mısın artık, ..." routes to window_close on the word "şu"
  (`intents.py` window table) - a wrong ACTION, not a miss.
* Two household items in one sentence: `ResolvedIntent.household_item` holds one
  (`intents.py`, `app/household/parse.py`).
* A pronoun with nothing before it: no action and ONE short Turkish question, in the relay
  record's `clarification_question` (today None; RED test through the real relay).
* Empty / 1200-character utterance: `ClientEvent.text` allows 0-4000 (`realtime_sessions/routes.py`);
  the RED test posts to `/events` and wants HTTP 422 with a Turkish body (today 200).
* Open risk, already on main and flag-independent: `_split` (the fused-word reading every
  sentence gets) has no length cap either - one 4000-letter token reads in 7.8 s (1200: 0.34 s).
  A cap there changes today's reading, so it is a card of its own.
