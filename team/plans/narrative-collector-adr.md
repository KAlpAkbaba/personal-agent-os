## ADR — The narrative is collected from the ledger, told by a Protocol, and audited before it is spoken

**Decision.** `app/narrative` = collector (pure facts from `ledger.service.query`, read-only) →
`Narrator` Protocol (`RuleNarrator` today; a model narrator later behind the same Protocol) →
auditor (`audit`/`repair`). `tell()` runs all four; a narrative that still fails the audit after
repair (an invented number, a skipped subsystem) is replaced by the rule text, which is built
from the facts alone.

**Choices (reversible).**
- The ledger has no device column. The device is read from `detail_json["device"]`; a row naming
  none is a cloud row (`bulut`). No writer sets it yet - device filtering is live only once the
  writers do (next task). A device word matching nothing selects nothing (never everything).
- Windows are UTC, start inclusive / end exclusive: 'bu hafta' = last 7 days to now, 'bugün' =
  midnight..now, 'dün' = the previous UTC day. Other words raise `ValueError`.
- The auditor's "numbers that are facts" = the counts in the facts plus digits inside failure
  summaries/reasons (a failure "3 kaynak reddedildi" must be speakable). A failure counts as
  mentioned when every word of its summary (length >= 3) is in the text.
- Only `failed`/`completed` rows are narrated; `started`/`skipped`/`info` are not.
- Paging: `query` is capped at 200; the collector walks back by timestamp. More than 200 rows
  sharing one identical timestamp would be cut short (documented limit).

**Why.** ADR-0201 made the ledger the record of what the system did; a spoken summary must not be
able to hide a failure, whichever narrator writes it.
