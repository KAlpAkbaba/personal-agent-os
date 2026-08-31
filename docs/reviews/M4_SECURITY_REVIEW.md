# M4 Security Review — 2026-08-31

Independent review of the M4 voice + narration additions. Companion verification: independent test-engineer reproduced all 9 M4 acceptance criteria PASS. **No Critical findings; one High (forward-looking).**

## Findings and dispositions

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | High (forward-looking) | Speaker verification's device-trust second factor was a client-asserted boolean in the request body; per-call threshold overrides let a caller widen the accept/reject band; re-enrollment overwrote the owner profile with no owner-auth check. Not live-exploitable today (no endpoint gates a privileged action; the API has no per-request auth layer yet — a pre-existing, tracked deferral), but must be hardened before speaker verification gates anything. | **Partially fixed at M4 + tracked hard-gate.** Removed the per-call threshold override (the accept/reject band is now server-config only; regression test). Bounded embedding dims/sample counts. The core — deriving `device_trusted` server-side from an authenticated enrolled-device/broker session, and protecting re-enrollment — depends on the API authentication/identity layer that does not yet exist and is **a hard gate before speaker verification is wired to any privileged/authorization action** (tracked in BUILD_STATE owner-actions, alongside the M1 Session-0 pipe and M2 registry-ACL forward gates). The pure classifier already caps an untrusted device at UNCERTAIN (voice never the sole secret), tested at exact thresholds. |
| 2 | Low | Dead CHECK-constraint helper code in migration 0004 (`_SPEAKER_DECISIONS`/`_in_list`) never applied. | **Fixed at M4**: removed; migration round-trip re-verified. |
| 3 | Low | `PATCH /narration/sessions/{id}/cursor` accepted an arbitrary `state` string, later 500-ing `/command` via an unhandled `ValueError`. | **Fixed at M4**: `state` validated against `NARRATION_STATES` at the schema (422, not 500); regression test. |
| 4 | Low | Unbounded `utterance` / embedding inputs on some voice+narration routes. | **Fixed at M4**: `utterance` ≤2000, `command` ≤64, embedding dims ≤4096, enroll samples ≤64; regression tests. |

## Constitution voice-privacy verdict

"No raw audio persisted" and "encrypted derived profile" (Fernet, key from a configured secret; DB stores only an object-store `embedding_ref`) **are enforced in code today** and integration-tested (round-trip, wrong-secret rejection). "Voice never the sole secret" is enforced inside the pure classifier (untrusted device caps at UNCERTAIN, tested at exact boundaries); the end-to-end enforcement (real server-side device-trust derivation + protected re-enrollment) is the High-finding hard gate above, to be resolved with the API auth layer before speaker verification gates a real action.

## Clean areas (verified)

No raw audio accepted (float embeddings only); real provider adapters inert without keys with no key leakage in logs/URLs/errors; test HTTP fully mocked (no real network); normalizer regex pipeline has no catastrophic-backtracking risk and dict tokens are `re.escape`d; command parser is a pure fixed-enum mapper ("dur" always wins, speed clamped); object-store keys derived server-side and `validate_object_key`-checked; migration 0004 reversible with no raw-audio columns; no new tracked secrets, no non-loopback binds, CI untouched.
