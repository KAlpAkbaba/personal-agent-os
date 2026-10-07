# ADR draft: the protected-path list in its own file (protected-list-own-file, 2026-10-07)

## Context
`scripts/lib/TeamArea.ps1` held both the protected-path list (`$script:TeamAreaProtected`) and the
team's ordinary area logic (area keys, overlap, request resolution, changed-file helpers). The list
protected itself by protecting the WHOLE file, so every card fixing area logic could not be granted
by the duty run (`Test-TeamDuty` refuses lead-protected grants) and escalated to the Danışman, a chat
session nobody runs at night (area-check-wire-integrate-base, 2026-10-07 06:02, and the two cards
that depend on it).

## Decision
- `New-TeamAreaProtectedEntry` and `$script:TeamAreaProtected` move unchanged into
  `scripts/lib/TeamAreaProtected.ps1` (UTF-8 BOM, CRLF), dot-sourced by `TeamArea.ps1` after
  `TeamQueue.ps1`.
- The self-entry protects `scripts/lib/TeamAreaProtected.ps1`; the `scripts/lib/TeamArea.ps1` entry is
  gone. Every other entry and `Get-TeamAreaProtection`'s matching are unchanged.
- Fail-closed when the list file is missing: `Get-TeamAreaProtection` is not defined, so
  `Test-TeamDuty` refuses every decision (its existing "not loaded" check) and
  `Resolve-TeamAreaRequest` refuses every request with a stated reason.

## Rejected alternative
Let the lead (the duty run) grant protected paths, or a whitelist exception for `TeamArea.ps1` inside
`Test-TeamDuty`. Rejected: the protected list exists precisely so no request - the lead's included -
can shorten it at night without the owner/Danışman; an exception would be a second rule that drifts
from the list. Splitting the file keeps one list and one rule, and narrows the protected surface to
what actually needs it.

## Follow-ups (outside this card's area)
- `scripts/tests/team-cycle.tests.ps1:521` expects `TeamArea.ps1` protected -> `TeamAreaProtected.ps1`;
  `:1282` sandbox must also copy `TeamAreaProtected.ps1` (otherwise its judge refuses everything).
- `scripts/lib/TeamRun.ps1:292` PM prompt names `TeamArea.ps1` as protected -> `TeamAreaProtected.ps1`.
- `scripts/lib/TeamQueue.ps1:816` refusal message names `TeamArea.ps1` -> `TeamAreaProtected.ps1`.
