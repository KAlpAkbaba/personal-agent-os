# M18 — Holographic Core, Active Eye, Ambient Presence (2026-09-05)

From *a system you talk to* to *a presence in the room*. M18 gives PersonalAgentOS a body:
something the owner can see thinking, a way to notice they are there, and the ability to act
on time rather than only on request.

One rule governs the whole milestone, and it is the same rule as ADR-0052:

> **The Core shows what is true.** A visual state exists because a subsystem entered it.
> Nothing animates to look busy, nothing claims a capability it does not have, and an
> uncertain observation is drawn as uncertain.

That rule is why this is not "merely a UI milestone". A renderer that invents activity is
worse than no renderer, because it teaches the owner to distrust everything else the system
says.

## Architecture

```
   camera ──► LOCAL PERCEPTION ──► structured observations ──► Presence Engine
   (browser)   (ephemeral frames)   (no imagery)                 │
                                                                 ▼
   voice / research / memory / goals / evolution ──►  app/uistate  ──► Holographic Core
                                                     (contract v2)      (React Three Fiber)
                                                                 │
                                     Routine Engine ◄────────────┤
                                     (trigger→conditions→actions)│
                                                                 ▼
                                                World Model + Activity Ledger
```

Five deliverables, in dependency order:

| # | Package | What it owns |
|---|---|---|
| 1 | `app/uistate` (contract v2) | the state vocabulary the Core renders — **done** |
| 2 | `app/presence` | evidence-backed presence/wake states, fusion, greeting policy |
| 3 | `app/routines` | durable trigger → conditions → actions, alarms, media, display |
| 4 | `apps/web` Core | the living 3D Core, Minimal and Cockpit modes, Active Eye client — renderer detail in `docs/M18_CORE_RENDERER.md` |
| 5 | `app/evolution` release path | the ADR-0055 lifecycle, risk tiers, preflight, rollback |

## 1. Presence and the wake model

States: `PRESENT`, `AWAY`, `RETURNED`, `AWAKE`, `RESTING`, `LIKELY_ASLEEP`, `UNKNOWN`.

Every state carries **confidence**, the **signals** that produced it, and an
**observed_at**. The system says `LIKELY_ASLEEP confidence=0.86`, never `OWNER_IS_ASLEEP`.
Nothing is classified from a single frame: a state changes only on sustained evidence over
time, and the duration required is part of the policy, not a magic number in a branch.

Signals, none of them individually sufficient: visual presence, coarse activity level,
posture bucket, how long the current state has held, keyboard and mouse activity, recent
voice interaction, time of day, whether PagentOS has active tasks, recent display activity.

**Staleness is a first-class answer.** A camera observation from forty minutes ago is not
evidence about now; the World Model already distinguishes this (`is_stale`, per truth kind)
and presence uses the same machinery. An expired observation degrades to `UNKNOWN`, never
to "still present".

### Greeting policy

A morning greeting is a *transition*, not a detection. It requires: a prior sustained
`RESTING`/`LIKELY_ASLEEP` period, then sustained `AWAKE` evidence, a plausible time context,
and no greeting already delivered in the cooldown window. **A brief movement at 03:00 is not
a morning.** The policy is data, inspectable and testable, not scattered conditionals.

## 2. Active Eye

```
CAMERA → LOCAL PERCEPTION → structured observations → World Model / Activity Ledger
```

* raw video never continuously uploads to Cloud Core;
* raw frames are never continuously persisted — they are ephemeral by construction;
* only structured observations leave the perception layer:
  `person_present`, `presence_confidence`, `activity_level`, `posture`, `awake_state`;
* if a future capability needs a frame to leave the machine, that is a **separate,
  owner-visible permission path**, not a flag on this one.

Camera activity is obvious: an indicator, the selected camera, the current perception state
and its confidence, the privacy mode, and an enable/disable control. `Gözünü kapat`,
`Kamerayı kapat` and `Beni izleme` stop perception immediately.

**Perception is never authentication.** Presence is an interaction signal. No face or body
observation may act as owner identity, and none may carry production-release authority.

## 3. Routine Engine

`TRIGGER → CONDITIONS → ACTIONS`, durable, owner-visible, ledger-recorded at creation,
arming, execution and cancellation.

Triggers: absolute time, recurring schedule, `owner.returned`, `owner.awake`,
`owner.likely_asleep`, presence changes. Conditions: presence, quiet hours, display state,
an active task, policy permissions. Actions: a voice briefing, an alarm, media playback, a
browser action, a display action.

Media actions name the item the owner asked for. The system does not choose content on the
owner's behalf when a specific item was requested, does not bypass CAPTCHA or anti-bot
measures, and does not force extreme volume — wake volume is configurable and ramps.

## 4. Display power

`LIKELY_ASLEEP` + sufficient confidence + sustained inactivity + no recent interaction + no
policy blocker → **display off**. Recorded in the ledger.

M18 v1 will **not** shut down, reboot, hibernate or suspend the machine on a sleep
inference. Turning a display off is reversible by moving the mouse; suspending a machine
that is mid-research is not. Background work keeps running.

## 5. Owner-authorised deployment (ADR-0055 realised)

```
SHADOW_READY → OWNER_APPROVAL_REQUIRED → OWNER_AUTHORIZED → QUALIFYING
             → DEPLOYING → VERIFYING → LIVE
    failure:   DEPLOYING|VERIFYING → FAILED → ROLLING_BACK → previous LIVE restored
```

Risk tiers 1–5 (UI/additive → identity/root/secret boundary) decide how much confirmation
is required; tiers 3+ need explicit second confirmation. **Voice alone is never root
authority** — authorisation binds to the authenticated owner session and the existing
production-authority mechanism, and remains unmintable by Evolution-generated code.

`Bunu canlıya alabilir misin?` is a question and starts nothing. `Canlıya al` may open an
authorisation workflow.

Preflight before any mutation: candidate is SHADOW_READY, version/diff known, source
committed, tree clean, tests passed, security review acceptable, benchmark passed, migration
impact known, dependencies known, rollback point exists, and the component genuinely needs
deploying. After: health, version, source/install/runtime provenance, required
qualification — `LIVE` only after verification, else automatic rollback and a report.

## 6. Performance

Quality tiers High / Balanced / Low, graceful degradation without WebGL/WebGPU, and reduced
work when the tab is hidden, the display sleeps or the machine is constrained.

**Cognition never depends on the renderer.** If the Core drops to one frame per second or
fails to start entirely, research, memory, goals and evolution continue exactly as before.

## 7. What "done" means here

Not unit tests. The acceptance is one short real owner run proving: the Core renders and
shows genuine state; listening/thinking/speaking transitions are real; research, memory and
evolution state can appear; the real camera can be enabled and its indicator is truthful;
a real presence transition is detected; disabling the Active Eye actually stops perception;
a routine can be created and a short test alarm fires; an owner-selected media action
executes; the ledger records it; **no raw camera archive is created**; a SHADOW_READY
candidate is visible; asking about deployment does not deploy; and production authority
stays owner-controlled.

Display-off gets its own separate qualification, once it can run without risking unrelated
owner work.
