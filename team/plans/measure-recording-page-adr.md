# ADR (unnumbered): the measurement recording page `/voice/measure`

Date: 2026-10-03 · Card: measure-recording-page · Proposal: team/proposals/2026-10-02-olcum-kaydi.md
Status: accepted (worker); the lead numbers it.

## Context
ADR-0242 left the STT measurement with an instrument and no recordings. The API card
(`services/api/app/voice/measurement/`) stores twenty readings per place. This page is the
owner's side: he reads, the page writes the file, the name and the labels.

## Decisions
1. **Capture point: the processed stream, not the raw one.** The samples come from
   `BrowserMicrophone.stream`, opened with the device and the profile constraints the voice
   rig uses (`constraintsFor(profile)`, passthrough denoiser). So the recording is the sound
   the system hears, with the browser's echo cancellation / noise suppression / AGC as
   applied. A raw recording would measure an engine on audio it never gets in use. The applied
   settings (echoCancellation, noiseSuppression, autoGainControl, voiceIsolation, sampleRate,
   label - unknown values left out, because the API refuses nulls) travel as `capture`, so the
   report can say what processing was on. Not attached: the gated-attenuation uplink shaper,
   which is driven by the session's speech detector and does not run on this page.
2. **One microphone.** While the tab's voice session is live (`isLiveState`) recording is
   disabled and the page says why (ADR-0061). Each take builds a fresh probe
   `BrowserMicrophone` and closes it in a `finally` (good take, refused or failed upload,
   thrown capture). Resampling to 16 kHz and the 44-byte PCM header are done in the page
   (`lib/voice/measure/wav.ts`); out-of-range samples are clamped, never wrapped.
3. **Chrome's recogniser on the same audio, guarded.** `SpeechRecognition.start(track)` with a
   clone of the take's own audio track, `lang = "tr-TR"`. This was read from a compatibility
   table, never tried with tr-TR. It is feature-detected: no SpeechRecognition, or a Chrome
   below 135 (which would ignore the argument and listen to the default microphone - a second
   capture of a different sound), means no recogniser. Never started without a track.
   `browser_transcript = null` means Chrome's row is not measured for this sentence (no
   recogniser, start threw, an error event, or neither an end nor a result within
   `RECOGNIZER_GUARD_MS` = 1.5 s after the take); `""` means it ran and wrote nothing. The take
   is uploaded either way. `browser_engine` is `"bilinmiyor"` when it ran (the page does not set
   `processLocally`, so it cannot know the leg - SttEngine of localMode.ts), null when not.
4. **One-press delete.** 'Ölçüm kayıtlarını sil' sends one `DELETE /recordings` with no dialog
   (owner rule 2026-09-18) and says how many were deleted. 'Tekrar' re-records one sentence
   (the PUT replaces); 'Sil' deletes one.
5. **Reached by its link, not by voice.** The proposal's spoken sentence "Ölçüm kaydını başlat"
   is NOT wired: `intents.py` is other cards' area. `/voice` carries a link 'Ölçüm kaydı'.
6. No sentence text is in the web source; the page shows what GET returned
   (`stt_compare.OWNER_SENTENCES`), and a contract test holds that.

## Consequences
- The first real `start(audioTrack)` with tr-TR happens on the owner's machine (or an inspector's
  headless run); if Chrome refuses the track, the Chrome row stays NOT_RUN and the rest stands.
- A take abandoned by leaving the page is cancelled, not uploaded.
