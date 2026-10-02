# ADR (no number yet): alan dışı geri verme - the rules (`scripts/lib/TeamArea.ps1`)

Status: accepted by the worker of `area-widen-rules`; the lead numbers it and moves it into
`docs/DECISIONS.md` at merge time. Implements rules (1) and (2) of
`team/proposals/2026-10-02-alan-disi-geri-verme.md` as functions; wires nothing.

## Context

A card whose fix is outside its file area cannot be finished by its worker: the inspector
returns it, the second return stops it, and it waits for the lead to open it by hand. In
d20261001 two cards (`narrative-failures-only-model`, `execution-call-site-research`) spent
25,86 USD that way and reached nothing; three cards of `cycle-2026-10-01` sat stopped for
seven and a half hours beside idle seats. The written rule in `lead.md` did not prevent it
twice, so the cycle needs a rule it can execute.

## Decision

`scripts/lib/TeamArea.ps1` (PS 5.1, dot-sources `TeamQueue.ps1`) holds the rules as pure
functions: no process, no file read, no store write. The cycle does not call them yet.

1. **The contract line** (the same text in card `area-widen-role-lines`). The request is ONE
   line of a role's report, alone on its line: the inspector writes
   `alan_disi: [path, path]` above its RETURN verdict, the worker writes
   `ALAN_ISTEGI: [path, path]`. The key is case-sensitive and each role's key is read only
   from that role's report. The list is bracketed, comma-separated, repository-relative,
   forward slashes. Backticks, asterisks and spaces around the line, the key or a path are
   ignored (quotes around a path too - models write JSON lists). The LAST key line wins; an
   empty list or a key line without brackets there is "no request", even after an earlier
   valid line (a role can withdraw). `Get-TeamAreaRequest` returns `Asked`, `Files`
   (normalised, each once, order kept) and `Bad` (absolute, drive letter, `..`, a wildcard,
   or `.`: never in `Files`). A request of bad entries only is still `Asked`, and is refused.

2. **The order of judgement** (`Resolve-TeamAreaRequest`), first hit decides:
   1. a path that is not inside the repository -> `refuse`;
   2. nothing asked that is outside the area -> `refuse` (nothing to widen);
   3. ANY asked path protected -> `refuse`;
   4. `area_widenings >= 2` (`$script:TeamAreaMaxWidenings`) -> `refuse`, to the lead;
   5. the area would exceed `$script:TeamMaxAreaEntries` (25, TeamQueue's own) -> `refuse`;
   6. ANY path to add overlaps (`Test-TeamAreasOverlap`: same file, file inside directory,
      directory holding file) the area of ANOTHER task that is `approved`, `assigned`,
      `in_progress`, `inspecting` or `returned` (`$script:TeamStatesInWork`) -> `wait`,
      `DependsOn` = those ids, the area unchanged;
   7. otherwise `widen`, `Add` = the asked files not already inside the area.

   Step 1 and one branch of step 6 are additions to the card's order, both towards refusal:
   a holder that itself waits (directly or through others) for this task would make
   `depends_on` a loop that holds both for ever, so that request is `refuse`, to the lead.

3. **A refusal and a wait are of the whole request.** One protected path beside ten free
   ones widens nothing; one held file beside free ones widens nothing either. A partial
   widening would send the worker back for a round that cannot finish (the missing file is
   why the request was made) and would spend one of the two widenings on it.

4. **The cap is two widenings per task**, a named constant. A third request means the card
   was cut wrong, which is the lead's to fix, not the cycle's to keep patching; the record
   in `area_history` ({at, by, why, files[, waits_for]}) is what the lead reads.

5. **`Add-TeamAreaWidening` is idempotent**: it adds only what is still missing (files
   outside the area, ids not in `depends_on`, never the task's own id) and writes the count
   and the record only when something was added. `Test-TeamAreaReturnCounts` is `$false`
   for `widen` and `wait` (the card's fault, not one of the worker's two rights), `$true`
   for `refuse` and for anything that is not a resolution.

6. **Protected, never widened into** - ONE constant, `$script:TeamAreaProtected`; each entry
   is a path (itself, anything under it, any directory holding it) or a pattern, and names
   its source. The suite fails when an entry has no case.

   | Entry | Source |
   |---|---|
   | `docs/HANDOFF.md`, `docs/DECISIONS.md`, `state/BUILD_STATE.json`, `docs/THIRD_PARTY_COMPONENTS.md`, `team/queue.json`, `team/lock.json` | `TeamQueue.ps1` `$script:TeamSharedFiles`, taken at load (not copied); TEAM_PROTOCOL section 4 |
   | `docs/ROADMAP.md`, `docs/TEAM_PROTOCOL.md` | `.claude/agents/lead.md`: not edited without the owner |
   | `.claude/agents` | the card; TEAM_PROTOCOL section 2 (the role files) |
   | pattern `hand[-_]?gestures` | `.claude/agents/worker.md`; `Test-TeamSplit` refuses the same |
   | **Secrets**: patterns `.env` / `.env.*`; `*.key`, `*.pem`, `*.pfx`; `*.tfstate*`, `*.tfvars`, `*.tfplan`, `tfplan.binary`; paths `secrets`, `services/api/var` | `.gitignore` ("# Secrets", the OpenTofu block, the owner identity root) |
   | `scripts/lib/SecretStore.ps1`, `scripts/secret-store.ps1`, `scripts/cloud/install-env-secret.sh`, `scripts/cloud/set-cloud-secret.ps1` | PROJECT_CONSTITUTION section 6 "secret root" |
   | **Last-known-good**: pattern `last[-_]?known[-_]?good`; `scripts/cloud/release-cloud-core-bluegreen.sh` (writes `RELEASE` / `LAST_KNOWN_GOOD` on the host); `services/recovery-supervisor` (`workspace.py`: `last_known_good.txt`) | PROJECT_CONSTITUTION section 6; DEVELOPMENT_POLICY section 11 |
   | **Recovery roots**: `services/recovery-supervisor`; `services/api/app/identity/root.py`; `infra/docker/docker-compose.prod.yml`, `infra/docker/edge`, `scripts/cloud/release-cloud-core-bluegreen.sh` (the three files `install-recovery-supervisor.sh` copies into `/opt/pagentos-recovery`); `infra/systemd`; `scripts/cloud/install-recovery-supervisor.sh`, `uninstall-recovery-supervisor.sh`; `scripts/cloud/backup-cloud-core.sh`, `restore-cloud-core.sh` | PROJECT_CONSTITUTION section 6; `services/api/app/evolution/sandbox.py` `PROTECTED_TREES` and `risk.py` |

   The last-known-good metadata and the recovery root themselves live on the host
   (`/opt/pagentos/LAST_KNOWN_GOOD`, `/opt/pagentos-recovery`), not in the repository; what
   is protected here is the code that writes them. `.env.example` is refused with the other
   `.env.*` files: it is a lead's card, not a widening.

7. **The queue's schema is not changed here.** `area_widenings` and `area_history` exist only
   on the objects these functions return; they reach `team/queue.schema.json`, the Cloud
   Core's validation and the store with the wiring card, which also owns the TEAM_PROTOCOL
   text.

## Consequences

- The wiring card calls: `Get-TeamAreaRequest` on the report, `Resolve-TeamAreaRequest`
  when `Asked`, `Add-TeamAreaWidening`, and `Test-TeamAreaReturnCounts` before it counts a
  return. A `wait` keeps the request only in `area_history`: once the holder is on main the
  wiring must resolve those files again (the area was not changed).
- `depends_on` is met only by `awaiting_release` / `released` / `done`
  (`Get-TeamUnmetDependencies`). A waiting card whose holder is `stopped` waits until the
  lead acts; that is today's rule for every dependency and is not changed here.
- Not in the list: the update-signature verification of constitution section 6 - no single
  path in the tree could be named for it with confidence; the lead may add an entry (and
  its case) when it is named.

## Evidence

PROVEN_AUTOMATED: `scripts/tests/team-area.tests.ps1` under Windows PowerShell 5.1, its own
step in `scripts/quality-gate.ps1`; red before `TeamArea.ps1` existed; three mutations
(protected check, conflict check, cap) each red, restored by sha256 from a backup copy.
PROVEN_REAL belongs to the wiring card's first real cycle.
