# M18.1 — The Core's visual language: layered, bounded, measured

Status: **implemented** (2026-09-07). Governing decision: ADR-0065 in `docs/DECISIONS.md`.
The renderer it extends is `docs/M18_CORE_RENDERER.md`; its rule — *the Core draws only
what was published or measured* — is not relaxed anywhere in this document.

## 1. What changed, in one paragraph

The M18 Core was a wireframe icosahedron with a lattice, some points and a few rings. M18.1
replaces that with a **layered structure**: a translucent nucleus; three concentric internal
rings on tilted planes (the topology layers); the connection paths across the interior; two
translucent structural shells standing off the nucleus; and, beyond them, the things that
exist only when a subsystem said so — the evidence constellation, the parked capability
nodes, the eye's aperture, the lab's construction layers, the release orbit. Two bounded
particle populations move through it: one pulled inward while the system listens or
recalls, one travelling the paths while it thinks or works. Every motion is a number on
`VisualIntent`, derived in `visual.ts` from a published field or a real measurement, and the
frame arithmetic is a pure reducer (`scene.ts`) tested in Node. The Minimal mode gives the
structure the viewport; the Cockpit prints the numbers it was drawn from.

This is an original PersonalAgentOS language. It is not a copy of any film interface, and
the constraint that makes it original is the honesty rule: a structure that may only move
on evidence ends up looking like nothing else, because nothing else is built that way.

## 2. The shapes

From the inside out. World radii are the 3D scene's (`CoreScene.tsx`); the 2D fallback
draws the same proportions from the same constants (`scene.ts`), scaled to its viewbox.

| Shape | Radius | What it is | Drawn from |
| --- | --- | --- | --- |
| Nucleus | 0.62 | The core body. Translucent, breathing only when told to. | `scale`, `breathAmplitude`/`breathHz`, `glow` |
| Internal rings | 0.84 / 1.00 / 1.16 | The topology layers, each on its own tilted plane. They turn at the reported spin and contract while energy is drawn inward. | `ringSpin`, `inwardFlow` |
| Connection paths | 1.22 | Chords across the interior, on a fixed golden-angle spiral. | `topology` (density/opacity), `flowRate` (the travellers on them) |
| Structural shells | 1.30 / 1.52 | Translucent wireframe shells that stand off the nucleus by the reported spread and counter-rotate slowly. | `shellSpread`, `ringSpin`, `glow` |
| Inward flow | 1.55 → 0.32 | Particles pulled from the outer start to the nucleus along fixed directions. | `inwardFlow`, quickened by `ownerVoice` |
| Path travellers | on the chords | Particles moving along the connection paths. | `flowRate` |
| Pulse shell | 1.28 + 0.45·pulse | The speaking envelope, as a back-face shell. | `pulse` (the measured RMS or the declared intensity) |
| Held boundary | 1.28 | A dashed loop: the system is deliberately not working. | `restraint` |
| Error ring | 1.12 | One offset ring, bounded and slow. | `agitation` (capped) |
| Convergence ring | 1.45 − 0.9·progress | Memory: closes in by exactly the published progress. | `convergence`, only when `convergenceKnown` |
| Eye aperture | 1.64 | One thin tilted ring while `eye.active` is current. | `eyeActive` |
| Constellation | 1.78 | Research evidence nodes, a spoke each, drifting as a whole. | `constellationNodes`, `constellationDrift` |
| Candidate field | 2.00 | The wider set research saw, faint, counter-drifting. | `fieldNodes` when `fieldNodesKnown` |
| Capability nodes | 1.70 | SHADOW_READY candidates, parked, the first under the satellite's halo. | `capabilityNodes` |
| Construction layers | 1.35 + 0.16·i | One ring per lab phase reached. | `constructionLayer` |
| Release orbit | 1.70 | Unchanged from M18 spec §15. | `releaseStage`, `releaseProgress` |

Depth comes from the layering itself — three ring planes, two shells, the paths inside —
and from a slight camera lean towards the pointer (`parallax`, high/balanced tiers only).
The lean is a way of seeing the layers, not a claim about the system; it settles when the
pointer does.

## 3. The channels

Every channel is a number on `VisualIntent` in 0..1 unless noted, produced in
`app/lib/uistate/visual.ts` and nowhere else. The M18 channels (`scale`, `breathAmplitude`,
`breathHz`, `inwardFlow`, `topology`, `pulse`, `agitation`, `restraint`, `dim`,
`sourceNodes`, `convergence`, `constructionLayer`, `satelliteComplete`, `release*`) are
unchanged. M18.1 adds:

| Channel | Derived from | Zero when |
| --- | --- | --- |
| `energy` | The publisher's `intensity` (bus), the gate's microphone level (local listening), the playback RMS (local speaking). | Nothing declared or measured. **Never synthesised.** |
| `glow` | A per-state base + 0.5·`energy`, capped at 1. | No event (untold, connecting, unauthorized). Damped ×0.3 for unreachable / last-known. |
| `shellSpread` | Per-state constant; thinking adds 0.25·`energy`. | No event. |
| `ringSpin` | Per-state constant; thinking adds 0.5·`energy`. | No event, expired, unreachable, the room's states. |
| `flowRate` | Per-state constant; thinking adds 0.5·`energy`. | Idle, listening, waiting, error, and every silence. |
| `ownerVoice` | Exactly `micLevel` while THIS tab's session is listening. | Bus listening (its intensity is declared, not measured here); every other state. |
| `constellationNodes` (count) | Research: `kept` else `candidates`; the fixed motif `CONSTELLATION_MOTIF` (5) when neither was sent. | Outside research; a published kept count of 0. |
| `constellationDrift` | Research: `CONSTELLATION_REST` (0.2) + 0.6·published progress; the rest figure alone without progress. | Outside research. |
| `fieldNodes` (count) | Research: `candidates − kept` when both were published. | Either count missing (`fieldNodesKnown` false). |
| `capabilityNodes` (count) | SHADOW_READY: published `ready`/`candidates`, else 1 (the candidate the event is about; `capabilityNodesCounted` false). | Nothing at SHADOW_READY. |
| `eyeActive` (0/1) | `eyeView(eyeClaim(truth))` is `active` and unexpired. | Anything else, including an unreadable `eye.*` token. |

Booleans that say whether a count was published (`sourceNodesKnown`, `fieldNodesKnown`,
`capabilityNodesCounted`) are not channels; they are what the readout reads.

## 4. What each state does

`e` is `energy`. The full table is `forLiveState` in `visual.ts`; this is what the owner
sees.

| State | Nucleus | Shells | Rings | Paths | Flow | Light |
| --- | --- | --- | --- | --- | --- | --- |
| IDLE | breathes 0.14 Hz | close (0.15) | drift (0.05) | none | none | 0.15 |
| LISTENING | contracts 0.88−0.05e | closed (0.05) | slow (0.12) | none | inward 0.35+0.65e | 0.2+e/2 |
| OWNER_SPEAKING (local listening, mic > 0) | as listening | as listening | as listening | none | inward at the mic level, quickened by it, brighter | as listening, from the mic |
| THINKING | expands 1.12+0.1e | open 0.55+0.25e | turning 0.5+0.5e | dense 0.4+0.6e | travellers 0.5+0.5e | 0.4+e/2 |
| TOOL_RUNNING | 1.02 | open 0.45 | 0.4 | 0.25 | travellers 0.6 (the most of any state) | 0.35+e/2 |
| SPEAKING | 1.04 | 0.3 | 0.2 | none | travellers 0.25; pulse shell = the envelope | 0.3+e/2, e = the RMS |
| RESEARCHING | 1.06 | 0.4 | 0.3 | 0.2 | travellers 0.35; constellation drifts at the published or rest figure | 0.3+e/2 |
| MEMORY_RETRIEVAL | 0.96 | 0.2 | 0.25 | none | inward 0.4; convergence ring at the published progress | 0.3+e/2 |
| EVOLUTION (lab working) | 1 | 0.3 | 0.2 | none | travellers 0.2; one construction ring per phase | 0.25+e/2 |
| SHADOW_READY | 1 | 0.2 | drift (0.05) | none | none; capability nodes parked | 0.2+e/2 |
| WAITING_OWNER | 0.94 | near-closed 0.1 | almost still (0.02) | none | none; dashed held boundary | 0.12+e/2 |
| GOAL_COMPLETED | 1.1 | wide 0.6 | 0.15 | none | none | 0.6+e/2 |
| ERROR | 0.98 | 0.25 | 0.08 | none | none; one bounded offset ring at 0.18 Hz | 0.25+e/2 |
| EYE_ACTIVE | (unchanged) | | | | | one thin aperture ring, on any of the above |
| untold / connecting / unauthorized | still | none | still | none | none | 0 |
| last_known / unreachable | the shape it had, faded | as it was | still | none | none | ×0.3 |

"Waiting for the owner" is `agent.waiting_owner` — a published bus state — plus the
release orbit's `owner_approval_required`. Nothing is derived for it that was not published.

## 5. Budgets

`TIER_BUDGETS` in `quality.ts`, and `sceneBudgetFor(tier)` which a test holds the scene to.

| | fps | detail | rings | shells | particles | lattice | satellites | parallax | glow | DPR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| high | 60 | 4 | 3 | 2 | 160 | 48 | 64 | yes | yes | 2 |
| balanced | 30 | 3 | 2 | 1 | 80 | 24 | 32 | yes | yes | 1.5 |
| low | 20 | 1 | 1 | 0 | 0 | 0 | 12 | no | no | 1 |

`sceneBudgetFor`: **high** 26 drawables / 296 instances / 160 particles; **balanced** 24 /
152 / 80; **low** 18 / 32 / 0. Instances = particles + 2 × satellites (constellation and
field) + 8 capability nodes. The figures are maxima: a channel at zero mounts nothing.
`low` keeps one ring so the structure keeps its identity, and drops shells, particles,
paths, parallax and the glow shell whole rather than shrinking them.

## 6. Engineering shape

- **The frame is a reducer.** `stepScene(state, intent, dt, pointerX, pointerY)` in
  `app/lib/uistate/scene.ts` approaches every smoothed value towards its intent channel
  (exponential in `dt`) and accumulates rotations and particle phases (linear in `dt`).
  `CoreScene` calls it and copies numbers onto three.js objects. `tests/uistate/scene.test.ts`
  proves: a zero intent stays still forever; sixty small steps and twenty large ones reach
  the same picture; the state is mutated in place; and — by reading the source — neither the
  reducer nor the scene's frame body contains an allocating construct.
- **Hidden tab: no work.** `CoreCanvas` takes `hidden` and `still` apart. Hidden puts the
  canvas in `demand` mode, the frame loop returns before any arithmetic, and no effect
  invalidates. Reduced motion settles the structure on the new intent in one long step and
  draws that frame, once per intent change.
- **Particles are instanced.** Two `InstancedMesh`es, `frustumCulled={false}` (the base
  geometry's bounds sit at the origin), matrices composed from module-level scratch objects.
- **The 2D fallback carries the same identity.** `CoreFallback2D` draws the rings, shells,
  aperture, constellation (counted or motif), field and capability nodes in static SVG, with
  the ring rotation as a CSS animation whose duration is `7 / ringSpin` seconds and which is
  absent at zero spin. Its markup is what the render tests assert on.
- **The readout stays truthful.** A motif constellation is captioned "Çizilen takımyıldız
  sabit bir temsildir, sayım değildir."; capability nodes say whether the lab counted them;
  the cockpit's `ChannelReadout` prints every channel as it is.

## 7. What is deliberately NOT drawn

- No idle activity beyond the reported-idle breath and a minute-scale ring drift.
- No synthesised speech rhythm: the pulse and the glow while speaking are the playback RMS.
- No estimated source count: research without counts gets the fixed, labelled motif.
- No random node positions, particle phases or ring tilts: everything is a function of index.
- No motion for `untold`, `connecting`, `unauthorized`, `last_known`, `unreachable`, or for
  the room's states on the core body.
- No parallax on `low`, and none as a claim about the system anywhere.
- No strobe: error stays at the capped `ERROR_AGITATION` and `ERROR_BREATH_HZ`.
