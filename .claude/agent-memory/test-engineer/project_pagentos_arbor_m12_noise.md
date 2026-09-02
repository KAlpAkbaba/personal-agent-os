---
name: project-pagentos-arbor-m12-noise
description: Verification outcome for ADR-0043 (Arbor voice profile) and ADR-0044 (mic noise pipeline) at commits a969375/f34cfd6
metadata:
  type: project
---

Verified 2026-09-02 at commits a969375 ("Arbor as the owner's perceptual voice
profile") and f34cfd6 ("layered microphone pipeline"). Both PROVEN across
server (services/api, 1731 unit tests) and client (apps/web, 70 vitest tests,
oxlint clean, next build clean) with mutation-probe methodology, not just
reading the tests.

**Server (ADR-0043):** SUPPORTED_VOICES pinned list, `require_supported_voice`
refusing "arbor", MINIMAL_LAYERS byte-identical contract, VOICE_STYLE_ARBOR_TR
naming no vendor voice, `audio.output.speed` riding the audio_formats layer —
all directly asserted by dedicated tests in test_voice_openai_realtime.py /
test_voice_realtime_sessions.py, reran green.

**Client (ADR-0044):** independently reproduced (not just read) two things by
executing the real TS source in a scratch vitest config outside the repo:
1. Client `isForbiddenKey` (contract.ts) vs server `is_forbidden_key`
   (service.py) agree on all 9 probe spellings (apiKey, api-key, Api Key,
   audioPcm, x-api-key, context, next_item, waveform, eot_to_first_audio_ms) —
   both true except next_item. Confirms the two blocklists are truly
   duplicated logic, not just superficially similar.
2. A 40ms-wide click burst (boundary of `maxClickMs`) never opens the gate —
   reproduced directly, not inferred from the 5ms-click test already in the
   suite.

**Real defect found via mutation-probe:** of the two "controller gaps fixed in
passing" the f34cfd6 commit message claims are "covered by a test that would
fail if reverted" — only one actually is.
- Gap 1 (local detector `onSpeechEnd` never subscribed): reverting it breaks
  2 real tests in noise-metrics.test.ts. Claim TRUE.
- Gap 2 (duplicate local-speech subscriptions after reattach, via
  `wireLocalSpeech`'s unsub-then-resub in controller.ts): reverting the fix
  (removing the unsub loop) causes the FULL 70-test committed suite to still
  pass 70/70. The fix itself is real and correct — my own probe proved a
  reattach then duplicates every `report.mic_calibration` (and by extension
  every local-speech-sourced report) once per prior leg — but no committed
  test exercises "reattach, then a local-speech event" together, so this is a
  genuine test-coverage gap. Report this as DEFECT-in-coverage even though
  implementation is correct.

**Method note:** when a claim says "logic on side A matches side B" or
"function X and Y agree", don't just read both and eyeball it — actually
execute both with the same probe inputs and diff outputs. Here that meant a
throwaway vitest config (`root` pointed at apps/web, `include` pointed at a
sibling scratch dir outside the repo, since cross-drive scratch paths broke
Vite's `/@id/` resolution) importing the real TS module directly, and a
mirrored `app/lib/voice` + `tests/voice` tree in scratch to run the exact
committed test files against a hand-mutated copy of controller.ts (never the
real repo file) to see whether the suite would actually catch a reverted fix.

See [[feedback-concurrent-agent-worktree]] for a session hazard hit during
this same verification.
