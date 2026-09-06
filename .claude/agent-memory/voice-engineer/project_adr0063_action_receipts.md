---
name: adr0063-action-receipts-state
description: M18 action grounding (ADR-0063, 2026-09-06) shipped on the Cloud Core; what the web half must match and what is still pending an owner re-run
metadata:
  type: project
---

ADR-0063 (WRITE -> READ-BACK -> SPEAK) is implemented on the Cloud Core side of
`docs/M18_ACTION_CONTRACT.md` as of 2026-09-06: tools `state.now`, `eye.enable`,
`eye.disable`, `release.promote`; `ResolvedIntent.klass/capability`; `eye_state` query kind;
`app/actions/receipt.py`, `app/state/now.py`, `app/voice/realtime_sessions/actions.py`.

**Why:** the owner's real M18 run had two defects - "Gözünü kapat." was acknowledged with
"öyle olmuş gibi düşün" (no eye tool existed, the safety net did the write), "Gözünü aç"
did nothing, and "Kendi sisteminde şu anda ne görüyorsun?" answered with counts of truth
kinds. The contract is binding for both halves; the web half (`apps/web` EyeStore,
`observed_after` relay) was built in parallel by another agent against the same document.

**How to apply:** any new mutating voice capability must return an `ActionReceipt`
(`terminal_status` verified|already|unverified|failed) and speak only from the read-back;
the ledger row is `action.receipt` with status = terminal status. Current-state questions
go to the live composer, never the ledger. Enable sets the durable eye flag ONLY when the
client reports `observed_after.local.state == "ACTIVE"`; a relay without `observed_after`
is `capability_missing`. Pending: the owner's re-run of the Eye/Voice qualification (the
integrator's harness in `scripts/core/`) against the merged halves - nothing here has been
validated against the real realtime model yet, only against the simulator.
