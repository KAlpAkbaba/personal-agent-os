---
name: project-pagentos-m8
description: M8 Authorized Security milestone - initial PARTIAL verdict (unwired registry provider), then re-verified PASS after fix commit 8b7812a closed the wiring gap
metadata:
  type: project
---

M8 (Authorized Asset Registry + defensive security agent) for
PersonalAgentOS_Claude_Autonomous_Build_Package_v1. Two-pass history:

**Pass 1 (verdict: PARTIAL, not closeable)** found `RegistryAuthorizationProvider`
built and unit-tested in isolation but never wired into `EvolutionRuntime` - the
live grant path still resolved to the deny-everything default, so ADR-0026's
"closes M7's High finding" claim was false of the running system. Also found a
concurrent `security-reviewer` session live-patching the same source files
uncommitted mid-verification (process deviation - see the git-status-drift
lesson below, still valid going forward).

**Pass 2 (2026-09-01, verdict: PASS, M8 CLOSED)** re-verified on the committed
tree at `8b7812a` (fix) + `1288917` (closure commit). Tree was clean at start
and end (`git status --porcelain` empty both times). Confirmed at THREE levels,
not just source reading:
- **Source**: `EvolutionRuntime.authorization` (services/api/app/evolution/runtime.py:161)
  now builds `RegistryAuthorizationProvider(self.session)` by default (env var
  override still works for dev, Null when neither exists); `routes.py:193`
  passes `reviewer=runtime.reviewer` into `EvolutionPipeline` - the only live
  HTTP grant path. NOTE: `EvolutionRuntime.improver` (used by `SkillImprover`,
  self-improvement path) still does NOT pass `reviewer=self.reviewer`, so if it
  is ever wired to a route it would default to `NullAuthorizationProvider`
  (fail-safe, but inconsistent with the "registry by default" framing). Not a
  live bug today - `runtime.improver`/`runtime.improvement_detector` are
  constructed but grep confirms **no route calls them** - flag this if M9+ ever
  wires up self-improvement.
- **Runtime**: started uvicorn myself against the real dev Postgres
  (`services/api`, `uv run uvicorn app.main:app`), hit `/v1/system/health` ->
  `security.authorization_provider: "registry"` with NO env var set.
- **End-to-end HTTP**: drove `/v1/security/assets` (enroll/suspend/revoke) +
  `/v1/evolution/gaps` + `/gaps/{id}/resolve` myself with fresh unique assets
  per case. Full 5-case matrix confirmed: enrolled+active+correct-permission ->
  `registered` in production; permission not recorded -> `rejected` (review:
  `permission_grants_reviewed`, `reviewer_approved:false`); suspended -> same
  refusal; revoked -> same refusal; unknown/never-enrolled ref -> same refusal.
  **Gotcha discovered while building this test**: the gap composer matches
  purely on declared input/output NAMES across ALL production capabilities
  system-wide (not by capability_id), and the shared dev Postgres persists
  state across test runs/processes - a leftover `text->slug`-shaped capability
  from an earlier (uncleaned) run of this same test made LATER scenarios
  resolve via `composition` instead of `generation`, silently skipping the
  permission-grant check entirely and producing a false FAIL. Fix: either give
  every scenario a distinct operation/output name (5 fixed ops exist:
  slugify/slugify_tr->slug, word_count->count, reverse_text->reversed_text,
  char_checksum->checksum) or clean up (`DELETE FROM capabilities WHERE
  capability_id LIKE ...`) between runs. Always clean up self-created
  capabilities/gaps/skill_versions AND revoke self-enrolled security assets
  after an E2E drive against the shared dev DB - there is no capability delete
  endpoint, only revoke for assets.
- **Junction fix**: independently planted a real `mklink /J` junction (via the
  PowerShell tool - `cmd.exe` invoked through the Bash/MSYS tool silently no-ops
  on mklink, shows only the cmd banner with exit 0 and no junction; must use
  the PowerShell tool or `dangerouslyDisableSandbox` doesn't fix it either,
  it's an MSYS quoting/interactivity issue not a permissions one) pointing
  outside an authorized root, called `collect_files` directly - junction target
  excluded, genuinely-inside files (including nested/deep) still collected.
  Also: `Path(r'/c/Users/...')` in a `uv run python -c` snippet resolves
  relative to the CURRENT DRIVE (silently becomes `E:\c\Users\...` and
  `.exists()` is False) - always convert to a real Windows path
  (`C:\Users\...`, via `cygpath -w` from bash) before handing paths to Python
  `pathlib` in this environment.
- **Breadth guard**: live `/v1/security/assets` enroll calls - `0.0.0.0/0`,
  `10.0.0.0/8`, `::/0`, `224.0.0.0/24` (multicast), `240.0.0.0/24` (reserved),
  `0.0.0.0/1` all refused with HTTP 422 `validation_error` (not silent
  narrowing); `10.20.30.0/24`, `172.16.0.0/16`, `2001:db8:abcd::/48` all
  enrolled cleanly (201).
- **Regression**: same 5 spoof targets from pass 1 (10.20.3.40,
  10.20.30.40.evil.com, 168430120, ::ffff:10.20.30.40, example.test.evil.com)
  still refused via live `/v1/security/scope/check`. Side-gotcha: re-enrolling
  the same locator repeatedly (even after revoke - `all_assets()` deliberately
  includes revoked/suspended rows so refusals can say why) makes a legitimate
  positive-control target come back `ambiguous_target` instead of
  `in_scope` - intentional fail-safe design (never guess which of several
  matching authorizations applies), not a regression; use a fresh
  never-before-used locator for a clean positive control.
- **Suites**: ruff clean; unit 1024 passed; integration 57 passed (`-m
  integration`); recovery-supervisor 25 passed; full
  `scripts/quality-gate.ps1 -E2E` all 12 steps PASS including M1 Notepad E2E
  with restart recovery.
- `state/BUILD_STATE.json`: `last_completed_milestone: "M8"`, quality_gate
  status PASS, matches actual committed/tested state this time (contrast with
  the M6/M7 pattern in [[project-pagentos-m6]] where this file lagged reality -
  this time it was accurate).
- `docs/reviews/M8_SECURITY_REVIEW.md` exists and its findings/dispositions
  table matches exactly what pass-1 found and what pass-2 independently
  reconfirmed fixed.

**Standing lesson reconfirmed**: a milestone that "closes" a prior milestone's
gap by adding a class satisfying an existing interface is only actually closed
once you grep for where the interface's *consumer* constructs the
implementation AND drive it end-to-end over the real transport (HTTP here) -
reading the wiring diff is necessary but not sufficient; the registry/reviewer
call chain across 3 files (`runtime.py` -> `routes.py` -> `pipeline.py`) had to
be traced together before the live drive could even be designed correctly, and
the live drive is what caught the composition-shortcut false-negative that
source reading alone would have missed.

See [[pagentos-project-state]] for overall milestone progress tracking.
