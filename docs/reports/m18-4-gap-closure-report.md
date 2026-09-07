# M18.4 final gap closure — milestone report (2026-09-07, late night)

Owner directive: "M18.4 FINAL GAP CLOSURE — BEFORE M19", then the master directive "CLOSE M18.4 AND COMPLETE M19 -> M28" (Phase 0). Decision record: ADR-0081 addendum 3. Evidence: `docs/evidence/m18-4-qualification-2026-09-07-{163343,170850,172512,173803,175914,182258}.json` (runs 4–9) with their probe logs.

**IMPLEMENTED**
- Gap 1, device presence during the drain: two edge upstreams (`pagentos_api`, `pagentos_devices` for `/v1/devices/connect`); the release moves device authority first (device upstream → idle colour; `POST /v1/devices/drain` on the active colour, loopback-only: sockets closed 1012, presence dropped, new connects refused; the script waits for `checks.broker.active_sessions` on the idle colour, exit 79 → rollback), then HTTP; rollback and `--rollback` reverse it (undrain first); a live colour without the drain route falls back to the legacy switch out loud. The edge runs its `nginx.conf` from the persistent edge dir (`-c`) because a bind-mounted tree file kept the old inode across the tree swap; `nginx -s reload` carries `-c`; the post-switch probe is a bounded settle wait.
- Gap 2, interrupted promotion recovery: `release-cloud-core-bluegreen.sh --reconcile` (the last COMPLETED promotion is canonical even when the edge names the candidate; the half-promoted candidate drained, stopped, its tree kept as `app.interrupted`; loud fallback exit 81); `PAGENTOS_INTERRUPT_AT` (SIGKILL) for the proof; `infra/systemd/pagentos-bluegreen-reconcile.service` at boot.
- Gap 3, Windows agent (DeviceService + SessionCompanion) staged update: `scripts/lib/AgentUpdate.ps1` — the candidate manifest (the staged service's `capabilities` verb now names `software_version`, agent 0.2.0; every staged file hashed; the browser worker's release identity) verified file by file before the swap; `Test-AgentHeartbeatOnCore` inside the journaled engine's health handler (Cloud Core must see the device online as the candidate with its capabilities, else rollback to the previous trees); the owner session from the DPAPI credential, skipped-and-said when absent.
- Gap 4, browser worker staged update: `BrowserWorkerHost.SwapWorkerAsync` (candidate beside the current worker; verified hello; drain of in-flight requests; busy on an open session; graceful retire; routing) and `BrowserCandidateWatcher` (request file under the companion data dir; candidate only under the admin-only install root).
- Gap 5, one real lifecycle: `scripts/core/evolution-advance.ps1` (list / show / advance / approve / footprint / authorize); `POST /v1/evolution/opportunities/{id}/footprint` and `.../authorize` (found missing by the lifecycle); `app/evolution/release_evidence.py` (the deployment ledger as the release-evidence provider, found missing by the lifecycle).

**TESTED** — `test_broker_drain.py` (7), `cloud-release-bluegreen.tests.ps1` 45/45 (fake docker: handoff order, stuck handoff 79, rollback devices-first, legacy fallback, three interruption points + reconcile, emergency 81, idempotent reconcile, edge recreation), `cloud-release.tests.ps1` 32/32, `agent-update.tests.ps1` 16/16, `BrowserWorkerSwapTests` 12/12 (agent suite 397/397; `BenchAndOptionsTests` 5/5 after the bench fix), `test_evolution_routes.py` (footprint validation, authorize chain to LIVE over REST), `test_release_evidence.py` (3), `test_identity_enforcement.py` (the two loopback routes allow-listed), API unit suite 1844+ green, ruff clean.

**SYNTHETIC OWNER COMMANDS TESTED / VOICE ROUTES PASSED** — the Owner Utterance Corpus 413/413 (category `evolution` 68 cases: pause/resume/cancel/hold, the four questions, refusals of rollback/promotion by voice) plus the regression and evolution-tool suites: 478 passed, 0 wrong routes. **FORBIDDEN ROUTES / SIDE EFFECTS**: 0. **TTS OUTPUT TEST**: the corpus harness's structural speech + audible-turn assertions per case (PROVEN_AUTOMATED); **LOOPBACK/STT**: not run in this closure (unchanged from ADR-0080: PROVEN_PROXY for the audio pipeline).

**BUGS FOUND / FIXED (all with regression tests)**
1. The edge's `nginx.conf` bind mount kept the old inode across the tree swap → the config now lives on the persistent edge dir (`-c`). (run 4)
2. `nginx -s reload` without `-c` looked for `/run/nginx.pid` on the recreated edge → `-c` on the signal. (run 4)
3. The old tree's script ran for later phases after a failed release (no `--reconcile`) → the harness runs HEAD's script from a stable copy. (runs 4–6)
4. The live colour predates the drain route (405) → the drain's HTTP status is read; 404/405 → the legacy switch, said out loud. (run 5)
5. `nginx -s reload` only sends the signal; the probe fired before the workers switched → a bounded settle wait. (run 6)
6. The audio bench's wall-clock tool-silence guard tripped on a loaded runner → a bench option (production keeps 4000 ms). (CI)
7. Two swap tests assumed runner timings → rewritten on the property; a candidate's exit was counted as the current worker's failure and reaped Chrome by profile → `WorkerProcess.IsCandidate`. (CI)
8. No REST route for `authorize` / `record_release_footprint` → both routes added. (the lifecycle)
9. The null release-evidence provider on production → the ledger provider. (the lifecycle)
10. A comma-joined footprint derived tier 2 for a tier-3 change → the route refuses malformed paths; the CLI splits. (the lifecycle)
11. The prober called a 3.3 s answer during the image build a drop → 10 s timeout, latency reported. (runs 2, 8)
12. The worktree merge ran in the worktree, not main → process rule (`git -C`). (engineering)

**SECURITY REVIEW** — the drain/undrain routes are loopback-only (403 for a tailnet peer, proven), not owner-gated by design and allow-listed by the enforcement test; the reconcile never makes a half-promoted candidate live silently (exit 81 is loud); the agent candidate manifest refuses a changed file; the browser candidate must live under the admin-only install root; secrets are never printed (DPAPI credential, token only in memory); no authority boundary changed.

**CI** — green on 338ffec (run 34150861282), all seven jobs. **DEPLOYMENT STATE** — Cloud Core 338ffec on `api-green` behind the edge (contract action 12 / realtime 2 / ui_state 3 / ambient 1); `RELEASE` = `LAST_KNOWN_GOOD` = 338ffec on the host; the reconcile unit enabled; Windows agent 0.1.0 on MAIL (0.2.0 waits on the owner's elevated update, items 26/27); web marker unknown to the server. **RUNTIME VERIFICATION** — run 9, 50/50 (above); opportunity `m18-4:device-presence-during-drain:v2` LIVE on production with `release_ref = ledger_event:c5baf315…`.

**PROOF CLASS**
| Item | Class |
|---|---|
| Device presence during drain | PROVEN_REAL (runs 8–9: handoff before HTTP on every switch, presence gaps 1.1–2.3 s) |
| Interrupted promotion recovery | PROVEN_REAL (runs 5–9, both points, 0 dropped) |
| Reboot recovery (the unit at boot) | PROVEN_PROXY (installed, enabled, run once; no autonomous reboot) |
| Windows agent staged update (service + companion) | PROVEN_PROXY (16 tests; the real run is the owner's elevated update) |
| Browser worker staged update | PROVEN_PROXY (12 tests over the real fake worker) |
| Real lifecycle rows on production | PROVEN_REAL (idea → live with commit / CI / evidence refs) |
| Autonomous candidate / fix / regression / shadow / canary | PROVEN_PROXY (engineer-authored patch; no coding backend for Cloud Core code) |
| Voice routing of the evolution commands | PROVEN_AUTOMATED (corpus 413/413) |

**REMAINING MACHINE-UNVERIFIABLE ITEMS** — the owner's elevated agent update (UAC; items 26/27); a real production reboot (owner-only); physical audibility (unchanged).

**M18.4 READY TO CLOSE / M19 DIGITAL OPERATOR MAY BEGIN** — and, under the master directive, M19 has begun (ADR-0082, `docs/M19_DIGITAL_OPERATOR_SPEC.md`).
