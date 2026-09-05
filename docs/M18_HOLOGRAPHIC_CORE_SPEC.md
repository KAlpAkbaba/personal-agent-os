# M18 — Holographic Core UI

Status: **implemented for the states the contract actually carries** (2026-09-05)

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

## 3. State → visual

Implemented in `app/lib/uistate/visual.ts`, a pure function, exhaustively tested.

| State | Visual |
| --- | --- |
| `agent.idle` | calm breathing, 0.14 Hz |
| `agent.listening` | contracts below unit scale, energy drawn inward, depth from reported intensity |
| `agent.thinking` | expands, internal lattice topology, density from reported intensity |
| `agent.speaking` | pulses; **amplitude is the event's bounded energy**, zero if none was sent |
| `agent.researching` | evidence nodes, **exactly the count the publisher sent** (`kept`, else `candidates`) |
| `agent.memory_retrieval` | inward convergence ring, drawn only against real progress |
| `agent.tool_running` | modest expansion, light topology |
| `agent.waiting_owner` | restrained: near-still, dashed held boundary |
| `agent.goal_completed` | brief expansion |
| `agent.error` | controlled: one bounded offset ring, 0.18 Hz. **Capped in code** (`ERROR_AGITATION`), and severity changes colour only — never speed |
| `evolution.researching/designing/building/testing` | construction layers 1–4, one ring per phase reached |
| `evolution.shadow_ready` | one completed satellite, parked and motionless, with "canlıya alınmadı" in words |

### Audio

`agent.speaking` pulses from the `intensity` the state event already carries — a bounded
0..1 figure the API derives from levels the voice client reports. **This client never
captures, requests or persists owner audio**, and there is no Web Audio code in the Core.
No intensity means no pulse.

## 4. Truthful empty states

- No goals → "Hedef yok." No orbits are invented.
- No research counts → no particles, and "Kaynak sayısı bildirilmedi."
- Zero kept sources → "Kalite kapısından geçen kaynak yok." (a real zero, not unknown)
- No progress reported → **no progress bar at all**, and "İlerleme bildirilmedi."
- Panels distinguish four outcomes: not-yet-asked, failed, genuinely empty, loaded.
  A failed request never renders as an empty list.

## 5. Quality tiers and fallback

`app/lib/uistate/quality.ts`.

| | fps | detail | max satellites | lattice | DPR | glow |
| --- | --- | --- | --- | --- | --- | --- |
| high | 60 | 4 | 64 | 48 | 2 | yes |
| balanced | 30 | 3 | 32 | 24 | 1.5 | yes |
| low | 20 | 1 | 12 | 0 | 1 | no |

- `low` drops whole features rather than scaling them down.
- Counts from the API are capped before reaching the GPU; **the readout always states the
  true number** and, when capped, how many were drawn.
- No WebGL → `CoreFallback2D`, pure SVG animated by CSS only, showing every fact the 3D
  view shows with the same readout. `three` sits behind `next/dynamic({ssr:false})`, so a
  machine on the 2D path never downloads it.
- WebGL1 only → the high tier is refused (it would be a slideshow).
- Hidden tab → no rendering, and polling drops from 1 s to 20 s.
- `prefers-reduced-motion` → motion stops, information does not.
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
- counts, progress and pulse are each asserted absent when the publisher sent nothing.
