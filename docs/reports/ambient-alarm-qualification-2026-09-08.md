# Ambient display + alarm + continuous operation — production qualification (2026-09-08)

Owner directive: run a bounded qualification of the already-built ambient display / alarm /
continuous-availability behaviour, in parallel with M26, without pausing it. Everything
below was measured against the real production Cloud Core (release `20db267`) and the real
Windows agent on the owner's machine. Nothing here is asserted from a unit test where the
directive asked for production behaviour, and nothing that could not be measured is claimed.

**One correction up front, because I got it wrong mid-run and said so out loud.** At 20:23Z
the device advertised 40 capabilities including `desktop.display_off`, and I reported that
the elevated agent update had landed. It had not. That was the **staged 0.6.0 candidate
inside its health window**; it failed qualification at ~92.6 s and rolled back, and by
20:26Z the device was back to its 29-capability 0.1.0 runtime. The owner's own incident
report arrived and explained it. The rollback worked exactly as designed.

---

## AMBIENT DISPLAY POLICY

**decision** — `PROVEN_REAL` (production). Read from `/v1/ambient/explain` at 19:53Z with
the pre-update runtime: `action=none, reason=display_not_on`, because the device could not
report a display state. That is the policy's first rule working (`docs/M18_*` §1.4:
*uncertain means ON*) — it refuses to darken a screen it cannot see. At 20:24Z, once the
candidate's heartbeat carried `display: {state: "on"}`, the same route answered
`action=none, reason=holdoff:alarm_wake`, naming the alarm that had just rung.

**production values, recorded before anything was touched and unchanged since**:
`auto_off_enabled=false`, `off_when_away=true`, `off_when_asleep=true`, `wake_on_return=true`,
`away_after_s=900`, `asleep_after_s=600`, `asleep_min_confidence=0.7`, `input_holdoff_s=600`,
`command_holdoff_s=900`, `alarm_holdoff_s=1800`, `return_holdoff_s=600`,
`asleep_after_outside_quiet_s=1800`, `camera_unknown_grace_s=120`, `keep_on=false`,
`quiet_hours=unset`. **No temporary qualification policy was applied**: the decision path
was reachable through the owner's own test route without weakening a threshold, so nothing
had to be changed and nothing had to be restored.

**dispatch** — `PROVEN_REAL` (production). `POST /v1/ambient/test-display` armed the real
display-off; the production routine clock (10 s interval, 931 ticks, no errors) picked it up
and issued it through the same `WakeSequence.display_off` the automatic policy uses, with
reason `owner_test`. Measured twice, at 19:59:45Z and 20:25:00Z.

**read-back** — `NOT_YET_PROVEN` for physical darkness. Both dispatches ended
`execution_status=failed, terminal_status=failed, error_class=no_capable_device`: the
installed 0.1.0 runtime advertises no display capability. The receipt says so, the owner's
sentence says so ("En son ekran işlemi 23:25'de doğrulanmadı (no_capable_device)"), and
nothing anywhere reported success. **The honest outcome of a chain that works up to a
capability the deployed agent does not have.** It becomes provable when the 0.6.0 staged
update passes its health qualification — which is the separate production incident now under
repair.

**proof class**: policy decision and dispatch `PROVEN_REAL` on production; the physical
panel `NOT_YET_PROVEN`, blocked on the agent update, with the blocking reason measured
rather than assumed.

---

## THE INVARIANT THE OWNER PUT FIRST: display off ≠ Windows sleep

`PROVEN_REAL`, structurally, and this is the strongest result of the track.

`services/api/tests/unit/test_no_machine_suspend_path.py` (28 facts) reads the source of
both halves rather than watching one code path:

- **No Windows power-state API exists anywhere** in the device agent or the Cloud Core —
  `SetSuspendState`, `SetSystemPowerState`, `ExitWindowsEx`, `InitiateSystemShutdown`,
  `PowrProf`, `shutdown.exe`, `Restart-Computer`, `Stop-Computer`. There is no call to
  reach, on any path, from any utterance.
- **The one darkening mechanism is a monitor power message** — `WM_SYSCOMMAND` /
  `SC_MONITORPOWER` with the off parameter, in exactly one file, sent with a timeout so one
  hung window cannot stall it. It changes what the panel does, not what the OS is doing.
- **No capability name** in the protocol offers to sleep, hibernate, suspend, shut down, log
  off or restart.
- **No standing execution-state claim**: `ES_CONTINUOUS`, `ES_SYSTEM_REQUIRED` and
  `ES_AWAYMODE_REQUIRED` are refused across the whole agent, not only in the file that
  currently calls the API.

That last rule exists because the guard's first run flagged `SetThreadExecutionState` in
`DisplayWake.cs` and sent me to read it: it is the careful use — the display flag alone,
deliberately without the continuous flag, resetting the idle timer once. So the rule narrowed
to what would actually be wrong.

At the router, measured through the real transcription boundary: **"Bilgisayarı uyut." and
"Bilgisayarı kapat." resolve to NO intent at all.** Now permanent corpus cases
(`d.neg.suspend.*`, with paraphrase and ASR variants), which also forbid answering a suspend
request with a display action — darkening a screen is not the nearest safe reading of
"suspend the machine", it is a different act. Corpus: **1411 passed**.

---

## PAGENTOS WHILE DISPLAY OFF

`NOT_YET_PROVEN` as posed, and the reason is honest: **the displays were never actually
darkened**, because the capability was absent, so a "while the displays are off" window
could not be created. Reporting anything else would be inventing the state the question is
about.

What *was* measured continuously across the whole qualification, including through a real
alarm firing: Cloud Core `status=ok` with `db, redis, object_store, temporal, broker,
artifacts, voice, memory, selfhealing, evolution, security, identity, mobile, voice_realtime,
temporal_worker, research, routine_clock` all ok; the routine clock running at its 10 s
interval with `last_error=null`; the Windows agent online with a heartbeat age under 6 s
throughout; and real device commands succeeding (`desktop.alarm_start`, `desktop.alarm_stop`)
during the window.

---

## TEST ALARM — `PROVEN_REAL` (production, end to end at the Cloud Core layer)

One bounded qualification alarm, armed through the production REST surface at 20:01:59Z:
`is_test=true`, `max_play_seconds=120`, `timezone=Europe/Istanbul`, labelled as a
qualification alarm, scheduled for 20:04:29.486Z (23:04 local).

| moment (UTC) | event |
|---|---|
| 20:01:59.5 | `alarm.scheduled` — durable row, `state=SCHEDULED` |
| 20:04:35.7 | `alarm.firing` — 6.2 s after the scheduled instant, inside one 10 s clock tick |
| 20:04:36.0 | `alarm.display_waking`, then a real `display.wake` dispatch |
| 20:04:36.0 | display wake → `failed`, `no_capable_device` (the same capability gap) |
| 20:04:36.0 | `alarm.media_starting` |
| 20:04:37.0 | `alarm.playing` |
| 20:05:07.1 | `alarm.greeting` |
| 20:06:39.5 | `alarm.completed`, `terminal_reason=max_play_seconds` |
| 20:06:39.6 | `alarm.cleaned_up` |

- **durable trigger**: `PROVEN_REAL`. Survived as a row, fired from the production clock.
- **exactly once**: `PROVEN_REAL`. One `firing_id` (`1208eba3…`), one completion, no repeats.
- **display wake**: dispatched for real, refused honestly — `NOT_YET_PROVEN` physically.
- **media**: played `media_kind=tone_fallback`. **This is the owner's "beep beep" complaint,
  and it is now root-caused** (see BUGS below).
- **volume ramp**: the policy is real and durable (`start 0.15 → end 0.6 over 20 s`) but was
  never applied to a device that could not play the media path — `NOT_YET_PROVEN`.
- **greeting TTS**: a `alarm.greeting` row 30 s after playing, on the ramp schedule the
  policy predicts. The audio itself is `PROVEN_PROXY`; OpenAI has no credits (owner item 29),
  so **loopback/STT is `NOT_YET_PROVEN`** and no acoustic claim is made.
- **bounded self-stop and cleanup**: `PROVEN_REAL`. Ended itself at its 120 s test bound and
  released, with no orphan alarm and no leftover schedule.
- **physical audibility**: `NOT_YET_PROVEN`, deliberately. Nobody listened.

---

## NO STALE "AWAY → OFF" LOOP AFTER A WAKE — `PROVEN_REAL`

The alarm set an `alarm_wake` holdoff until **20:34:35Z**, thirty minutes, and it was set
*even though the wake dispatch itself failed*. Twenty minutes later, with the device now
reporting `display: on` and presence `present`, `/v1/ambient/explain` still answered
`action=none, reason=holdoff:alarm_wake` and told the owner why: *"Ekranlar açık kalıyor
çünkü alarm az önce çaldı; 23:34'e kadar otomatik kapatma beklemede."* An alarm wake
outranks the ambient policy for its holdoff, on production, observed rather than reasoned.

---

## INPUT WAKE — `PROVEN_AUTOMATED` only

The device's own heartbeat carried `input_active: true, input_idle_s: 0.2` throughout, so
the input path is demonstrably live. The full "display off + real keypress → immediate wake"
sequence was **not** attempted: no display was ever off, and the directive forbids
manufacturing fake owner input to claim a real proof. The state-machine half remains covered
by the existing ambient suites.

---

## THE OWNER WAS NOT AWAY

Stated plainly because it bounds several rows above: presence read `present`, confidence
0.85, with `input_active: true` — the owner was at the machine and working throughout. The
away → sustained-away → display-off scenario could not be created without either faking
perception into the production world model or darkening a screen someone was using. Neither
was acceptable, so the away path stands on its existing unit coverage (75 ambient facts, 99
presence facts) rather than on a production claim.

---

## M26 DURING THE TEST

- **before**: both halves merged locally, the reconciled tree green.
- **during**: untouched. This qualification exercised production; M26 is not deployed
  (`GET /v1/executive/runs` → 404 on production, as expected), so there was no production
  executive run that could be lost, orphaned, reset, duplicated or cancelled.
- **after**: full API unit suite **6758 passed, 2 skipped**; the corpus **1411 passed**.

---

## CONTINUOUS AVAILABILITY

- **task loss**: none.
- **duplicate actions**: none — one firing id, one completion, one cleanup.
- **service interruption**: none. Every health check stayed ok across the alarm, and the
  Cloud Core's uptime ran unbroken from 17:24Z through 20:26Z.

---

## BUGS FOUND

1. **A normal alarm plays the tone even when the owner has set a default wake song.**
   `app/alarms/service.py::_media_source` consults the remembered wake song only when the
   owner *named* music in the request; with no media named it returns
   `{"kind": "tone"}` unconditionally. So "Yarın 07:30'da beni uyandır." can never use the
   saved song, which is exactly the owner's report and their directive's item 2. The
   YouTube-primary/tone-fallback design in `sequence.py::fire` is correct and already
   built — the defect is one branch upstream of it. **Being fixed on its own track**, with
   the regression test the owner asked for by name ("with a configured playable wake song,
   a normal alarm is not a beep").
2. **The staged 0.6.0 update fails health and rolls back** — the owner's own incident
   report, reproduced in my measurements: 29 caps → 40 caps during the candidate window →
   29 caps after rollback. **Under repair on its own track.** It is the single blocker
   between this report's `NOT_YET_PROVEN` rows and real proof.

## REGRESSIONS ADDED

- `services/api/tests/unit/test_no_machine_suspend_path.py` — 28 facts, the machine-suspend
  invariant, proven to bite (it flagged a real call on its first run).
- Corpus `d.neg.suspend.1..4` with variants — the suspend utterances reach no intent and no
  display tool.

## CI

Both additions are on `main` and go through the normal pipeline with the rest of the day's
work.

---

## WHAT WOULD MAKE THE REMAINING ROWS REAL

One thing: the agent update passing its health qualification. Then, in a single unattended
window with the owner genuinely away, the same three commands in this report re-run
end to end — the display test, an armed alarm, and the explain route — turn
`NOT_YET_PROVEN` into `PROVEN_REAL` for the panel, the wake and the volume ramp, with no
code change. The tone-versus-music question is separate and is being fixed now.
