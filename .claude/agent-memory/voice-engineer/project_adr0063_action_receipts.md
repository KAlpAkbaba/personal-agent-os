---
name: adr0063-action-receipts-state
description: M18 action grounding (ADR-0063 + same-day amendment, 2026-09-06) on the Cloud Core; receipt shape, what the web half must match, and what still awaits the owner's re-run
metadata:
  type: project
---

ADR-0063 (WRITE -> READ-BACK -> SPEAK) is implemented on the Cloud Core side of
`docs/M18_ACTION_CONTRACT.md` as of 2026-09-06: tools `state.now`, `eye.enable`,
`eye.disable`, `release.promote`; `ResolvedIntent.klass/capability`; `eye_state` query kind;
`app/actions/receipt.py`, `app/state/now.py`, `app/state/routes.py` (`GET /v1/state/now`),
`app/voice/realtime_sessions/actions.py`. Amended the same day (items 7-9) after owner
session 3eb6fee7: no utterance safety net, receipts carry `session_id` / `observed_at` /
`action_trace`, client error classes each have a sentence, physically-done-but-unverified
gets its own truthful sentence. See [[m18-eye-action-seam]] for the seam details.

**Why:** the owner's real M18 run had "Gözünü kapat." acknowledged with "öyle olmuş gibi
düşün" (no eye tool existed, a hidden hook did the write), "Gözünü aç" did nothing, a
current-state question answered with counts of truth kinds, and later a camera the browser
HAD closed narrated as "kapatamadım". The contract is binding for both halves.

**How to apply:** any new mutating voice capability must return an `ActionReceipt`
(`terminal_status` verified|already|unverified|failed; the harness reads `verified`) and
speak only from the read-back; the ledger row is `action.receipt` with status = terminal
status and `detail_json.session_id` for correlation. One writer per capability, ever.
Current-state questions go to the live composer, never the ledger. Enable sets the durable
eye flag ONLY when the client reports `local.state == "ACTIVE"` with the track not `ended`.
Pending: the owner's re-run of the Eye/Voice qualification (the integrator's harness in
`scripts/core/`) against the merged halves - validated against the simulator only.
