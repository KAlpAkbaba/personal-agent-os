# CLAUDE.md — Personal Agent OS Engineering Contract

This repository contains a single-owner personal Agent OS. Treat the files in this repository as the authoritative product specification.

## Required reading order

Before architecture changes, read `PROJECT_CONSTITUTION.md`, then the relevant document under `docs/`.

## Product invariants

- One human owner. No SaaS tenant model.
- Turkish (`tr-TR`) first-class, especially voice and narration.
- Voice-first, but web/desktop/mobile remain fully usable.
- Hybrid architecture: cloud brain + device-local execution.
- The owner should not become the software's operator/maintainer.
- Routine bug fixing, regression testing, release, rollback and feature extension should become autonomous.
- Task completion, artifact persistence and presentation are separate concepts.
- A completed task must not automatically force a long result onto the owner. Default: notify briefly and wait.
- Security testing is allowed only for assets recorded as owner/enrolled/authorized. Within authorized scope, avoid repetitive permission questions.
- Third-party services must be behind provider interfaces.
- Never commit secrets.

## Engineering operating mode

The binding form of this section is `docs/DEVELOPMENT_POLICY.md` (permanent, owner
directive 2026-09-07): every request is a tracked work item run through UNDERSTAND →
DESIGN → IMPLEMENT → TEST → REVIEW → FIX → RETEST → INTEGRATE → QUALIFY → DEPLOY if
authorised → VERIFY RUNTIME → CLOSE; bugs found on the way are part of the same task;
every real bug gets a regression test; integration through the real application object
is part of done; owner-only steps are marked `READY_FOR_OWNER` and never block the rest;
every work item ends with the completion report the policy names.

1. Understand the current milestone and acceptance criteria.
2. Decompose work into testable tasks.
3. Delegate specialist work to `.claude/agents/` proactively.
4. Prefer isolated worktrees for substantial parallel implementation.
5. Add/adjust tests.
6. Implement.
7. Run deterministic quality gates.
8. Run an independent review.
9. Record decisions and state.
10. Release only when gates pass.

## Asking the owner

Do **not** ask the owner routine implementation questions.

Choose autonomously when the decision is reversible and consistent with the constitution. Record it in `docs/DECISIONS.md`.

Ask only when one of these is true:

- human login/MFA/OAuth is required;
- UAC/reboot/physical action is required;
- a paid account or API credential must be created;
- data loss or an irreversible external action cannot be made safe automatically;
- two interpretations conflict with a fundamental product requirement.

Batch owner requests.

## Platform defaults

- Native Windows is the primary development shell because of the Windows Agent.
- Linux services run in Docker/WSL2 locally and Docker on the cloud VM.
- Cloud runtime target for initial production is Hetzner NBG1 + Tailscale + S3-compatible object storage.
- Use PostgreSQL + pgvector as canonical persistent state.
- Use Temporal for long-running/durable workflows.
- Use Redis only for ephemeral/cache concerns; do not make it a sole source of truth.
- Use S3-compatible object storage for artifacts/backups.
- Use GitHub private repo + Actions + GHCR for CI/release unless the owner later changes this.

## Windows Agent rule

A Windows Service runs in Session 0 and cannot be treated as the interactive desktop controller. Architect Windows execution as:

- privileged/background service for machine-level concerns;
- owner-session companion process for UI, audio and interactive desktop;
- authenticated local IPC between them;
- outbound-only connection to the cloud/device broker.

## Browser rule

Use the highest semantic control surface available:

1. API/integration
2. DOM/Playwright
3. accessibility tree
4. Windows UI Automation
5. vision
6. raw coordinates only as last resort

Existing logged-in Chrome/Edge sessions should be supportable through a Playwright/browser-extension path.

## Voice rule

Do not conflate:

- STT/transcription,
- speaker verification,
- realtime assistant voice,
- long-form narration TTS.

They are separate subsystems and may use different models/providers.

## Self-development rule

Never implement self-improvement as "model edits production source and restarts".

Required path:

`Gap/incident -> issue/spec -> isolated branch/worktree -> code -> tests -> review -> build -> sandbox -> canary/shadow -> metrics -> promote or rollback`

The Recovery Supervisor and its last-known-good metadata must survive a broken main application release.

## Definition of done

A task is not done until its acceptance criteria pass. A milestone is not done until `docs/ACCEPTANCE_TESTS.md` gates for that milestone pass and `state/BUILD_STATE.json` is updated.
