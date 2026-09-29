# TEAM_PROTOCOL — how the Claude agent team builds PersonalAgentOS

Status: binding (owner decision 2026-09-29). Changing this file needs owner approval and a
lead commit, like the roadmap.

## 1. Why

One session cannot carry the project: context fills, work serialises, and nobody argues
back. The owner asked for a team: "birden fazla agent takımı çalışacak ve bu projeyi
geliştirecek" — with him touching only the final checks. This protocol makes that safe under
the rules the project already has (DEVELOPMENT_POLICY, evidence classes, ADRs, the recovery
and secret rules).

## 2. Roles (definitions in `.claude/agents/`)

| Role | File | Does | Never |
|---|---|---|---|
| Lead — Proje Hakimi | `lead.md` | Owns ROADMAP + definition of done; splits work into non-overlapping tasks; assigns; sends back incomplete/wrong work; owns the integration branch; merges; writes the cycle report to the owner | writes feature code itself; merges without inspector approval; releases without owner approval |
| Researcher | `researcher.md` | Knows the whole project (ROADMAP, DECISIONS, HANDOFF, QUALIFICATION); scans the web for models, libraries, methods; writes proposals with cost/risk/evidence to the owner | edits ROADMAP; writes code; assigns work |
| Integrator | `integrator.md` | For an assigned task, finds existing code/libraries (GitHub etc.), checks licence, security, device safety; writes the integration plan; registers in THIRD_PARTY_COMPONENTS | pulls a dependency into the tree without the licence/safety record; writes feature code |
| Worker (×2–3) | `worker.md` | Implements one task in its own worktree/branch under DEVELOPMENT_POLICY: red test first, code, mutation RED with sha256 restore, ADR when a decision is made, HANDOFF block | touches files outside its assigned area; touches main; runs releases; touches secrets, LKG, recovery roots, `feat/hand-gestures-stage1` |
| Inspector — Denetleyici | `inspector.md` | Runs the result (tests, gate, real run where possible), then tries to break it (adversarial pass: false claims, missing evidence, contract violations, security); reports raw numbers + evidence class | changes code; softens a finding; approves without running |

Model: lead and inspector on the strongest available model; workers and integrator may use a
cheaper model for mechanical tasks; the researcher uses web search.

## 3. The cycle

```
researcher scan → OWNER approves ideas → lead splits & assigns
  → integrator plan (if a task needs one) → workers (parallel, own worktrees)
  → inspector runs + breaks → lead sends back or merges to integration branch
  → gate once on the integration branch → lead merges to main
  → OWNER release approval → release (blue/green, pin, LKG) → OWNER real-world evidence
  → cycle report → next cycle
```

- One cycle = one run of `scripts/team/cycle.ps1`. It ends at the first owner gate it meets
  or when the queue is empty; it never waits for a human inside a run.
- The owner's three gates and only three: **idea approval**, **release approval**,
  **real-world evidence + final verdict**. Anything else that seems to need him is a protocol
  gap: the lead writes it down in the report instead of asking.

## 4. Work splitting (no conflicts by construction)

- A task names its **file area** (directories/globs). Two concurrent tasks never share an
  area. Shared files (BUILD_STATE.json, HANDOFF.md, DECISIONS.md, THIRD_PARTY_COMPONENTS.md)
  are written only by the lead at merge time from the workers' reports.
- Task size cap: one branch, one area, ≤ 25 files changed, ≤ 1 ADR. Bigger → the lead splits.
- Branch names: `team/<cycle-id>/<role>-<task-slug>`; integration branch
  `integrate/<cycle-id>`; worktrees under `.claude/worktrees/<branch>` (never inside the
  main checkout).

## 5. Evidence and gates

- Every worker report carries: 40-hex sha, tests added (red-first proof), mutation RED
  proof, evidence class per claim, open risks.
- The inspector's evidence classes: PROVEN_AUTOMATED (tests/gate), PROVEN_PROXY (fixture,
  dev stack, headless browser), READY_FOR_OWNER (needs a real device/voice/production).
  **PROVEN_REAL is written only after the owner's real-world test**, by the lead, quoting
  the owner's report.
- Full quality gate runs once per cycle, on the integration branch, on a machine that can
  run all of it (home PC). A machine that cannot (no Temporal, no dotnet test) runs the
  partial gate and says so.
- No merge to main without: inspector approval + green gate + lead review. No release
  without the owner's explicit sentence.

## 6. Roadmap and protocol changes

Researcher proposes → owner approves → lead writes and commits. Nobody else edits
`docs/ROADMAP.md` or this file.

## 7. Context and continuity

- State lives on disk: `team/queue.json` (tasks and their states), `team/reports/<cycle>.md`,
  HANDOFF.md ("Şu an üzerinde çalışılan"), BUILD_STATE.json.
- Every agent run is a fresh `claude -p` process with only its role file + its task; it
  returns a ≤ 40-line report. Raw logs go to files, never into another agent's context.
- Every step is idempotent: existing branch/worktree/file → skip; a killed run resumes from
  the queue on the next trigger.
- Budgets: per-run token/time caps in the role files; per-cycle cap in `cycle.ps1`. Over
  budget → stop, report, wait for the next trigger.

## 8. Machines

- The lead runs on one machine per cycle; worktrees live there. Two machines never write the
  same checkout: the office PC runs a cycle only when the home PC's queue shows no active run
  (lock file `team/lock.json` with machine name and timestamp, stale after 6 h).
- Secrets are per machine (DPAPI); never copied. The office PC never releases.
- Real-world evidence: home tasks on MAIL, office tasks on GMKADIRAKBABA; the report says
  which.

## 9. Scheduling

- Pilot cycles are started by the owner. After the pilot report, a Windows scheduled task on
  the home PC starts `cycle.ps1` nightly (02:00, Europe/Istanbul) with the budget caps.
- The morning report reaches the owner in the web shell (Bildirimler) and, when the voice
  path allows, as one spoken paragraph: what is ready, what needs his approval, what he must
  try on a real device.

## 10. Stop conditions

A cycle stops itself when: budget cap hit; inspector rejected the same task twice; gate red
twice on the same integration branch; lock held by the other machine; production health not
`ok` before a release step. Each stop is a line in the report with the reason.
