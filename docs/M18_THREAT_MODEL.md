# M18 — security and privacy threat model (2026-09-06)

M18 gives PersonalAgentOS a camera, a schedule and a deployment button. Each of those is
a genuinely new class of exposure, and none of them existed in M17:

| New capability | What it could do wrong |
| --- | --- |
| Active Eye | continuously watch the owner's room, and archive it |
| Presence inference | become an identity claim, and then an authority |
| Routines and alarms | act on the owner's behalf while they are asleep or absent |
| Owner-authorised release | put unreviewed code into production |

This document is about what stops each of those, in code rather than in intention. Where a
control is a *property of the code* it is named with the file that holds it; where it is a
decision that could be reversed, it says so.

## 1. The perception boundary

```
camera ──► local perception ──► structured observation ──► Cloud Core
           (owner's device)     (7 bounded fields)
```

**The invariant.** Only `person_present`, `presence_confidence`, `activity_level`,
`posture`, `awake_state`, `observed_at` and `source` may cross. Nothing else — and in
particular no frame, thumbnail, crop, embedding or descriptor of a face.

**How it is enforced**, in `services/api/app/presence/observations.py`:

* the field set is *closed*: a payload with any eighth key is refused before it is
  type-checked, so a new field is a deliberate schema change and never an accident;
* keys are screened by *shape*, not by exact name — normalised (lowercased, non-alphanumerics
  stripped) and refused if they contain `image`, `frame`, `base64`, `snapshot` and friends,
  so `frameBase64`, `frame_base64` and `Frame-Base64` are one rule, not three;
* values are screened independently, because a boundary that only reads field names is one
  rename away from a bypass: a `data:image/...;base64,` URI or a long base64-shaped string
  is refused even under a legitimate key such as `source`;
* it **refuses rather than redacts**. A rejected request is recoverable; a silently mutated
  one is a record nobody can trust afterwards. This is the same rule as
  `app/ledger/screening.py` and the same reason.

**Residual risk.** The boundary protects the Cloud Core from the device, not the device from
itself. A compromised local-perception client can still see the room — it is the thing
holding the camera. What the boundary guarantees is that nothing it sends can *become* a
durable image server-side, and that the ledger and World Model can never accumulate one.

**Not built, deliberately:** any path that lets a frame leave the machine. The spec reserves
that as a separate, owner-visible permission path; it does not exist, and adding it must not
be a flag on this one.

## 2. Perception is never authentication

The strongest rule in M18, because it is the one whose violation would be invisible.

No function in `app/presence` returns a session, a token, a scope or an owner identity —
there is nothing to mistake for one. Every presence endpoint sits behind
`require_owner_session` exactly like every other write surface, and
`test_presence_never_grants_or_influences_authority` proves the negative directly: it
establishes a confident presence state through an authenticated client, then shows an
unauthenticated client still gets 401 on every presence route.

Nothing in the release path reads presence. Face and body observations carry no
production-release authority, and the release lifecycle's own guard constants
(`OWNER_ONLY_STATUSES`, `LAB_FORBIDDEN_STATUSES` in `app/evolution/service.py`) are the
only thing that admits a status change.

**Future owner voice identification** must land the same way: as *one signal among several*
for personalisation, never as root authorisation. Voice identity alone is not authority
(ADR-0055), and this document is where that has to stay written down before the capability
exists.

## 3. The eye's default, stated plainly

`is_eye_enabled` returns **true** when no enable/disable has ever been recorded. That is a
choice worth naming rather than burying:

* the server-side flag governs whether the Cloud Core will *accept* observations. It does not
  open a camera and cannot;
* the only thing that opens a camera is the owner's own device client, behind the browser's
  own permission prompt, which the owner grants explicitly and can revoke in the browser;
* so the durable default is "the system will listen if the device offers", not "the system is
  watching".

Disable is durable (an `eye.disabled` ledger row), immediate (read fresh from the database on
every camera-sourced observation, with no in-memory flag that could go stale against a
disable recorded seconds ago), and observable (an `eye.disabled` UI-state event).

**The indicator may not lie.** The Core distinguishes `disabled` from `untold`: an indicator
that reads as "off" because nobody has said anything would be a privacy assurance nobody
gave. An `eye.*` state the client cannot read is flagged as unreadable rather than falling to
"off" (`apps/web/app/lib/uistate/ambient.ts`, asserted in `tests/uistate/ambient-render.test.tsx`).

## 4. Presence as an inference, and the harm of certainty

Every presence state carries a confidence, the signals behind it and an `observed_at`, and
the system says `LIKELY_ASLEEP confidence=0.86` rather than `OWNER_IS_ASLEEP`. The exposure
if it did not: a display turned off, an alarm suppressed, or a greeting delivered on a
confident-sounding guess.

Three controls:

* **no state from a single observation**, and a transition needs sustained evidence over a
  configured span — policy as data in `app/presence/engine.py`, not conditionals;
* **staleness is a first-class answer.** An observation that has aged out degrades to
  `UNKNOWN`, never to "still present", on both sides of the wire: the engine owns the policy
  and publishes it as `ttl_s`, and the client honours the publisher's figure over its own
  default rather than guessing;
* **conflicting signals lower confidence** and record `reason="conflicting_signals"` instead
  of silently picking a winner.

The greeting policy is the worked example: a brief movement at 03:00 is not a morning,
because a greeting requires a prior sustained rest period, then sustained wake evidence, then
a plausible hour, then an un-elapsed cooldown — four independent gates, each testable alone.

## 5. Routines: acting while nobody is watching

A routine is the first thing in this system that acts without the owner initiating it, so:

* **there is no background timer.** Evaluation happens only on an explicit
  `POST /v1/routines/evaluate`. A routine engine that polls itself is a scheduler nobody can
  see; this one cannot fire without something asking it to;
* **every transition writes exactly one ledger event** — created, armed, triggered, executed,
  skipped, cancelled — against the closed vocabulary. An unrecorded transition would be an
  audit gap in precisely the capability that most needs an audit trail;
* **actions are bounded.** Media actions play the item the owner named and do not choose
  content on the owner's behalf; wake volume ramps and has a ceiling; no action bypasses a
  CAPTCHA or an anti-bot measure, and none is implemented that could;
* **display power is the only machine-state action, and only off.** M18 v1 does not shut
  down, reboot, hibernate or suspend on a sleep inference. Turning a display off is undone by
  moving the mouse; suspending a machine mid-research is not.

**Residual risk.** A routine created with a bad trigger can still act at a bad time. The
mitigations are visibility (the ledger, the UI-state band) and reversibility (the actions
chosen), not prevention.

## 6. The release path

The full argument is ADR-0055; the security-relevant summary:

* **Evolution has no autonomous production authority, and cannot mint one.** The authority
  kernel's tokens are module-private, the classes are runtime-final via `__init_subclass__`,
  and `assert_genuine_authority` requires an exact type *and* its mint.

  **Two bypasses have been found here, and both were found by someone building against the
  kernel rather than reading it.** The first (2026-09-05) was a live subclass forge. The
  second (2026-09-06) was subtler and is worth stating in full, because the shape of it will
  recur: `EvolutionService.advance()` classified a transition by its *target* alone.
  `QUARANTINED` and `REJECTED` are ordinary lab-reachable targets early in the lifecycle — a
  candidate can be quarantined mid-BUILDING with no owner involved — but they are also legal
  from `QUALIFYING` and `ROLLING_BACK`, and neither target is itself production-side. So a
  lab actor could divert an opportunity that was mid-deployment into `quarantined` using only
  its default `propose_candidate` grant. The lifecycle table does not prevent that; only the
  authority check does. Now: once an opportunity is on the production side of the wall,
  leaving it *to anywhere* requires production authority.

  The lesson is about where to look. Both defects lived in the gap between a table that is
  correct and a guard that reads only half of it, and neither was visible from the guard's own
  tests. A third is a reasonable expectation rather than a surprise.
* **Owner authorisation is a separate privileged capability**, bound to the authenticated
  owner session — not to a voice, not to a presence, not to anything Evolution-generated code
  can construct.
* **Asking is not authorising.** `Bunu canlıya alabilir misin?` is a question and starts
  nothing. Only an explicit imperative may open an authorisation request, and risk-sensitive
  releases require a second confirmation.
* **The Core cannot act.** It is a pure consumer with no write path; the absence of a write
  endpoint is what stops a client claiming a state it is not in, and the release band renders
  no button, form or input at all.

## 7. What the UI itself may leak

The UI-state bus carries state, never content. `_clean_metadata` keeps only bounded numbers,
bools and short tokens, and refuses keys whose normalised form contains `text`, `transcript`,
`audio`, `secret`, `token`, `password`, `content`, `body` and friends. Lists and dicts are
dropped as content-shaped — which is why publishers reduce a collection to its length before
sending rather than hoping it survives.

The Core client never captures, requests or persists owner audio, and there is no Web Audio
code in it. `agent.speaking` pulses from the bounded intensity figure the event already
carries, and that figure is never described to the owner as a measurement of their voice.

## 8. Test safety

Two rules with a history behind them:

* **no browser tests for the Core.** The 2D view is a pure function of the intent and is
  animated entirely by CSS, so `react-dom/server` markup carries the full assertion. A
  headless browser would add orphan-process and desktop-pollution risk for no extra coverage —
  and that risk was once realised on this owner's desktop, which is why
  `services/browser/tests/test_test_isolation_guards.py` exists.
* **camera tests never persist real owner imagery.** The observation fixtures are the seven
  structured fields and nothing else; the privacy tests use synthetic base64-shaped strings,
  not photographs.

## 9. Open items

* ~~The routine action dispatcher does not exist.~~ Built (ADR-0060): `app/routines/dispatch.py`
  routes each action kind to the subsystem that owns it; a failed or refused action gets its
  own ledger event and a `UiState.ERROR`, critical for an alarm. The wake alarm is a ramp on
  both sides of the wire — refused at or above 0.5 start, clamped at 0.85 and reported, the
  generated samples clamped independently, never the Windows master volume, and it always
  stops. Display-off is built on the companion and unreachable twice over: advertised and
  routed only behind `PAGENTOS_AGENT_DisplayPowerEnabled` (default false), and refused by
  Cloud Core behind `DISPLAY_ACTION_QUALIFIED = False`. Neither gate knows about the other.

* ~~The device-side local-perception client does not exist yet.~~ Built: `apps/web/app/lib/eye/`
  (ADR-0058). The frame-never-escapes property is **structural**, not a convention the
  caller has to keep: `FrameSource.sample(reduce)` takes the reducer in and returns only a
  108-number luminance grid, so the pixel buffer is a local inside one synchronous block and
  there is no signature anywhere in the client through which a frame can leave its source.
  (It began as `capture()` returning `{data, width, height}`, which was safe as written but
  made the guarantee depend on every future caller behaving; the docstring claimed more than
  the code enforced, and in this file of all files that gap had to close.) Disable-immediacy
  is proven in `apps/web/tests/eye/perception.test.ts`, including the reentrant mid-tick
  race and an already-in-flight observation POST.
* A camera disable that FAILS is now visible: it lands on the audit record and publishes a
  critical `agent.error` on the presence channel, so the Core can say the camera did not
  close. It used to be a debug log, which is the wrong place for a privacy control that did
  not take effect.
* Display-off has its own separate qualification, deliberately not folded into the main M18
  owner run, because a wrong inference there interrupts unrelated owner work.
* **One session owns the microphone.** The first real run (2026-09-06) found `/core` and
  `/voice` were two pages with two rigs; opening both would have meant two `getUserMedia`
  captures and two OpenAI realtime sessions — the owner's voice leaving the machine twice,
  billed twice, with two speakers answering. The voice session is now a single client-side
  store (ADR-0061): one rig, one capture, one realtime session, and a second `connect()`
  while one is live is refused rather than stacked. `/voice` reads that store; it cannot
  create a second. The owner qualification asserts the server-side half — no two web
  realtime sessions of the run were open at once — from the sessions' own started/ended
  instants; the client half is a unit-suite property, because a harness cannot count
  microphones.
* **An open camera is not presence.** Also from the first real run: the Core said "Sahip
  durumu bilinmiyor" with the eye on, and that was the *correct* half of a two-part failure.
  `device.camera_state` is an evidence fact about a device; `owner.presence` is a runtime
  inference that needs observations, and with the eye on and nothing observed the World
  Model carries the first and refuses the second by name (`no_observations_yet`,
  `test_an_open_camera_is_not_evidence_of_presence`). The other half — a held state never
  republished, so a live claim looked like no claim — is fixed by a heartbeat at half the
  TTL. Neither fix infers anything from the camera being open.
* **The presence client was a motion sensor at the wrong unit.** A seated owner was read as
  `away` for eleven minutes because presence was "whole-frame mean luminance changed by more
  than 2% between two samples". The rewrite (ADR-0062) counts changed grid CELLS and keeps a
  90 s memory of the last real movement, distinguishes an exit burst from sitting still,
  and caps absence confidence at 0.75 because a motion sensor has no positive evidence of
  an empty room. The privacy property is unchanged: still a 108-number grid, still no frame
  leaving its source, and the diagnostics on the Core are an age, a level and a fraction.
* Owner voice identification is not built. When it is, §2's rule is the acceptance criterion.
