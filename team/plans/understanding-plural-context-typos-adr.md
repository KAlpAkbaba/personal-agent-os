# ADR draft: layer 1 word repairs - plural, one typo, fused household sentence (card understanding-plural-context-typos)

Status: proposed (worker-2, cycle d20261006; return 2 wires it). Number: the lead's.

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
4. The router asks for that reading only when the words as heard and the polite / folded
   repairs reached NOTHING (`intents._word_repair_route`, label `repaired`), at the
   repaired-word confidence 0.75 (MEDIUM: read back, never a second question). "One of
   them" ("nöbetlerimden birini kaldır": a partitive the reading folded into one accusative)
   is NOT acted on - none is named, and `test_watch_voice` pins that "Nöbetlerimden birini
   sil." deletes nothing; the session asks which instead.
5. Long polite sentence: in the window table a deictic directly before a time noun ("şu an",
   "o zaman", "bu arada") is a time adverb, not a pointer (`_deictic_pointer`), so "Şu an
   çalan alarmı kapatır mısın artık, ..." is alarm_stop, not window_close.
6. Two household items: `household.parse` reads a sentence of two or more clauses, each a
   known item + a level verb, all at ONE level, as one command with `items`
   ("süt de bitmiş ekmek de kalmamış" -> süt, ekmek); a vocabulary item wins over a known
   head ("mutfağa baktım da süt" -> süt); "haberin olsun" is a tail. Two levels, or a clause
   without a known item: the one-item reading stands. The router carries `household_items`,
   the relay record keeps it.
7. Context: the session row keeps the family of the last alarm/watch intent
   (`ctx["last_object"]`). A sentence nothing routed with a pointing word (onu, bunu, şunu,
   bir öncekini, aynısını) is read with the family's noun in its place, then with its set
   verb ("alarmı yedi buçuğa al kur"), and taken only if it reaches that family - label
   `context`, which the policy reads as a confusion (MEDIUM, read back). Nothing to point at
   (or "birini"): no act, one short Turkish question in `clarification_question`
   ("Neyi kastettiğinizi söyler misiniz efendim?").
8. `ClientEvent` of kind utterance: blank text or more than 1000 characters (about a minute
   of speech) answers 422 with a Turkish message; other kinds and text=None unchanged.

## Open (follow-ups)

* The household TOOL acts on `household_item` only; `household_items` reaches the relay
  record, but recording the second item needs `tools_household.py` (a card of its own).
* The web controller reports a final transcript even when empty (`controller.ts`
  `onOwnerTranscript`): such a batch now answers 422 and its timing events are lost with it.
  The client should not report a blank utterance (web card).
* `_split` has no length cap (main, flag-independent): a 4000-letter token 9 s. Own card.
* "sütler bitti" / "sut bitti" are not repaired (household plural / ASCII). Own card.
