# ADR-0251 addendum (proposed; the lead numbers it): "gone" is terminal for the network handlers

Task `voice-gone-is-terminal`, cycle d20261003. Closes the "Finding for the lead" of ADR-0251 addendum 1.

## Context
After the server answered 410, the web voice controller patched `state: "closed"` on both paths that hear it
(`onReportFailure`'s `gone` branch for `/events`, `runReattachLoop`'s `error.gone` branch for `/attach`), but
`onNetworkLost` / `onNetworkChange` guarded only on `closing` and on `state === "reconnecting"`. A network flap
took the closed controller back to `reconnecting` ("yeniden bağlanıyor" for a dead session), and each return of
the network cost one `POST .../attach` to the gone session. On the attach path, the queued `network_lost`
telemetry was also POSTed to the gone session after the 410.

## Decision
A named `gone` flag, not the existing `closing`.
- `markGone()` (called from both 410 branches) sets `gone`, clears the reattach timer and ends the reporter
  (`reporter.end("gone")`), so the queued telemetry is not sent to the dead session.
- `onNetworkLost` returns when `gone`; `reattachLoop` returns at once when `gone`. `onNetworkChange` then does
  nothing either: offline goes to `onNetworkLost` (guarded), online acts only in `reconnecting`, which a gone
  controller never re-enters.
- `connect()` clears `gone` together with `closing` and `closedByUs`: the terminal state belongs to the old
  session, not to the page; a new session reconnects on a flap as before.

Why not `closing`: it is read in 14 other places, and setting it would change all of them. Most importantly,
`disconnect()` returns early when `closing` is set (so the owner's "stop" after a 410 would no longer release the
microphone or the leg). It would also silence `renewLeg`, `onTransportEvent`, `onLinkImpaired` (twice),
`onOwnerSpeechStart`, `onLocalEvidence`, `onLocalSpeechEnd`, `onLocalFalseStart`, `onCalibration`, `onSideband`,
`runReattachLoop`'s retry and `onReportFailure`. `closing` means "we are ending it". `gone` means "the server
ended it". Only the network handlers need the second.

## Evidence
Tests are in `apps/web/tests/voice/gone-is-terminal.test.ts` (4 cases). They fail against the unchanged
controller with the probe's counts (4, then 5). `session-storm.test.ts` gains one assertion: attaches after the
410 = 0 (it was 4). There are three mutation REDs (onNetworkLost guard, attach-path markGone, `gone` reset in
connect).

## Not touched
`localMode.ts`, the server, any other test.
