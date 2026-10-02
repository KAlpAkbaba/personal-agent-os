## ADR (lead numbers it) — ADR-0224, the gap of addendum 4, step 1: the rule tables read layer 1's reading (polite forms for every verb table, a fused token split) — 68.9 % -> 92.5 %, target still NOT met

**Status.** Built (worker, cycle d20261002). The owner's target (>= 95 % on sentences as the STT
renders them) is **still not met**: 98 of 106 = **92.5 %**. This step's own bar (>= 90 %, polite
29/29, fused >= 24/29, 0 wrong-device) is met.

**The number (2026-10-02, branch on main e1543a97, layers 1-3, no layer-2 engine in the harness).**

| | 2026-10-01 | 2026-10-02 |
|---|---|---|
| correct | 73 / 106 = 68.9 % | **98 / 106 = 92.45 %** (97 acted + 1 question) |
| polite | 16 / 29 | **29 / 29** |
| fused | 12 / 29 | **24 / 29** |
| diacritics | 24 / 24 | 24 / 24 |
| invented suffix | 18 / 21 | 18 / 21 (not this step) |
| real sentences | 3 / 3 | 3 / 3 |
| wrong-device actions | 0 (11 observable) | 0 (11 observable) |
| confident wrong readings | 8 | 6 |
| not understood | 25 | 2 |

`KNOWN_GAPS` lost 25 cases and gained none (33 -> 8). The Owner Utterance Suite, run again after
the second pass (decision 2's owned-sentence rule) in ONE process on the committed sources
(`intents.py` sha256 `d9c42028…`, `normalize.py` `3f582858…`): **2754 / 2754** (2756 tests
passed, 0 failed, 22 min 24 s on a busy machine). The STT numbers above did not move with the
second pass. About 300 of the owner sentences carry a polite form layer 1 now reads; none
changed its route. Third pass (the "don't" guard without punctuation, the two-ways test), on
main 65cd94ff merged in, `normalize.py` sha256 `b50c7ed7…`, `intents.py` unchanged `d9c42028…`:
Owner Utterance Suite **2754 / 2754** (2756 passed, 0 failed, 20 min 20 s, TMP/TEMP on an empty
E: folder); STT report unchanged (98/106 = 0.9245, polite 29/29, fused 24/29, 0 wrong-device).

**Decision.**

1. **One mechanism, in `resolve_intent`, for every table** (`intents._layer_one_route`). Layer 1
   gained `normalize.lemma_reading(text)`: the owner's sentence with exactly two kinds of rewrite -
   a polite form of a verb layer 1 knows written as its bare imperative ("kapatın", "kapatınız",
   "kapatsana", "kapatır mısın(ız)", "kapatabilir misin(iz)" -> "kapat"), and a fused token written
   as its two words. That text goes through the very same `_resolve_intent_rules`. No table gained
   a form; a test holds that "kapatın", "bakın", "bulun", "yapın", "kurun" are in no table.
2. **Who wins.** The words as heard are resolved first, as always.
   - Every polite form in the sentence is one a table LISTS ("okuyun" in the research-read table
     since ADR-0184, "açın" since ADR-0233) and a table owns the sentence: exact closed form, 1.0,
     nothing re-read.
   - A polite form no table lists: the imperative reading decides, at the suffix-dropped
     confidence (0.9, `route_repair="polite"`, which the relay's `policy.rule_reading` already
     maps to `MATCH_SUFFIX_DROPPED`). Where the words as heard reached the SAME intent (a stem
     table read "yazar mısın" by its prefix), the surface reading is returned - the owner's slots
     are untouched ("Şuraya ışıkları söndürün yazar mısın?" types "ışıkları söndürün", not layer
     1's rewrite of it), only the confidence says a suffix was dropped.
   - **Where the words as heard reached ANOTHER intent, the table that owns the sentence keeps
     it, exactly as heard (1.0, no repair) - polite form or fused word alike.** The first build
     said "they reached it without the verb, so the imperative wins"; that premise is false for
     dictated content and was a regression against main (inspector, 2026-10-02): "Şunu hatırla:
     ışıkları söndürün." went from `memory_remember` to `window_close` at 0.9, "Şunu yaz: sabah
     alarmı kurun." from `type_text` to `alarm_create`, "Yarın bana hatırlat: müziği durdurun."
     from `memory_remember` to `stop`. The premise is now CHECKED instead of assumed
     (`intents._asks_with_no_verb_of_its_own`); the imperative takes an owned sentence only when
     all three hold: (a) the words as heard were read as a QUESTION (class `query`) - a table
     that read a command keeps its sentence; (b) the sentence is one clause (no `, ; : . ! ?`
     with words after it); (c) no word but the polite form is a verb - neither a form a router
     table lists nor a form of a verb layer 1 knows. That leaves "Kendi kendini geliştirmeyi
     duraklatın." (`explain` by the noun alone -> `evolution_pause`, 0.9) and nothing else in
     either corpus. Each of the three guards has its own sentence and its own RED mutation.
   - A split reading of the SAME intent, or of a sentence no table owns, is returned whole
     (`route_repair="fused"`, `confidence` 0.75 - a repaired word, the confidence of a
     confusion): the surface slots were read off the fused token.
   - **Not covered, and not new:** a sentence NO table owns as heard is read as its bare
     imperative is, the bare rule's own weakness included - "Şunu not et: ekranları kapatın." is
     `display_off` at 0.9 because "Şunu not et: ekranları kapat." is `display_off` on main;
     likewise "Ekranları kapatın demedim.". Dictation after a verb no table knows is a gap of
     the tables, not of this mechanism.
3. **The fused split** (`normalize._split`, applied in `normalize()` too and recorded as
   `Normalized.applied_splits`, like a confusion): a token is split when, and only when, it is two
   words layer 1 knows (a stem, a stem with its suffix chain, or one of the closed `_WORDS`).
   Never: a token that is itself a known word ("bugün", "bugünün", "masaüstünde"); a verb as the
   first half ("silver" is not sil + ver); a negative form as a half; a half under two letters;
   a second split; a token that divides two ways ("masaüstümüziki" is masaüstü + müziki and
   masaüstümüz + iki: left whole - held by `test_a_token_that_divides_two_ways_is_left_whole`,
   added in the third pass after inspector-2 found the claim untested).
4. **A negative never becomes its positive.** The grammar has no negative chain, so "kapatma" and
   "unutma" keep their surface (unchanged). New, and explicit: `lemma_reading` returns None for a
   sentence that carries a negative imperative of a known verb (`_says_dont`), so a polite clause
   beside a "don't" is not turned into the action. **Corrected in the third pass:** the bare form
   ("kapatma") is ALSO the verbal noun, and the first build counted it as "don't" only at the end
   of a clause or before punctuation - but Chrome Web Speech (ADR-0173) writes no punctuation, so
   "Ekranları kapatma ışıkları söndürün" read as `display_off` at 0.9 (inspector-2: 9 of 25
   unpunctuated probes flipped, acting at HIGH). Now a bare negative before another word says
   "don't" UNLESS that word proves it the verbal noun: the next word, with nothing between them, is
   a compound head (`normalize._is_compound_head`) - a known noun, not a verb, whose EVERY reading
   carries a possessive ("indirme klasörünü gösterin": klasör + poss3sg/poss2sg + acc). "ışıkları",
   "ama", "hatırlatma", "kurun", a bare noun, a word layer 1 does not know - none proves it, so
   the sentence gets no reading and is resolved as heard. Erring this way costs only the reading
   (the surface tables still run); the corpora did not move (STT 98/106 unchanged; owner corpus
   below). The sentences of the finding are router tests (`test_a_dont_without_punctuation_still_says_dont`).
   Not closed here, and not new: their BARE twins ("Ekranları kapatma ışıkları söndür") are
   resolved by the surface tables as on main.
5. **Mail and calendar.** The older repairs never route into `mail_*` / `calendar_*` (B45/B46).
   This reading is narrower, not wider, about ACTIONS - "Gönderir misin?", "Gönderin." after a
   read-back are still not a send, and a sentence a mail/calendar table already owns is never
   re-read (not even its confidence moves: "Bunu bir saat erteleyin." with an event in focus,
   "Son maili okur musun?" - held by a test since the second pass) - but it does route into the QUERY class of those families: "Maillerime bakın." ->
   `mail_inbox`, "Fatura maillerini bulun." -> `mail_search`. Reading mail changes nothing the owner
   can see (the table's own comment at `QUERY_TOOL_BY_INTENT`), and the card's polite 29/29 cannot
   be reached without these two. **This is the one judgement the lead should look at.**
6. **Vocabulary.** `_VERBS` is unchanged (it also feeds layer 3's negation cap, which this task
   must not move). `_TABLE_VERBS` (28) adds the verbs of the router's exact-form tables so the
   mechanism covers them ("açıkla" joined in the second pass: the router reads it inline, and
   guard (c) above must know it is a verb - "Şunu açıkla gözünü kapatınız"); left out on purpose: mutating stems (et, kaydet, git) and verbs whose
   polite form is a common word (alın, basın, kesin, koyun, sayın). `_WORDS` (28: determiners,
   pronouns, small numbers) and six nouns (göz, hareket, teknik, gün, bugün, buçuk) exist for the
   split. **Honest note:** these words were chosen knowing the corpus; the corpus cannot be tuned,
   the vocabulary can. What keeps it from being a fit: every sentence of the Owner Utterance Suite
   (2754) is run through the splitter in a unit test and none may split.

**Two things on the card that do not hold as written.**
- "'Raporu okuyun.' and 'Alarmı kurar mısınız?' fall through": they did not (measured on e1543a97:
  `research_open` and `alarm_create`, 1.0). "Raporu okuyun." is an exact listed form and stays 1.0
  - the card's own rule ("the surface form still wins where a table matches it exactly") against
  the card's acceptance line (0.9 for this sentence); the rule was followed. "Alarmı kurar
  mısınız?" is 0.9 now (no table lists the question form).
- "the split is recorded like a confusion": it is recorded, and `ResolvedIntent.confidence` is
  0.75; but the relay's `policy.rule_reading` (outside this area) maps every `route_repair` to 0.9,
  so a split sentence is acted on at HIGH, not read back. One line there (fused -> `MATCH_CONFUSION`)
  makes it MEDIUM. Not done: layer 3 is out of the area.

**What is left (8 cases, all in `KNOWN_GAPS`).**
- 3 invented suffix + 2 fused read as ANOTHER intent at HIGH ("Hesapü makinesini aç." -> media;
  "Şubug'ı kendin düzelt." -> memory_correct; "Buresmi Paint'te yeniden çiz." -> repeat): an exact
  rule that nothing may contest - `understanding-confident-wrong`.
- 2 fused with a half layer 1 does not know ("Uyurkenekranları", "Faturamaillerini"): a free word
  is not vocabulary; this is layer 2's (the semantic reading).
- "Saat yedibuçukta beni uyandır.": the router now reads it as the two-word sentence; the alarm
  TOOL parses the time from the sentence as heard and refuses (`when_unparsed`). The fix is in the
  relay/tool (hand the tool the reading), outside this area.

**For the lead at merge.** `tests/unit/test_stt_utterance_corpus.py` (outside the area) still says
"73/106 = 68.9 %" in the strict-xfail reason; the xfail itself stays correct (92.5 % < 95 %).
`policy._negative_forms` could import `normalize.is_negative` instead of its own copy.
