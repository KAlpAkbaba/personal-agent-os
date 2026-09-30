# pilot-01 — the lead's split of the three approved proposals (2026-09-30)

The owner approved all three ("Üçünü de onaylıyorum, CPX32'de ölçerek başla"). TEAM_PROTOCOL
section 4: one branch, one area, at most 25 files and one ADR per task; two tasks in work never
share an area. This cycle runs the two tasks that need no infrastructure and no library, plus
the owner's own Onay Merkezi task; the rest are written here and enter the queue when their
turn comes.

| # | Task | Proposal | Area | Cycle |
|---|---|---|---|---|
| 1 | `narrative-collector` — collector, rule narrator, auditor | 2 | `services/api/app/narrative` | **pilot-01** |
| 2 | `execution-target-rule` — the pure rule table, fallback chain, events | 1 (PR 1) | `services/api/app/execution` | **pilot-01** |
| 3 | `onay-merkezi` — the owner's gates in the Cockpit (API + shell first) | bootstrap prompt | `services/api/app/team`, `apps/web/src/app/core/approvals` | **pilot-01** (after 1 and 2) |
| 4 | `cloud-browser-worker` — the worker container on the Cloud Core registered as `device_kind=cloud`, alias `bulut`; `mem_limit` 2 GB; measured with `docker stats` on CPX32 | 1 (PR 2) | `services/browser` (cloud entry), `infra/docker`, `app/devices` (device_kind) | pilot-02, integrator first (gVisor/Chromium in Docker) |
| 5 | `execution-target-wiring` — the rule table used by research, browser tasks and schedules; the ledger vocabulary; `no_capable_device` becomes a fallback event | 1 (PR 1b) | `app/research`, `app/routines`, `app/webtask/service.py` | pilot-02, after 2 and 4 |
| 6 | `compute-run-sandbox` — network-less Docker (`--network none --cap-drop ALL --read-only`, CPU/memory/time caps); gVisor second | 1 (PR 3) | `services/api/app/compute`, `infra/docker/sandbox` | pilot-03, integrator first |
| 7 | `narrative-voice` — the model narrator behind the Protocol, the intent ("bu hafta ne oldu"), the explain query type, TTS through the narration path | 2 (step 2) | `app/voice/intents`, `app/explain` | pilot-02, after 1 |
| 8 | `task-loop-in-the-cloud` — PR-C's planners and the six binding risks, proven on the cloud worker for T1/T2/T4 | 3 | `app/webtask` | pilot-03, after 4; **needs the owner's answer on "no unattended task" for a cloud job** |

Capacity: the owner said CPX32, measured. Task 4 measures; CPX42 (+€34/month) is asked
separately only if the measurement says so.

Held back on purpose: nothing in this cycle touches production, a device, the owner's Chrome
or a schedule.
