---
name: feedback-proven-real-discipline
description: Never mark a feature PROVEN_REAL in state/BUILD_STATE.json without an actual real-device/real-Chrome/real-OS run — tests against fakes are BUILT_AND_REVIEWED only.
metadata:
  type: feedback
---

`state/BUILD_STATE.json` and `docs/QUALIFICATION.md` use `PROVEN_REAL` to mean the real OS
mechanism/host/hardware/network was actually exercised, not simulated. A proxy result (unit
tests, integration tests against a `FakeDeviceCommandClient`, deterministic providers) is never
written up as `PROVEN_REAL` — record it as `BUILT_AND_REVIEWED` or `PROVEN_PROXY` instead, and
say explicitly what real-world leg is still missing.

**Why:** this rule is stated directly in `state/BUILD_STATE.json`'s `qualification.rule` field
and reinforced by the project's own history — several real-machine defects (see
`docs/QUALIFICATION.md`) were found only because a "proven" claim was checked against a real run
and failed. Overclaiming here misleads whoever reads BUILD_STATE.json next (including a future
instance of this agent) into skipping verification that still needs to happen.

**How to apply:** when a task's device/browser-worker half is being built in parallel (or not at
all) and you only have fakes/scripted doubles to test against, say so explicitly in
BUILD_STATE.json — e.g. "BUILT_AND_REVIEWED... against fakes/contract-shapes... NOT PROVEN_REAL
until it runs against a [worker implementing the contract / real device / etc.]". See the
`section_5a_owner_handoff` entry added under `m13_browser_research` in BUILD_STATE.json
(2026-09-03) for the exact phrasing pattern used.
