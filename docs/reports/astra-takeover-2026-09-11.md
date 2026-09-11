# ASTRA takeover verification — 2026-09-11

Status: `TAKEOVER_VERIFIED`, with two P0 reliability candidates in implementation.

## Current source

- Local `main`: `699c1659c8edede22d6e9d035b00327efc9fc7ec`, clean except owner/local untracked `.codex/` and `AGENTS.md`.
- `origin/main`: `47279670a67895ab9f8b0a6f1bd85164fc51251c`; local main is 12 commits ahead.
- The latest GitHub CI and nightly voice qualification are green on `4727967`, not on the current local/production commit.
- Seven pre-existing worktrees were preserved. Three branches contain unmerged commits: `claude/gifted-banach-779aa9`, `claude/hungry-wu-063eec`, and `claude/magical-borg-13226b`. Four worktrees contain uncommitted agent-memory, creative voice, or native voice changes. Nothing was deleted or overwritten.
- Long-running Claude processes from 2026-09-10 and current Codex processes exist. Process age alone is not treated as authority to terminate them.

## Current production

- Live host: `pagentos-core`, reached through the existing strict-known-host Tailscale SSH path.
- Active colour: blue.
- Cloud Core release and application marker: `699c1659c8edede22d6e9d035b00327efc9fc7ec` (matches local HEAD).
- Last-known-good marker: `667b9fb70b7d4bf30f832706e2fd269c33df4eab`.
- Database migration: `0039_owner_media_playbacks`.
- PostgreSQL, Redis, MinIO, Temporal, API blue and edge are running and healthy; Redis answers `PONG`.
- `pagentos-bluegreen-reconcile.service` is installed, but it runs only at boot/manual start. No continuous recovery timer and no backup timer are installed.

## Current Windows runtime

- `PagentOSDeviceAgent`: running as `LocalSystem`, automatic start, PID observed during takeover.
- `PagentOS Session Companion`: running in the owner session from the installed tree.
- Installed DeviceService and SessionCompanion product provenance: `0.6.0+72843b21a1c3c6e11897daaba4c89e76fb45b53f`.
- Installed browser worker: `0.5.0`, process running from `C:\Program Files\PagentOS\agent\browser`.
- The device's own capability command reports manifest `5cc3d9fbd9f7` and 85 distinct capabilities, including `project.*`, `app.*`, UI Automation, browser, document and native-app prerequisites.

## M28 status

The handoff report and `state/BUILD_STATE.json` are stale on the most important Windows fact. Stage 28 and `docs/evidence/item28-unlocked-20260909-190753.json` prove the real application path: build and independent PE inspection, launch, UI Automation interaction, close, relaunch, persistence read-back, application-log read-back and clean close. Row 26.15 is `PROVEN_REAL` with 48 checks, zero failed and zero blocked.

M28 is still not fully closed because row 26.16 is real missing product work: production supplies no `native_runner`, so Cloud Core cannot trigger the device's already-present native build path. Row 26.17 remains `PROVIDER_UNAVAILABLE` for real narration audio. Android remains `WAITING_OWNER_JDK`; iOS remains `NOT_BUILT` on this hardware.

## Report claims confirmed

- No governed production backup implementation or measured restore drill exists.
- Continuous post-release recovery was not deployed.
- Self-evolution governance is substantially present, but advancement remains owner-request driven and candidate isolation is not real git isolation.
- The coding backend claim was partly stale: a real Anthropic-backed candidate writer now exists, but its scope and reviewer remain narrow.
- Production native build triggering is absent despite the device half existing.
- Push delivery could permanently lose a readiness notification when a provider returned a failure receipt instead of raising.

## Report claims now stale

- M28's real Windows launch and persistence proof is complete.
- Windows runtime is no longer at 29 capabilities; it advertises 85.
- Cloud Core is already on the current local HEAD, rather than the older release described by the handoff.
- The model-backed engineering seam is no longer wholly deterministic, although it is not yet a general engineering engine.

## New defects found

1. `BUILD_STATE` and the M28 report still state that item 28 / launch is pending, contradicting durable qualification evidence.
2. GitHub CI is green only for `origin/main`; current production is 12 unpushed commits ahead and therefore lacks CI evidence for its exact commit.
3. Production contains one `RUNNING` and one `CREATED` task dating from 2026-09-09; both are stale and need workflow-specific reconciliation. There are eight READY tasks and none is currently unannounced.
4. `ArtifactReadyAnnouncer` treated any non-throwing provider result as delivery, while `MobileService` represents transient provider failures as `NotificationResult(delivered=0, failed>0)`.

## Critical P0

1. Backups: absent. PostgreSQL, MinIO/artifacts, identity/config and release metadata have no scheduled encrypted off-host backup or measured restore.
2. Recovery: the qualified reconcile action exists but is not continuously scheduled, so a release that degrades after initially becoming healthy can remain degraded.
3. Source/CI provenance: production is on an unpushed commit and cannot be called CI-qualified at its exact SHA.

## Active work and next gates

- Candidate branch/worktree: `codex/astra-p0-reliability` / `E:\AI\PersonalAgentOS_astra_p0_reliability`.
- Push delivery regression fix: implemented locally; targeted tests pass.
- Continuous recovery timer and transactional installer: implemented locally. Independent review initially rejected a racy userspace lock, mutable root-executed app-tree code, an HTTP-200/degraded health false positive and unsafe reinstall failure. Follow-up review found that Compose/nginx inputs were still mutable, edge health accepted `degraded`, and an installer upgrade could overlap a running reconcile. The candidate now uses kernel `flock`; pins and verifies the reconcile script, Compose definition and nginx policy outside release trees; requires exact `status=ok` plus release SHA internally and through the edge; waits for an active reconcile before replacing files; and restores/restarts the previous monitor on failed upgrade. Regression tests cover each finding.
- Deployment is not yet performed. Recovery-root changes are owner-gated by the repository's risk policy and require review plus full qualification before the final concrete deployment approval.
- Backup implementation and a real restore drill remain the next P0 work item. Off-host storage credentials/configuration are a genuine owner boundary only when the tested implementation is ready.

## Ready for M29?

No. M28 production triggering, backups/restore, continuous recovery deployment, exact-SHA CI, and the stale production task rows must be resolved or explicitly quarantined first.
