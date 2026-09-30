## ADR (number by lead): a named machine the parser could not bind is asked about, never launched elsewhere

Context: 2026-09-30 20:11 UTC the owner said "Ofis bilgisayarımdan hesap makinesini aç"; the STT
wrote "Ofisü bilgisayarında ... açın." "açın" (polite imperative) was in no open-verb table, so the
router resolved NONE; the model then called `operator.app_open({application})` and the launch ran
on the session's own device (MAIL) with no word about it.

Decision:
1. `açın`, `açınız`, `acın`, `acınız`, `aciniz` join `_OPEN_VERB_FORMS` and `_APP_OPEN_VERB_FORMS`
   (one shared `_POLITE_OPEN_VERB_FORMS`; the other tables - screen/news/routine/bare-title - are
   untouched: no observed need, each has its own false-positive history).
2. Per ADR-0224 `operator.app_open` gets NO device argument. When the turn record's
   `utterance_text` holds a computer word ("bilgisayar…", or "ofis…") and `device_targets` is
   empty ("bu/şu bilgisayar" excluded: that is this machine), the tool dispatches nothing and
   answers one question listing the enrolled device aliases. A bound alias keeps the existing
   path (selection honours the named device; the direct-launch speech names it).

Consequence / OPEN: the turn record (`service.py` `ctx["last_utterance"]`) does not carry the
owner's words today (it is deliberately word-free). The tool reads `utterance_text`; the one-line
writer in `service.py` (outside this task's area) must set it, bounded (<= 300 chars), or the
guard never fires in production. Until then behaviour is unchanged.
