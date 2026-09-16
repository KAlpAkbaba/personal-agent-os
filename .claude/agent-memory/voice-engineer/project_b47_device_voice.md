---
name: b47-device-voice
description: B47 device-side voice (2026-09-16) - owner chose continuous listening; the M12 companion client already captured and streamed silence (StreamWhileIdle=true); no offline Turkish engine on the machine
metadata:
  type: project
---

Owner decision 2026-09-16 (final): continuous listening on the device, local VAD gate, raw
audio never persisted, indicator + toggle + detected hardware mute, wake word optional,
push-to-talk fallback, small offline command set (alarm stop/snooze, listening off, time).

Facts measured while building it:
- The B47 evidence file said "no capture path exists" - wrong. `PagentOS.Companion.Audio`
  (M12 track C) captures via WASAPI and, with `StreamWhileIdle = true`, uplinked EVERY frame
  (silence included) while voice was enabled. The device gate (`DeviceListeningService`)
  sits upstream of the orchestrator as a virtual capture device.
- This machine has only en-US SAPI/OneCore recognizers, no Turkish engine, no speech NuGet in
  the offline cache. The offline engine is an in-house MFCC+DTW template spotter with
  owner-enrolled templates (speaker-dependent; never an authentication signal).
- Cloud Core URL on this machine: http://pagentos-core:8001; voice is enabled per user env
  `PAGENTOS_AGENT_VoiceEnabled` + `PAGENTOS_AGENT_CloudCoreUrl` (installer writes neither).

**Why:** privacy rows 244/249/254/255 are product gates; offline rows 241/253 depend on an
engine the machine does not have.
**How to apply:** physical microphone evaluation stays the owner's
(`scripts/core/qualify-device-voice.ps1`); never open the real mic in automated tests.
Related: [[m12-realtime-state]], [[m12-noise-gate]].
