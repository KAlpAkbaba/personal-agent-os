---
name: m18-active-eye-state
description: Status of the M18 Active Eye device-side local-perception client (apps/web) as of 2026-09-06
metadata:
  type: project
---

Built and committed on `worktree-agent-a945382534565e3fd` (commit `1df4904`, on top of a
merge of local `main` that pulled in the Presence Engine / Holographic Core renderer /
Routine Engine — see [[worktree-lag-behind-local-main]]). Not merged to main; not deployed.

**What exists**: `apps/web/app/lib/eye/{types,signal,client,perception,useActivePerception,
labels}.ts`, `apps/web/app/core/{EyeControl,EyeControlView}.tsx` wired into `/core` and
`/core/cockpit` beside (never inside) `AmbientBand`. Backend: a new `Intent.EYE_DISABLE` in
`services/api/app/voice/intents.py` (same normalization table, checked before `STOP`), and
`record_client_events` in `app/voice/realtime_sessions/service.py` deterministically calls
`app.presence.eye.disable_eye` when that intent resolves from a live utterance.

**Why the confidence formula is multiplicative**: `derivePresence` in `signal.ts` computes
`presence_confidence = evidenceQuality × sampleConfidence` (a gate), not an additive sum of
weighted terms. An earlier additive version let a single clean reading with ZERO samples
collected round up to ~0.75 confidence, because the sample-count weight was too small to pull
the total down — caught by `signal.test.ts`'s own "no evidence at all" test during
development. If this formula is ever revisited, keep the multiplicative gate or re-verify
that specific test case explicitly; it is the honesty property the M18 spec cares about most
("presence_confidence must be low when the evidence is weak").

**Not built / deliberately out of scope**: a dedicated LLM-callable voice tool
(`presence.eye_disable` in `realtime_sessions/tools.py`) — the deterministic
`record_client_events` path was judged sufficient and more reliable (does not depend on
model judgment for a privacy-critical action); adding the tool later would be additive, not
a conflict. Display-off, owner voice identification, and the full M18 "real owner run"
acceptance (`M18_HOLOGRAPHIC_CORE_SPEC.md` §7) are separate, still-open milestone items
untouched by this change.

**Gates run and passing at commit time**: `pnpm --filter @pagentos/web test` (403/403),
`pnpm --filter @pagentos/web lint` (oxlint, exit 0, only pre-existing warnings elsewhere),
`pnpm --filter @pagentos/web exec tsc --noEmit` (clean), plus the full backend
`tests/unit -k "presence or voice"` suite (422/422) via `uv run pytest` — pnpm is at
`C:\Users\alpak\AppData\Roaming\npm\pnpm.cmd`, uv at
`C:\Users\alpak\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe`
(see the user's own `machine-tool-paths` memory).
