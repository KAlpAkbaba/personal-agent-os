---
name: feedback-intents-prefix-collision-risk
description: app/voice/intents.py's _has() helper matches by TOKEN PREFIX, not stem — a new short vocabulary word can silently collide with an existing one that happens to share a prefix (found via "konu" vs "konum"); always grep existing _has(...) stem tuples for a prefix collision before adding a new short noun stem, and prefer _has_exact with explicit inflected forms for anything under ~5 letters
metadata:
  type: feedback
---

Building the Owner Location Context / Live Weather / Morning Briefing capability
(docs/DECISIONS.md ADR-0090, 2026-09-09) surfaced a real routing bug this way: adding the
word "konum" (location) as a new stem collided with the PRE-EXISTING mail vocabulary stem
"konu" (subject/topic), because `_has(tokens, *stems)` in `app/voice/intents.py` matches
any token that **starts with** the stem, not an exact word or a real linguistic stem
boundary. `"konumumu".startswith("konu")` is `True`, so "Varsayılan hava durumu konumumu
İstanbul yap." was being swallowed by `_mail_edit_draft_match`'s "konuyu ... yap" shape
(checked earlier in the router's priority order) and never reached the new intent at all.
The same class of bug also lurks the other direction: "konu" also matches "konuş-" (to
speak).

**Why this recurs:** `_has()` is deliberately loose so Turkish suffixes ("maddeyi" /
"maddeye" / "maddeden") don't need enumerating — but that looseness has no lower bound on
stem length, so any new stem under ~5-6 letters has a real chance of prefix-colliding with
an unrelated existing word. This is the SAME class of defect ADR-0089 addendum 1 already
paid for once with "taslağı" (a diacritic-stripped ASR variant matching neither of two
word-form stems), just on the "too broad" side rather than the "too narrow" side.

**How to apply:** before adding a new short `_has(tokens, "X")`-style stem anywhere in
`app/voice/intents.py`:
1. `grep -n '"X' app/voice/intents.py` for every existing stem tuple that shares the same
   first 3-4 letters.
2. If a collision risk exists, use `_has_exact(tokens, *forms)` with the actual closed set
   of inflected forms the word takes (e.g. `("konu", "konuyu", "konusu", "konusunu")`)
   instead of a bare prefix — narrower, but correct.
3. Run the FULL voice corpus after the change
   (`tests/unit/test_owner_utterance_corpus.py`, ~13 min — see
   [[feedback_test_suite_speed_and_tooling]]), not just the new category: a prefix
   collision shows up as an EXISTING case's intent changing, which only a full run (or a
   targeted run of the OTHER family's own corpus category/unit tests) catches.

A second, related lesson from the same session: broadening an existing matcher's accepted
question-form set (adding a new trigger word) can just as easily collide with an
ALREADY-CONTRACTED phrase elsewhere in `test_voice_intents.py`'s own table — e.g. adding
"durumda" as a system-status question form also matched the pre-existing
"Sistemin şu anda ne durumda?" (EXPLAIN/`world_state`) case, because both utterances share
a token starting with "durum". The fix there was the same shape: require the actual NOUN
("durumu") in addition to a question form, rather than treating a single shared substring
as sufficient.
