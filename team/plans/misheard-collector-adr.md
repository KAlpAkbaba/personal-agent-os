# ADR (unnumbered; the lead numbers it): the STT collector reads the misheard notebook

Status: accepted (card misheard-collector, cycle d20261003). Extends ADR-0224 addendum 4 and
ADR-0254.

## Context

`scripts/voice/collect-stt-corpus.ps1` turned a read-only production dump into proposals for
the STT corpus, from two kinds of line: 'session' (a local-mode sentence the router did not
understand, the only place a sentence was kept) and 'audit' (names and numbers, counted). A
paid session kept no sentence, so the tool had never had anything to read from one. The
misheard notebook (ADR-0254, `misheard_utterances`) now keeps the recogniser's written
sentence in both modes for 30 days, and the owner's answer (`meant`) once he gives one.

## Decision

1. **A third line kind.** `-ShowQuery` prints three read-only statements, each one SELECT (the
   session/audit `union all` became two statements); the third emits one json line
   `{kind:'misheard', ...}` per notebook row heard in the last `-Days` days, naming every
   CONTRACT column as the table names it. The PostgreSQL integration test executes the SQL
   text taken from the script's own output, so the script and the table cannot drift.
2. **A 'misheard' line is proposed exactly as a 'session' line**: origin real, its UTC day
   (whatever offset the database session wrote), times_heard, whole-rendering comparison
   (equal to a REAL rendering: skipped and counted; equal to a DERIVED one: proposed with
   `confirms_derived_case`). It also carries mode, engine, device_id, reason,
   resolved_intent, band, confidence. Status is `owner_answered` with `meant` = the owner's
   words letter for letter when the row has an answer, else `needs_owner_meaning`.
3. **The merge rule.** One sentence, in any number of lines of either kind, is ONE proposal:
   times_heard is the sum, the earliest day is kept. An answer is never lost to a line without
   one (answered wins over unanswered). Two different answers for one sentence are both kept -
   the first in `meant`, the rest in `meant_also` - and never merged or chosen between; the
   same answer twice is one. A 'session' proposal a notebook line joins gains the notebook's
   mode / engine / device_id / reason and `meant_also`; a dump with no 'misheard' line gives
   proposals byte-for-byte in the old shape.
4. **The report** gains `misheard: {rows, by_reason, by_mode, answered}` over every notebook
   line read (including those skipped as already in the corpus).

## Why the tool still writes proposals and never the corpus

The owner's answer is his words, not a corpus case: the case needs an intent, entities, an
application and a device, and mapping "Ofis bilgisayarının ekranını kapat" to them is a
judgement a person makes and the owner can confirm. A tool that guessed that mapping would
put its own reading into the measurement it is measured by (ADR-0224's 95% target). So
intent / tool / application / device stay null even for an answered row, and every refusal
stands: never the corpus file, never a `.py`, never over the dump, inside the repository only
under `state/reports` (proposals hold raw sentences, KVKK).
