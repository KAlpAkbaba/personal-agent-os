# ADR text for the lead: ADR-0214 addendum (model-policy-floor)

**Context.** The inspection of model-policy-cycle (ADR-0214 addendum 10) left two findings.
(1) The inspector's floor was the model of the task's LAST finished worker run: a branch worked
on Fable, returned, and reworked on Sonnet (lowered by the chain) was inspected on Sonnet and
merged - a weaker judge approved work written mostly by the stronger model. (2) The usage limit
was read from any occurrence of its words in a failed run's result text, stderr or the first
2000 characters of a prose stream: a run that failed for another reason and merely QUOTED them
(a test's output, a report about limits) was read as limited, its model barred for the cycle and
the task lowered with 'model düşürüldü'.

**Decision.**
1. The floor (`Get-TeamInspectionFloor`) is the STRONGEST model among the task's finished worker
   runs (`Get-TeamWorkerModel`, ranked by the chain). An entry that names no model ("tamam", a
   run from before the policy) counts as the configured worker model; no entry at all is the
   configured worker model, as before. When no model at least that strong is open, the
   inspection waits, as before.
2. The limit is read only from the tool's own error shape: a `rate_limit_event` with status
   `rejected`; an error result (`is_error: true`) whose text's first non-blank line starts (leading
   blanks and blank lines allowed, as for stderr) with the tool's limit sentence (`You've hit your … limit`, `You're out of extra
   usage / usage credits`, `[Claude AI ]usage limit reached`); or stderr's first line being it.
   A result that is not an error, prose with no result document, and the words anywhere later
   in a text are a plain failure. The reset epoch and the limit's scope are read from that
   sentence only.

**Consequences.** A reworked task can wait longer for its inspection when the strong model is
limited (accepted: the owner's rule). A real limit printed only as prose with no document and
nothing on stderr is now a plain failure; the integrator's probe found the tool always prints
the result document (stream-json) and an empty stderr, so no real shape is lost.

**Evidence.** scripts/tests/team-cycle.tests.ps1, four tests (two unit, two cycle-with-fake),
RED on the base, GREEN with the change; mutations "strongest → last" and "start anchor removed"
each RED. PROVEN_AUTOMATED (fakes).
