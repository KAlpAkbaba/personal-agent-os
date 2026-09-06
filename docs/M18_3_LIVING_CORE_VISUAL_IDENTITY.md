# M18.3 — The Living Core: a full-viewport gold/amber cognitive machine

Status: **implemented** (2026-09-07, Track W). Governing decision: ADR-0070 in
`docs/DECISIONS.md`. It extends `docs/M18_1_CORE_VISUAL_LANGUAGE.md` (ADR-0065) and
`docs/M18_CORE_RENDERER.md`; the architecture and the cross-track contract are
`docs/M18_3_LIVING_CORE_WAKE_ALARM_SPEC.md` §7 and §9.

The rule those documents state is not relaxed anywhere here: **the Core draws only what
was published or measured.** Everything below is a consequence of asking the opposite
question from the usual one — not "what would look alive", but "what is the richest
structure that can move **only** on evidence".

## 1. What changed, in one paragraph

The owner's directive: *"The existing small wireframe sphere is no longer acceptable as
the primary owner experience. /core must become a full-viewport living visual presence."*
So `/core` is now a fixed, near-black stage that IS the viewport, with no page scroll at
all; the Core covers 60–80 % of it at every aspect ratio; and the faint purple ball is
replaced by a dense gold/amber machine of nine layers — outer field, containment shell,
topology shell, three independent orbital layers, a procedural circuit layer, bounded
particle transport, floating processor fragments, an energy chamber, and a warm-white
nucleus with a gold skin. Everything else on the page is an overlay: a compact caption, a
connection dot, a control cluster that recedes after four idle seconds, and an ambient
strip. The Cockpit keeps a reduced Core and all the detail.

## 2. The palette

One family, two exceptions. Defined in `app/core/core.css` (`:root` and the `palette-*`
classes) and mirrored in `app/core/CoreScene.tsx` (`PALETTE`), so 2D and 3D agree.

| Token | Value | What it is |
| --- | --- | --- |
| `--core-warm-white` | `#fff1d0` | the nucleus' body |
| `--core-gold` | `#ffc86a` | the nucleus' skin, the chamber, the halo |
| `--core-amber` | `#f0a53a` | the energy the machine runs on; the wake surge |
| `--core-ember` | `#e0623c` | **exception 1**: a fault, and nothing else |
| eye aperture | `#5aa7e8` | **exception 2**: the camera. Cool on purpose — the privacy indicator must never be mistakable for the Core's own light |
| `--core-ground` | `#06050a` | the near-black the Core stands in |

Per-state palettes move **within** the gold family (`calm #c9a05a`, `inward #ffcf7a`,
`active #ffb347`, `voice #ffd98a`, `discovery #f2c14e`, `recall #e0b070`, `work #f0a53a`,
`held #b98a4a`, `achieved #ffe6a8`, `lab #d9a15c`, `ready #ffd27a`). `unknown` is
`#6b6250`, a desaturated warm grey: **silence does not glow.**

## 3. The layers

Outermost first. World radii are the 3D scene's; the 2D fallback draws the same
proportions from the same constants (`app/lib/uistate/scene.ts`).

| # | Layer | Radius | Drawn from | Moves on |
| --- | --- | --- | --- | --- |
| 1 | Outer field | 2.18 | instanced points, fixed spiral | `ringSpin` (drift), `glow` (brightness) |
| 2 | Containment shell | 1.52 | wireframe icosahedron | `shellSpread`, `ringSpin`, `glow` |
| 3 | Topology shell | 1.30 | wireframe icosahedron | `shellSpread`, `ringSpin`, `glow` |
| 4 | Orbital layers | 1.30 / 1.62 / 1.98 | three tori on three planes | `ringSpin` × `ORBITAL_RATES` (+0.52, −0.31, +0.19) |
| 5 | Data / circuit layer | 1.20 | one merged line buffer, procedural traces | `flowRate` (traffic), `topology` (turn), `glow` |
| 6 | Particle transport | 1.55→0.32 inward, chords outward | two instanced meshes | `inwardFlow`, `ownerVoice`, `flowRate` |
| 7 | Processor structures | 1.42 | instanced octahedra at fixed stations | `ringSpin` (slow drift), `glow` |
| 8 | Energy chamber | 0.92 | wireframe vessel | `breath`, `ringSpin`, `glow` |
| 9 | Nucleus | 0.62 | warm-white body + gold skin + wire + additive halo | `scale`, `breath*`, `glow`, `pulse`, `wakeSurge` |

Kept unchanged from M18.1, drawn only when a subsystem said so: internal rings
(0.84/1.00/1.16), connection paths (1.22), pulse shell, held boundary (1.28), error ring
(1.12), convergence ring, eye aperture (1.64), constellation (1.78), candidate field
(2.00), capability nodes (1.70), construction layers, release orbit (1.70).

New in M18.3: the **wake surge** ring at 1.86 (§5).

Depth is the layering itself — nine radii, five distinct planes, counter-rotation at three
incommensurate rates — plus the camera's slight lean towards the pointer (`parallax`,
high/balanced only). The lean is a way of seeing the layers, not a claim about the system.

**Restrained bloom, no library.** The glow is two additive/back-face shells (the nucleus
halo and the pulse shell) in 3D and one blurred disc in 2D. There is no post-processing
package, and no dependency was added for this milestone.

## 4. Which channel drives which layer

Every channel is a number on `VisualIntent`, produced in `app/lib/uistate/visual.ts` and
nowhere else. Zero channel ⇒ no motion.

| Channel | Drives |
| --- | --- |
| `scale`, `breathAmplitude`, `breathHz` | nucleus, skin, wire, chamber, halo |
| `glow` | the brightness of every layer, and nothing else |
| `shellSpread` | shells 2–3, and a sixth of it the orbitals |
| `ringSpin` | internal rings, shells (counter), orbitals (×3 rates), fragments, outer field |
| `topology` | connection paths, circuit turn |
| `flowRate` | path travellers, circuit traffic |
| `inwardFlow` | inward particles, ring contraction |
| `ownerVoice` | inward particle speed and brightness (the measured mic level, local session only) |
| `pulse` | the speaking shell and the nucleus' skin (the measured playback RMS) |
| `agitation` | one bounded offset ring |
| `restraint` | the dashed held boundary |
| `constellation*`, `field*`, `capability*`, `convergence`, `constructionLayer`, `release*` | unchanged from M18.1 |
| `wakeSurge` (**new**) | the wake ring, the nucleus' brightness, the orbitals' brightness |
| `eyeActive` | the aperture, in the one cool colour |

## 5. The wake surge (contract v3)

`docs/M18_3_LIVING_CORE_WAKE_ALARM_SPEC.md` §7 adds eight `alarm.*` states and two
`display.*` states. They are handled as follows.

- **`alarm.*` is its own channel**, like the release orbit. It never sets `kind`, never
  touches a core motion channel, and never displaces a Core that is genuinely thinking or
  speaking: a thinking Core with an alarm playing draws both.
- **The surge is drawn only while the alarm is actually sounding.** `WAKE_SURGE`: firing
  0.65, playing 0.45, greeting 0.80; `armed` 0 (a plan is a line on the strip, not a
  light); `snoozed`/`stopped`/`completed`/`failed` 0 (the surge releases). A failure is
  loud in words (severity on the strip) and silent in geometry, because failure is not
  activity.
- **A published ramp level raises it** (`intensity`, capped at 1); an absent level leaves
  the stage's own constant and the readout says the level was not reported.
- **The surge does not accelerate the rings.** Ring speed is the thinking channel. This is
  the one place the spec's sketch ("rings accelerate") was deliberately not followed: an
  alarm is not the Core thinking, and borrowing the thinking channel for it would teach the
  owner that ring speed means nothing.
- **`display.*` never reaches the Core.** There is no field on `VisualIntent` in which it
  could; it is a cell on the ambient strip and nothing else, and a test asserts the
  absence.
- **v2's `alarm.triggered` keeps its old meaning** ("a routine fired") on the release band.
  The release band reads its claim by vocabulary rather than by channel, so a ringing alarm
  cannot blank a deployment that is genuinely in flight.

`KNOWN_CONTRACT_VERSION` is 3; `MIN_SUPPORTED_CONTRACT_VERSION` is 2. A Cloud Core still
answering v2 is read **normally** — v3 only added states — and the lag is stated in one
line under the connection dot, because an empty alarm cell on such a server means "this
server cannot tell you", not "no alarm is set". A server *newer* than this build is still a
hard mismatch: we do not know its vocabulary.

## 6. Tier budgets

`TIER_BUDGETS` in `app/lib/uistate/quality.ts`; `sceneBudgetFor(tier)` derives the ceiling
and `quality.test.ts` pins it.

| | fps | detail | rings | shells | orbitals | circuit | fragments | outer field | particles | lattice | satellites | parallax | glow | DPR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| high | 60 | 4 | 3 | 2 | 3 | 64 | 12 | 90 | 160 | 48 | 64 | yes | yes | 2 |
| balanced | 30 | 3 | 2 | 1 | 2 | 32 | 6 | 48 | 80 | 24 | 32 | yes | yes | 1.5 |
| low | 20 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 12 | no | no | 1 |

`sceneBudgetFor`: **high** 36 drawables / 398 instances / 160 particles; **balanced** 33 /
206 / 80; **low** 22 / 32 / 0. Instances = particles + 2 × satellites + 8 capability nodes
+ outer-field points + fragments. The figures are maxima: a channel at zero mounts nothing.

`low` drops the outer field, the circuitry, the fragments, the shells, the particles, the
paths, the parallax and the glow **whole** rather than shrinking them, and keeps one ring
and one orbital layer so the structure stays recognisably the same machine.

A hidden tab draws nothing (`frameloop="demand"`, and the frame body returns before any
arithmetic); reduced motion settles the structure in one long step and draws it once per
intent change; no WebGL falls back to `CoreFallback2D` in the same identity.

## 7. Minimal mode's layout

`app/lib/uistate/stage.ts`, pure and tested (`tests/uistate/stage.test.ts`).

- `stageSizeFor(width, height)` returns the square stage's side and the resulting
  coverage. The Core's principal structure spans `CORE_FILL` of the stage — a figure
  **derived from the camera** (`CORE_SPAN_RADIUS / VIEW_HALF_EXTENT` ≈ 0.86 at distance
  6.0, fov 42), so changing the framing moves the layout with it instead of quietly making
  the coverage claim false.
- Targets by band: portrait 0.74, square 0.78, landscape 0.72, ultrawide 0.66 — of the
  usable short side, where "usable" is the viewport less a 10 % margin top and bottom for
  the overlays. The result is clamped so coverage is **always** within 60–80 %, and never
  exceeds the viewport.
- Overlays: the caption (compact `StateReadout`), the connection dot, the control cluster
  (voice, eye, tier, 2D, fullscreen, cockpit) and the ambient strip (presence, camera,
  screens, alarm, release).
- **The cluster fades, the strip does not.** `controlFadeReducer` takes the cluster to 25 %
  after 4 s without pointer, key or focus activity, and back to full on any of them; it
  never fades while the pointer is over it or focus is inside it. Fading is not hiding: the
  buttons keep their labels, their ARIA state and their place in the tab order at every
  opacity. The strip is deliberately excluded, because a parent's opacity cannot be undone
  by a child and a faded privacy assurance is not one.

## 8. Fullscreen and the PWA

- `document.documentElement.requestFullscreen()` is called from **exactly one** callback,
  bound to one button, and from no effect anywhere (a structural test reads the source).
  No user-gesture restriction is bypassed; a refusal is swallowed and the page stays as it
  was. While fullscreen is on, the same button reads "Tam ekrandan çık" and names Esc.
- `app/manifest.ts`: `display: "standalone"`, `start_url: "/core"`, background and theme
  `#06050a`, `lang: "tr"`, SVG icons in `apps/web/public/` (`icon.svg`, and
  `icon-maskable.svg` inside the 80 % safe area).
- **No `display_override: ["fullscreen"]`** — fullscreen stays a gesture — and **no service
  worker**: a cached shell that rendered yesterday's state would be the most expensive lie
  in the product.

## 9. The Cockpit

`/core/cockpit` keeps its reduced Core, its channel telemetry and all its panels, and gains
two:

- **Alarmlar** — `GET /v1/alarms`, one row per alarm with its state, local time,
  recurrence, media and device.
- **Ekran / Ortam** — `GET /v1/ambient/policy` plus each device's own heartbeat status
  (`GET /v1/devices`, `status` block): the policy's flags and thresholds, and per device
  whether the screen is on, off or untold.

Both routes belong to Track C and do not exist yet. `Loaded<T>` therefore gains a fourth
outcome — **`absent`** — and a 404 renders "Henüz yok." with the path, never an empty list
(which would claim there are no alarms) and never a fabricated row. A device that sent no
`status` block is drawn as "cihaz durumu bildirilmedi", never as a screen presumed on.

**Nothing in the renderer decides physical policy.** The cockpit is read-only, the Core has
no write path, and display power is decided in Cloud Core and executed on the device.

## 10. What is deliberately NOT drawn

- No motion at all for `untold`, `connecting`, `unauthorized`, `last_known`, `unreachable`,
  or for the room's states on the core body — the new layers included.
- No ring acceleration from an alarm (§5), and no wake surge for a stage that is not
  making a noise.
- No `display.*` anywhere near the Core.
- No post-processing bloom, no volumetric fog, no lens flare: the depth is geometry.
- No idle activity beyond the reported-idle breath and the minute-scale drift of the rings
  and orbitals.
- No random position, phase or tilt anywhere: everything is a function of index.
- No parallax on `low`, and none as a claim about the system anywhere.
- No service worker, and no offline cache of a state that has stopped being true.

## 11. A picture, for an owner who has not seen it

No browser is launched in this repository's test environment, so the WebGL scene has never
been rendered here. The 2D fallback carries the same identity from the same intent, so it
is written out for five real states:

```
pnpm --filter @pagentos/web exec vitest run tests/preview/core-preview.test.tsx
  -> apps/web/preview/core-idle.svg
     apps/web/preview/core-listening.svg
     apps/web/preview/core-speaking.svg
     apps/web/preview/core-researching.svg
     apps/web/preview/core-alarm-playing.svg
     apps/web/preview/core-preview.html   (all five, on the real ground)
```

They are gitignored build output. Scale, depth and light can only be judged in a real
browser — that is owner qualification A.
