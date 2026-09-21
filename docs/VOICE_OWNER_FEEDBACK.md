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

## 2026-09-03 — second real session (cedar, Arbor profile, K66), fetched after disconnect

Persistence closed as PROVEN_REAL by the owner: session `93f9b4f7-…-f294d3a949ae`, state
`closed`, `closed_at` set, `benchmark_snapshot_at_close = true`, the same canonical id
survived the provider/WebRTC closure, benchmark fetched from a separate PowerShell process.

Real metrics (client-reported, target in brackets): mic→uplink p50 391 / p95 472 ms
[120] FAIL, with 6 unmatched mic-start samples; EOT→first audio p50 674 / p95 1059 ms
[700] FAIL (outliers 925/1059); barge-in→playback stop p50 210 / p95 210 ms [150] FAIL,
almost every sample 209–210 ms, one 0 ms sample; audio gaps 0 PASS. K66 noise: false
starts 5, false barge-ins 4, false turns 0, gate opens 15, gated out 6, click rejects 6.
Persisted calibration carried `env=0, sensitivity=0, peak_db=-100` (ambiguous). The
transcript summary displayed with mojibake (`Ã`, `Å`). `tool_preamble` and
`tool_done_to_speech` were not exercised (n=0) and are excluded from any conclusion.

**What the system did with this (ADR-0047/0048):** the encoding defect was traced to the
reading side (the database holds correct UTF-8; the API now declares `charset=utf-8` and
the scripts decode bytes as UTF-8); the five metrics are now decomposed into measured
sub-phases (capture, local gate, RTP send, provider receipt; detect/stop-command/gain-zero
for barge-in; response-created/first-delta/playback for EOT); calibration reports
`measured: 1` with sample counts and never a sentinel; the client-side optimisation pass
targets the fixed 210 ms barge-in delay and the K66 false starts/barge-ins without a global
threshold. The revised voice target stays NOT PROVEN until the owner's rerun meets or
materially approaches the latency targets and ambient noise no longer causes distracting
activations.

**Acceptance for the revised voice target** (owner confirms, nothing else counts):
voice character perceptually close enough to Arbor; normal room noise no longer causes
distracting activations; the owner's Turkish stays natural and complete; interruption
stays fast; normal hesitation does not cause premature responses; the assistant's own
speaker output does not create loops. Voice character and noise processing are
qualified together, never separately.

## 2026-09-21 — two notes after the local-mode week (Chrome Web Speech, router-only)

**Note 1 (repetition):** "farkettiğim kadarı ile her bir işlemi ne kadar uğraşsam da tek
tek her seferinde söylemek zorundayım — yukarı tuşuna bas dediğimde 5 kere yapılması
gerekiyorsa bunu 5 kere demem gerekiyor; '5 defa' veya '5 kere yap' deyince kabul etmiyor."

**Note 2 (a recorded "hareket"):** "kafamda bir buton veya tıklama hareketi var … Gmail'den
yeni mail kısmını aç dememi anlamaz; ekranda Gmail yazan yere tıkla, sonra Oluştur yazan
yere tıkla demem lazım. Bunun yerine 'yeni hareket oluşturalım' veya 'başlat' dediğimde
attığım 3-5 ne kadar varsa hepsini alacak; 'hareketi bitir' / 'hareketi tamamla' deyince
benden ad isteyecek; 'yeni mail sekmesi' diyeceğim; bundan sonra 'yeni mail sekmesi aç'
dediğimde bu hareketleri ben tarif etmeden tekrarlayacak."

**What the system did with this:** ADR-0195 (the spoken count is applied to a key, a
chord and a scroll; bound 30, refused aloud past it) and ADR-0196 (voice macros:
`macro.record_start` → the calls that follow are kept → `macro.record_end` asks for a
name → the next sentence is the name → the name alone replays the calls through the same
tools and the same step-up gate). Not yet heard by the owner on the real device: the
sentences to try are in `docs/HANDOFF.md`.
