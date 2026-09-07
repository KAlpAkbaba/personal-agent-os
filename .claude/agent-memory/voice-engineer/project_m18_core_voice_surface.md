---
name: m18-core-voice-surface
description: ADR-0061 (2026-09-06) — /core is the owner's voice surface via one tab-wide VoiceStore singleton; overlay rules, what is still missing (query_kind caption), and the owner run that has not happened yet
metadata:
  type: project
---

ADR-0061 landed on worktree branch `worktree-agent-a76eb36a70fc4f2f8` (commit 2a2c4f0, not merged): `/core` and `/core/cockpit` read the tab's ONE realtime voice session through `apps/web/app/lib/voice/store.ts` (`getVoiceStore`), rig construction lives in `lib/voice/rig.ts`, `/voice` is now only the diagnostics view over the same session.

**Why:** the owner opened `/core` and could not speak; `/voice` built and disposed its own rig per mount, so a second controller for the Core would have meant a second `getUserMedia` and a second realtime session per tab. The instance registry (`voiceInstances` in rig.ts) exists so "exactly one" is a test assertion, not a hope.

**How to apply:**
- Never build a `VoiceSessionController`, `BrowserMicrophone` or transport outside `createVoiceRig`; consumers use `useVoiceSession()` / `store.subscribe`. The one tolerated exception is `/voice`'s AGC A/B benchmark probe (owner-triggered, closed before returning, never while live).
- The Core's voice states are a LOCAL overlay (`applyVoiceOverlay`, `VisualIntent.source = "voice"`), never bus events; the speaking pulse must stay `Playback.outputLevel()` (real RMS), never a synthesized rhythm.
- The controller exposes no cognition `query_kind`/subsystem yet, so the caption is tool-or-cursor only; adding `query_kind` to the controller snapshot would need `services/api` sideband work (was out of scope on 2026-09-06 because another agent was editing services/api).
- The owner has NOT yet done the real run on `/core` (Bağlan → listening/tool/speaking/Dur/Gözünü kapat); ADR-0061's consequences paragraph lists what that run should show.
