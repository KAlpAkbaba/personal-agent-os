---
name: project-m12-companion-contract
description: M12 companion<->Cloud Core integration state after ADR-0039 - what is real, what is still unwired, and the traps that bit (forbidden payload keys, structlog `event`, worktree sandbox refusing compound bash)
metadata:
  type: project
---

ADR-0039 (2026-09-02) closed the two track-C handoff defects: the companion now posts the
server's exact `/v1/voice/realtime` contract (batched events, server kinds, `credential.secret`
+ `transport_descriptor`, `client_kind=windows_desktop`) and the `voice_sideband` broker frame is
declared additively (schema `$defs/voice_sideband`, Agent.Core `VoiceSidebandMessage`, pipe frame
`voice_sideband`, `PipeSidebandPushSource` in the companion). ADR numbers: 0035=M13, 0036=A+E,
0037=C, 0038=B, 0039=integration.

**Why:** the first companion cut shipped against an in-process fake that was looser than the
server; every request would have 422'd. `InProcessFakeCloudCore` is now the server's mirror and
`ContractTests` post raw bodies to it - keep it that way.

**How to apply:**
- Any new client payload key must pass `RealtimeContract.IsForbiddenKey` (server rule: a key
  containing audio/pcm/wave/secret/credential/token/apikey/password/text/transcript under ANY
  spelling is 422). `eot_to_first_audio_ms`, `context`, `waveform` are refused; the reporter throws
  locally so tests catch it.
- 409 = leg superseded: close the leg, hold events, reclaim on the owner's next speech onset (not on
  the 409, to avoid two clients ping-ponging). 410/404 = session gone -> `StopReason=session_gone`,
  host reopens.
- Still unwired/unreal: owner mic, real credential, real broker; the OpenAI codec still uses
  beta-era `session.update` field names pending the ADR-0038 smoke.
- Python: structlog reserves the `event=` kwarg in `logger.warning(...)`; use another name.
- Worktree sandbox refuses compound bash (`&&` with `cd`/heredocs to repo files); use the Write/Edit
  tools or single simple commands for repo files.
