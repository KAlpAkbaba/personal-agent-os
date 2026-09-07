---
name: m12-track-c-audio-client
description: State and non-obvious facts from building the M12 desktop audio client (PagentOS.Companion.Audio) on 2026-09-02 — ADR numbering risk, pre-existing format-gate failure, what is deferred
metadata:
  type: project
---

M12 track C (desktop audio client) was built 2026-09-02 as `devices/windows-agent/src/PagentOS.Companion.Audio`, additive to the frozen companion, recorded as ADR-0035 in `docs/DECISIONS.md`.

**Why:** the Session Companion is the only owner-session process, so it is the only legal home for microphone/speaker access; the qualified IPC/installer must stay untouched, so the client is a referenced library the installer ships automatically.

**How to apply:**
- Parallel tracks A/B/D/E may also append ADRs numbered 0035; on integration check `docs/DECISIONS.md` for a collision and renumber the track C one if needed.
- `dotnet format --verify-no-changes` on the solution FAILS on `main` for two *qualified* files (`tests/PagentOS.Agent.Tests/IpcWiringTests.cs`, `PipeTests.cs`, error CHARSET) — pre-existing, not caused by track C; do not "fix" qualified files to make the gate green without an explicit decision.
- Deferred on purpose (each has a seam): WebRTC leg (`IMediaLeg`; SIPSorcery evaluated), software AEC/NS of WebRTC-APM class (`IAudioProcessor`), the real sideband push transport (`ISidebandPushSource`).
- Contract guesses to reconcile with server track A: one event per `POST .../events` with `{event, client_seq, client_ts_ms, data}`; credential value keys tried in order `value, client_secret[.value], token, key, secret, ephemeral_key`; `POST .../attach` treated as optional (404/410 = session gone).
- The Communications-category WASAPI capture path (driver AEC) and all WASAPI code are untested on real hardware; first owner run should watch the `voice_session_started` audit row's `processing` field and the capture log line (`path=communications_category|plain`).
