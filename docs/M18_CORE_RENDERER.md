# M18 — Holographic Core UI (renderer)

The milestone spec is `docs/M18_HOLOGRAPHIC_CORE_SPEC.md`; this document is the
renderer half of it, written by the agent that built `apps/web/app/core`.

Status: **implemented, reconciled to contract v2** (2026-09-05); **M18.1 visual language**
applied (2026-09-07, ADR-0065, `docs/M18_1_CORE_VISUAL_LANGUAGE.md`).

Governing document: `docs/DECISIONS.md` ADR-0052. Where this document and ADR-0052
disagree, ADR-0052 wins.

> This file did not exist when the renderer was built. The work was specified by
> ADR-0052 plus `services/api/app/uistate/contract.py`; this document records what was
> built against them, so the next change has something to read. See ADR-0056 for the
> decisions taken along the way.

## 1. What the Core is

A living visual centre in `apps/web` that shows **what is true about the system right
now**, driven only by `GET /v1/ui/state`. Two modes:

- **Minimal Core Mode** (`/core`) — the Core, its state and the least context that is
  still honest. Meant to be left open on a second screen.
- **Cognitive Cockpit Mode** (`/core/cockpit`) — the Core plus read-only panels for
  research, memory, goals, world model, evolution, lessons, health, the ledger, pending
  owner actions, SHADOW_READY candidates and running tools.

## 2. The rule everything follows

**A visual state exists because a subsystem entered it.** The renderer never animates
"thinking" because motion looks alive, never shows SHADOW_READY without a real candidate
at that status, and never draws a count it was not given.

The corollary that drives most of the design: **silence is not calm.** Four different
kinds of quiet exist, and they are drawn and worded differently:

| Situation | Kind | Drawn as |
| --- | --- | --- |
| The bus reported `agent.idle` | `idle` | calm breathing |
| Polls succeed, the bus is empty | `untold` | still, dimmed, "Henüz bir durum bildirilmedi" |
| A transient claim aged out | `last_known` | the old shape, still and faded, with its age |
| The API is unreachable | `unreachable` | last known shape, damped, labelled not-live |
| The session was refused | `unauthorized` | nothing |
| A state this build does not know | `unknown_state` | still, named, not guessed |

The bus publishes *entries* into states and never exits — nothing ever says "the agent
stopped thinking". So a transient state is a statement about a moment. After
`TRANSIENT_TTL_MS` (12 s) the client stops claiming it and shows last-known. It
deliberately does **not** fall back to idle: we were not told the work stopped, only that
we stopped being told anything.

Steady states (`agent.idle`, `agent.waiting_owner`, `agent.error`,
`evolution.shadow_ready`) never expire. `agent.goal_completed` is a *moment*: prominent
for 20 s, then history.

## 2a. Channels (contract v2)

v2 put four kinds of statement on one bus, and they must not displace one another:

| Channel | States | Drawn as |
| --- | --- | --- |
| `agent` | `agent.*` | the core body |
| `lab` | `evolution.*` | the core body, as satellites/construction |
| `ambient` | `eye.*`, `owner.*` | the band beside the core |
| `release` | `release.*`, `routine.*`, `alarm.triggered` | the band beside the core |

The core body reads `coreClaim` — the newest **agent/lab** event — not the API's `current`.
Publishing `owner.likely_asleep` while research runs must not blank a working core: the
owner going to bed is not the assistant stopping. The eye and the owner are read by prefix
rather than by channel, because a presence update says nothing about the camera.

Two new state kinds go with them. An `observation` (presence) lives five minutes by default
and an `operation` (a release stage) fifteen, because twelve seconds is the wrong answer in
both directions — but both still expire, and a decayed presence becomes **unknown**, never
"still present". A publisher's own `ttl_s` beats every default.

The camera cell carries the one rule the rest of the UI does not: `untold` is not
`disabled`. An indicator that reads as "off" when nobody has said anything would be a
privacy assurance nobody gave, so the two are separate statuses with separate wording, and
an unreadable `eye.*` state is flagged as unreadable. Presence is always shown with the
engine's confidence, or with "güven bildirilmedi" — never with a substituted number — and
the band states on every render that perception is not authentication.

## 3. State → visual

Implemented in `app/lib/uistate/visual.ts`, a pure function, exhaustively tested.

Since M18.1 the Core is a layered structure — nucleus, three internal rings, connection
paths, two structural shells, and outside them only what a subsystem said exists — with
two bounded particle flows. `e` below is `energy`: the publisher's declared intensity, or
the local session's measured level, and **0 when neither exists**. The full channel table
is `docs/M18_1_CORE_VISUAL_LANGUAGE.md` §3–4.

| State | Visual |
| --- | --- |
| `agent.idle` | calm breathing, 0.14 Hz; rings drift at 0.05; shells close; no flow |
| `agent.listening` | contracts below unit scale, shells closed, rings slow; energy drawn inward at 0.35+0.65e. Locally, `ownerVoice` = the gate's mic level quickens and brightens the pull |
| `agent.thinking` | expands 1.12+0.1e; shells open 0.55+0.25e; rings turn 0.5+0.5e; paths dense 0.4+0.6e with travellers at 0.5+0.5e |
| `agent.speaking` | pulse shell; **amplitude is the event's bounded energy** (locally, the playback RMS), zero if none; glow answers to the same figure |
| `agent.researching` | constellation of **exactly the count the publisher sent** (`kept`, else `candidates`), a spoke each, drifting at published progress or the fixed rest figure; the fixed 5-node **motif, labelled as a representation**, when no count was sent; the candidate field when both counts were |
| `agent.memory_retrieval` | inward flow 0.4; convergence ring closes in only against real progress |
| `agent.tool_running` | modest expansion, shells open 0.45, rings 0.4, the heaviest path traffic (0.6) |
| `agent.waiting_owner` | restrained: near-still rings (0.02), shells near-closed, no flow, dashed held boundary |
| `agent.goal_completed` | brief expansion, shells wide, brightest base glow |
| `agent.error` | controlled: one bounded offset ring, 0.18 Hz. **Capped in code** (`ERROR_AGITATION`), and severity changes colour only — never speed |
| `evolution.researching/designing/building/testing` | construction layers 1–4, one ring per phase reached; light path traffic |
| `evolution.shadow_ready` | capability nodes parked at the satellite's radius — the lab's `ready` count, else exactly one — with "canlıya alınmadı" in words and whether the lab counted |
| `eye.active` (eye channel) | one thin aperture ring on the core, whatever the core body is doing; read from the eye's own claim |

### Audio

A bus `agent.speaking` pulses from the `intensity` the state event already carries — a
bounded 0..1 figure the API derives from levels the voice client reports. No intensity
means no pulse.

Since ADR-0061 the Core is also the owner's voice surface, and when THIS tab's own
realtime session is speaking the pulse is the assistant's real output envelope: RMS read
off the playback analyser of the one shared `WebAudioPlayback` (`lib/voice/audio.ts`),
sampled at animation rate, 0 the moment playback stops, `null` (drawn as no pulse and
worded as "ölçülemedi") when no output path exists. The Core still has no Web Audio code
of its own — it reads one number from the voice store — and it still never captures,
requests or persists owner audio: the microphone belongs to the voice session, which
exists because the owner connected it, and the only thing the Core takes from it while
listening is the local gate's bounded level. The readout names which source (bus or the
local session) produced the visual on every render.

### Local voice overlay (ADR-0061)

| Controller state | Visual | Scalar source |
| --- | --- | --- |
| `idle`, `closed` | none — the bus body shows | — |
| `creating`, `connecting`, `reconnecting` | `connecting`, dimmed and still | — |
| `listening` | `listening` (contracts, inward flow) | mic level from the local gate, or a still 0.35 when unmeasured |
| `tool_running` | `tool_running` topology, label = the tool's Turkish name | — |
| `speaking` | `speaking`; `pulse` = output envelope; label = the speech caption | playback analyser RMS |
| `interrupted` | `interrupted`: pulse 0, no breathing | — |
| `error` | `error`, bounded agitation, label = `lastError` | — |

The overlay replaces the core body only; the release orbit stays exactly as the bus drew
it. A refused bus session (`unauthorized`) draws nothing, overlay or not.

## 4. Truthful empty states

- No goals → "Hedef yok." No orbits are invented.
- No research counts → no particles, and "Kaynak sayısı bildirilmedi."
- Zero kept sources → "Kalite kapısından geçen kaynak yok." (a real zero, not unknown)
- No progress reported → **no progress bar at all**, and "İlerleme bildirilmedi."
- Panels distinguish four outcomes: not-yet-asked, failed, genuinely empty, loaded.
  A failed request never renders as an empty list.

## 5. Quality tiers and fallback

`app/lib/uistate/quality.ts`.

| | fps | detail | max satellites | lattice | rings | shells | particles | parallax | DPR | glow |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| high | 60 | 4 | 64 | 48 | 3 | 2 | 160 | yes | 2 | yes |
| balanced | 30 | 3 | 32 | 24 | 2 | 1 | 80 | yes | 1.5 | yes |
| low | 20 | 1 | 12 | 0 | 1 | 0 | 0 | no | 1 | no |

`sceneBudgetFor(tier)` states the most the 3D scene can mount at a tier, and
`quality.test.ts` pins it: high 26 drawables / 296 instances; balanced 24 / 152;
low 18 / 32. Instances = particles + 2 × satellites + 8 capability nodes.

- `low` drops whole features rather than scaling them down; it keeps one ring so the
  structure keeps its identity.
- Counts from the API are capped before reaching the GPU; **the readout always states the
  true number** and, when capped, how many were drawn.
- The frame is a pure reducer (`app/lib/uistate/scene.ts::stepScene`), dt-based, mutated in
  place; the scene only copies its numbers. A structural test refuses any allocating
  construct in the reducer or in the scene's frame body.
- No WebGL → `CoreFallback2D`, pure SVG animated by CSS only, showing every fact the 3D
  view shows with the same readout and, since M18.1, the same layered structure. `three`
  sits behind `next/dynamic({ssr:false})`, so a machine on the 2D path never downloads it.
- WebGL1 only → the high tier is refused (it would be a slideshow).
- Hidden tab → no frame runs, nothing invalidates, and polling drops from 1 s to 20 s.
- `prefers-reduced-motion` → one settled frame per intent change; motion stops,
  information does not.
- Failed polls back off exponentially to a 30 s cap.

## 6. Boundaries

- The Core is a **pure consumer**. There is no write path and there must never be one:
  the absence of a write endpoint is what stops a client claiming a state it is not in.
- The cockpit is **read-only**. Approving a goal or a SHADOW_READY candidate stays an
  owner action on the surface that owns it (ADR-0053 §5).
- The renderer is never on the critical path of cognition: one in-flight poll, bounded
  work per frame, and every failure degrading to an honest label.

## 7. Testing

`apps/web/tests/uistate/`, run by `pnpm --filter @pagentos/web test`.

Node + `react-dom/server`, matching the existing research suite. **No browser, no
Playwright, no vitest browser mode.** The 2D view is a pure function of the intent and is
animated entirely by CSS, so static markup carries the full assertion — a headless browser
would add orphan-process and desktop-pollution risk for no extra coverage.
`services/browser/tests/test_test_isolation_guards.py` exists because that risk was once
realised on this owner's desktop; the discipline is inherited here by not opening the door.

Coverage of note:

- every state in the contract is asserted to produce its own visual **and** to produce no
  other state's visual;
- a test fails if the API grows a state and the visual table is not updated;
- silence, expiry, unreachability and refusal are each asserted to differ from idle;
- counts, progress and pulse are each asserted absent when the publisher sent nothing;
- M18.1: every core state has a channel profile no other state shares; with
  `intensity: null` and no measurement the rhythm channels are zero; the frame reducer
  stays still on a zero intent, is frame-rate independent, and — structurally, by reading
  the source — allocates nothing (`scene.test.ts`); the 2D structure, the motif, the
  capability nodes, the aperture and the cockpit telemetry are asserted as markup.
