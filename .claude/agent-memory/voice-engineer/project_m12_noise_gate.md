---
name: m12-noise-gate-state
description: ADR-0044 web microphone/noise work package (K66 defect) - what shipped on the worktree branch 2026-09-02, what stays server-side (Layer 4), owner actions pending
metadata:
  type: project
---

ADR-0044 (2026-09-02) shipped the apps/web microphone/noise package on branch
`worktree-agent-abce96d2e49933e2a` (not pushed): read-back of browser constraints,
1.8 s noise-floor calibration, SpeechGate (energy + spectral + temporal), per-device
MicrophoneProfile in localStorage, Otomatik/Sessiz/Gürültülü/Çok gürültülü modes,
Tanılama view, Marin/Cedar voice selector, numbers-only metrics via `state` events.

**Why:** the owner's K66 is unusually sensitive; ambient sound triggered turns
(VOICE_OWNER_FEEDBACK.md). The owner wants the working realtime path preserved - surgical
optimisation only, no controller rewrite.

**How to apply:**
- Layer 4 (provider VAD threshold/eagerness in the session_config Cloud Core sends) is
  the only lever against PROVIDER-opened noise turns; the client now counts them as
  `false_turns`. Tune it server-side from the 14-scenario matrix numbers, not by intuition.
- Denoiser (RNNoise/APM) and uplink attenuation are seams: promote only behind
  measurements on the real device (checklist in denoiser.ts / ADR-0044 §4).
- Server forbidden payload keys include "text" and "token" with normalisation
  (service.py `is_forbidden_key`) - a key like `context` is refused; use `numbersOnly()`.
- Pending owner actions: K66 read-back JSON from Tanılama in the owner's own browser,
  AGC A/B on the device, the 14-scenario matrix (docs/OWNER_ACTIONS.md).
