---
name: m18-eye-action-seam
description: M18 eye actions (2026-09-06) - the tool call is the ONLY mutation path (utterance safety net removed after owner session 3eb6fee7); the web EyeStore does its own durable POST first, so verified-vs-already rests on local.changed; receipts carry session_id/observed_at/action_trace
metadata:
  type: project
---

The web half of `docs/M18_ACTION_CONTRACT.md` §7 has `EyeStore.enable/disable` do the
durable `POST /v1/presence/eye/enable|disable` themselves on BOTH paths (owner button and
voice tool, reason `voice:<utterance>`), then relay the tool call with
`arguments.observed_after = {local: {state, running, camera_label, error_class,
observed_at, changed, media_track_ready_state, action_trace}}`. The server handler's own
write is therefore an idempotent no-op; "changed in this command" = handler write returned
True OR `local.changed` is true. Do not fix it by sniffing the reason prefix in `EyeStore`.

**Why (the second defect, same day):** the first server build ALSO kept an utterance hook
in `record_client_events` that wrote `disable_eye` on any `EYE_DISABLE` utterance, plus an
`eye_safety` context note the handler read to call a same-turn "already" a "verified". In
owner session 3eb6fee7 (13:57Z) the client relayed every tool call without
`observed_after` (vendor-name bug, fixed on the client), every receipt was
`capability_missing`/`failed`, and the hook still closed the camera 7 ms after the tool
call with no receipt. The owner's rule: exactly ONE mutation path per capability, the one
that ends in a receipt. The hook, `eye_safety` and the 30 s window are gone (contract §5.3
rewritten, ADR-0063 amended items 7-9).

**How to apply:** never add a second writer for a capability "for safety"; if the model
does not call the tool, fix the persona (`ACTION_GROUNDING_TR` now says the eye changes
ONLY through the tools). Speech is truthful in both directions: below `verified` never
"kapattım/açtım"; when `local.state` matches the request but the record is unverified
(write raised / read-back mismatch) it is "Kamera kapandı/açıldı ancak işlem kaydını
doğrulayamadım.", never "kapatamadım". An enable relayed ACTIVE with
`media_track_ready_state == "ended"` is `failed`/`stream_created_but_track_ended` and
never sets the flag. `GET /v1/state/now?scope=` mirrors the `state.now` tool for the
harness. Also known: a durable enable failure AFTER the camera opened is deliberately NOT
`ERROR` in the store (the camera is honestly open; the loop self-corrects on the next 409).
