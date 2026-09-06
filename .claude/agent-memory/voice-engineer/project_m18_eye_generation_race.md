---
name: m18-eye-generation-race
description: M18 web eye store (2026-09-06, session 9df439af fix) - transitions are generation-owned (`gen:N` first trace stage), the bus-driven stop is `stopLocalIfStale(bus)` gated by the event's date (never during ENABLING), `stopLocalOnly` is gone; trace layout gen/request/action/track; identity fields sent only when both present
metadata:
  type: project
---

After the ADR-0063 web half, the owner's run 9df439af (14:31Z) showed every SECOND
enable failing `state_mismatch` with trace `request:stop_local > superseded:stop >
state:ENABLING->DISABLED`: `EyeControl`'s effect re-ran on `status.running` (the loop
starts BEFORE the durable enable) while the page still held the previous command's
bus `eye.disabled`, and `stopLocalOnly()` obeyed it, superseding the enable and
erasing its trace. Fixed in `apps/web` only (commit "generation-owned eye
transitions"): `EyeStore` gives every transition a generation, late callbacks of an
older generation are `ignored:gen<N>` on the current trace, and the bus-driven stop
became `stopLocalIfStale({status, ageMs, expired})` -> pure
`shouldStopLocalPerception(eye, {state, activeSince, durableEnabledAt}, now)`: only
from ACTIVE, only for an unexpired dated event strictly newer than the ACTIVE commit
and the durable-enable ack. `EyeControl`'s effect depends on the bus view only.

**Why:** the owner's rule - a STALE external state must never cancel a NEWER local
transition, and an enable must never contain `request:stop_local`. `ageMs` is
client-now minus server-`at`, so the comparison is cross-clock; the 409 self-stop in
`PerceptionSession` remains the safety mechanism, this is the fast path.

**How to apply:** trace layout is now `gen:N, request:<x>, action:<call_id>?,
... , track:<8 chars of MediaStreamTrack.id>` (open and stop) - MAX_ACTION_TRACE
stays 12, so a real enable with identity is 11 stages; do not add stages lightly.
`enableEye/disableEye(reason, {action_id, session_id})` send the two ids ONLY when both
are non-empty and <= 64 chars (server `EyeActionRequest` was `extra="forbid"`; the
server side accepting them was being added in parallel - verify before relying on it).
`docs/M18_ACTION_CONTRACT.md` (~line 318) still names `stopLocalOnly()`; a docs pass is
owed. Two pnpm gotchas remain in [[worktree-pnpm-gates]]. See [[m18-eye-action-seam]].
