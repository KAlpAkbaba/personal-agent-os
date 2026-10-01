## ADR (number by lead): a named machine the parser could not bind is asked about, never launched elsewhere

Context: 2026-09-30 20:11 UTC the owner said "Ofis bilgisayarımdan hesap makinesini aç"; the STT
wrote "Ofisü bilgisayarında ... açın." "açın" (polite imperative) was in no open-verb table, so the
router resolved NONE; the model then called `operator.app_open({application})` and the launch ran
on the session's own device (MAIL) with no word about it.

Decision:
1. `açın`, `açınız`, `acın`, `acınız`, `aciniz` join `_OPEN_VERB_FORMS` and `_APP_OPEN_VERB_FORMS`
   (one shared `_POLITE_OPEN_VERB_FORMS`; the other tables - screen/news/routine/bare-title - are
   untouched: no observed need, each has its own false-positive history).
2. Per ADR-0224 `operator.app_open` gets NO device argument. The relay (`service.py`, next to
   `device_targets`) writes a word-free `machine_named_unbound: bool` into `last_utterance`:
   true when the sentence holds a computer word ("bilgisayar...") or "ofis..." and no alias
   bound ("bu/su bilgisayar" excluded: that is this machine). The owner's sentence is NEVER
   stored (`chat_question` is local-only; the status read exposes `last_utterance`). When the
   flag is true the tool dispatches nothing and asks one question listing the enrolled aliases.
   A bound alias keeps the existing path (selection honours the named device; the direct-launch
   speech names it as "Ofis cihazinda ...").
3. Pinning: the mutation unit for the polite verbs is the whole `_POLITE_OPEN_VERB_FORMS` table.
   `acin` is the folded form of `açın`, so dropping `açın` alone is behaviourally identical
   (the router folds before matching); the table-wide mutation is RED (5 tests).
