# ADR draft: a held duty return is read back from the store (duty-waits-survive-restart)

Status: proposed (worker, cycle d20261006)

## Context

When the Proje Yöneticisi returns a stopped task but another task in work holds the same files,
the cycle keeps the task `stopped` with the reason `<return prefix><reason> (alan çakışması: <ids>; o iş bitince)`
and remembers the hold in `$dutyWaits`, which `Resolve-DutyWaits` applies when the holder leaves the work.
`$dutyWaits` lived only in the cycle's memory. On 2026-10-06 the cycle restarted at 15:00, the hold
of conversation-followups (holder money-ledger) was lost, and nothing would have reopened it: the owner
saw a worker waiting for ever.

## Decision

The store is the hold's record. `Import-DutyWaits` (scripts/team/cycle.ps1) runs at the start of every
`Resolve-DutyWaits`, i.e. after every store re-read (`Sync-Queue`) in a refill, including the first one of a
fresh cycle. Every `stopped` task whose reason matches
`\A<returned prefix>.* \(alan çakışması: <ids>; o iş bitince\)\z` becomes a wait: its area the task's area,
its reason the text before the hold suffix, its stamp the task's `updated_at`. An existing in-memory wait
with the same stamp is kept as is (its area may carry a grant not yet on the task).

The holders named in the text are not trusted: `Get-DutyReturnBlock` asks the queue again, so a holder
merged, released or gone frees the return at once, and a new holder of the same files keeps it held.
A stopped task without the prefix or without the suffix is not a hold and goes to the duty as before.

## Consequences

- A cycle restart no longer loses a held return; no owner prompt is needed.
- A held return read from the store is also kept out of the duty hand-over (`$dutyWaits` keys are skipped),
  so the Proje Yöneticisi is not asked twice about it.
- Known limit: a `grant_and_return` grant is written to the task's area only when the return is made; a hold
  read back after a restart uses the task's stored area, so a grant made just before a restart is lost and the
  worker may come back with `ALAN_ISTEGI` again (one more duty round, never a wrong move).
