---
name: m18-eye-action-seam
description: M18 eye actions (2026-09-06) - the web client's EyeStore does the durable POST eye/enable|disable itself with reason voice:<utterance>, so the server's eye.enable/eye.disable handler sees an idempotent no-op; verified-vs-already must account for that
metadata:
  type: project
---

The web half of `docs/M18_ACTION_CONTRACT.md` §7 (shipped 2026-09-06, worktree
agent-a1d53022368435ba7) has `EyeStore.enable/disable` do the durable
`POST /v1/presence/eye/enable|disable` themselves on BOTH paths: the owner button
(`owner_start`/`owner_stop`) and the voice tool (`voice:<utterance>`, truncated to the
route's 200-char `reason`). The voice path then relays the tool call with
`arguments.observed_after = {local: {state, running, camera_label, error_class, observed_at}}`.

**Why:** the integrator asked for exactly this ("the durable calls are the existing
enableEye/disableEye; reason strings pass through"), and it keeps one code path for the
button and the voice. But §5.2's server handler ALSO writes the flag and computes
`terminal_status: verified` only when "the state changed in this command". With the
client's write landing first, the handler's own `enable_eye`/`disable_eye` returns False
(idempotent, §5.4) and a literal implementation would answer `already` ("Gözüm zaten açık
efendim") after a real enable - the very ungrounded-speech class the contract exists to fix.

**How to apply:** when touching the server handler or the harness, make "changed in this
command" include a same-turn durable write whose reason is `voice:<utterance>` (ledger
row or turn-start snapshot). If the server side cannot, the client-side alternative is a
one-line change in `apps/web/app/lib/eye/local-actions.ts` (skip the durable call on the
voice path and let the handler own the write); do not fix it by sniffing the reason
prefix inside `EyeStore`. Also known: a durable enable failure AFTER the camera opened
is deliberately NOT `ERROR` in the store (the camera is honestly open; the loop
self-corrects on the next 409) - see the store's test for it before "fixing" that.
