# Voice — owner feedback record

The owner's own words are the acceptance authority for voice (ADR-0034 §6). This file
records them as given, dated, with what the system did in response. Nothing here is a
claim by the system.

## 2026-09-02 — first real microphone / WebRTC session (Windows PC, K66 microphone)

**Overall:** the realtime interaction is *generally good*. The owner does not want the
working realtime architecture redesigned. Not to be read as "milestone complete": the
remaining work is focused quality optimisation.

**Preferred target voice:** *ChatGPT Arbor*. The owner tested the alternatives and
specifically prefers Arbor. Desired character: relaxed, natural, warm, versatile,
conversational rather than announcer-like, confident but not formal, low theatricality,
natural Turkish prosody, moderate speaking speed, smooth sentence transitions, natural
pauses, suitable for both conversation and long listening, low listening fatigue, no
exaggerated cheerfulness, no artificial "AI assistant" cadence.

**Primary remaining defect:** environment / background noise influences the session far
too much. The owner's microphone is unusually sensitive; ambient sound repeatedly
triggers conversational turns.

**What the system did with this (ADR-0043):**

- Live provider discovery (2026-09-02, one real client-secret mint with
  `voice: arbor`): the Realtime API refuses it and lists exactly `alloy, ash, ballad,
  coral, echo, sage, shimmer, verse, marin, cedar`. So `owner_target_voice_profile =
  arbor` is preserved as the perceptual target, the closest supported voice is selected
  by qualification with the Arbor style profile applied through the persona
  instructions and output pacing, and if OpenAI later exposes Arbor to the API the
  switch is configuration only, followed by qualification. No cloning or imitation of a
  proprietary voice is attempted.
- Background noise became a first-class acceptance defect with its own work package:
  a layered input pipeline (browser processing verified by read-back, non-invasive
  noise-floor calibration, local speech gating, provider semantic VAD kept, advanced
  denoising only behind measurements), microphone profiles per device, owner-facing
  modes (`Otomatik` default, `Sessiz ortam`, `Gürültülü ortam`, `Çok gürültülü ortam`),
  and a real 14-scenario noise qualification matrix on the owner's machine.

**Acceptance for the revised voice target** (owner confirms, nothing else counts):
voice character perceptually close enough to Arbor; normal room noise no longer causes
distracting activations; the owner's Turkish stays natural and complete; interruption
stays fast; normal hesitation does not cause premature responses; the assistant's own
speaker output does not create loops. Voice character and noise processing are
qualified together, never separately.
