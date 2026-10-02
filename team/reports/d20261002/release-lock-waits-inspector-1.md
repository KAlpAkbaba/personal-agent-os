## Inspector report — `release-lock-waits` @ `2cceeb84` (worker round 1)

**Pass 1 — run it (clean worktree, 3 files in the commit, all inside the area)**
- `cloud-release-bluegreen.tests.ps1`, full suite: **112 passed, 0 failed**, exit 0 — matches the worker's number.
- Neighbouring suites: `cloud-release` 38/0, `maintenance-reboot` 35/0, `host-snapshot` 95/0.
- Python: `test_release_exit_codes` + `test_systemd_onfailure_units` 20 passed; recovery-supervisor `test_systemd_install` 33 passed, 4 skipped on Windows; `bash -n` clean.
- **The 4 skipped tests, run by me on Linux** (`python:3.12-slim`, util-linux flock 2.41, non-root, tree from `git archive HEAD`): **37 passed, 0 skipped**. The reconcile's kernel-lock half holds with the new script.
- **The worker's NOT_RUN (real `flock`, `-n` then `-w` on one fd), run by me**: the real script against util-linux 2.38.1 in a throwaway container, with a real holder.
  - Lock held 3 s: preflight, release and rollback each waited 2.7 s, said the wait line once, then went past the lock (exit 66, no `.env` in the scratch base).
  - Lock held 6 s with wait 2: exit 82 after 2.0 s, message names "2 s".
  - Wait 0: exit 82 in 0.0 s with yesterday's words.
  - `--reconcile` (timer form and `SHA --reconcile`, wait 45 / unset / `abc`): exit 82 in 0.0 s, no wait line.
  - `abc`, `-1`, `601`, `4.5`, `045`, ` 45`, `+5`, `5;id`: exit 64, lock file not created.
  - A lock obtained through `-w` stays held for the shell's lifetime.
- **My mutations** (five, different from the worker's; scratch copies with a shortened suite, baseline 41/0):

| Mutation | Result |
|---|---|
| `flock -w` replaced by `-n` | RED, 0 pass / 6 fail, suite collapses at the first preflight |
| reconcile recognised by `$first_arg` only | RED, 4 fail, exactly the `SHA --reconcile` cases |
| upper bound `-gt 999` | RED, 7 fail |
| wait line printed twice | RED, 8 fail |
| seconds dropped from the timeout message | RED, 2 fail |

- Worktree untouched: sha256 before = after (`e727f29b…` script, `ae3d8e61…` test), `git status` empty, scratch removed, nothing left running.

**Host-snapshot rule**
- The fixture is **stale**: `collected_at` 2026-10-01T19:18:51Z is before the last release (23:50 UTC), and its `markers.release` is `858c3e0b`, not `5f250e5b`. The lead collects a new one (`collect-host-snapshot.ps1`) before the merge. The lock shape (2 of 60 samples) is unlikely to have moved, but that is unmeasured.
- The lock in the fake comes from the fixture (`longest_run * interval_s`), not a constant.
- `Reset-Host` still defaults to a constant `blue`. It predates this diff, and the lock code runs before any colour is read, so it cannot mask a defect here. I am not returning for it; it is debt against ADR-0235 and deserves its own card.

**Pass 2 — break it**
- The card's "stage-only waits": confirmed there is no path. `release-cloud-core.ps1 -StageOnly` never calls the script (lines 111–113).
- Reconcile identity is the mode alone; restore and the operator's `--reconcile` (no bundle) also step aside. The unit has `SuccessExitStatus=82`. Correct.
- `PAGENTOS_LOCK_WAIT_S=""` (set but empty) is taken as 45, not refused (`:-`). Same as the maintenance script; untested and not in the ADR. Minor.
- The driver's `$exitMeanings` has no text for 64 (nor 82): the owner sees "the host reported exit 64" plus the host's own line. Out of the area; follow-up.
- The wait line is new stderr on a **success** path. The driver does not redirect stderr, so it is safe today; the standing rule (never `2>&1` the driver) now also covers this line.
- No test in the repo holds the wait half against a real kernel lock. Mine was by hand; a Linux-only test beside `test_periodic_recovery_refuses_to_overlap_a_live_release` would keep it.
- Cosmetic: the diff turned `function Get-Upstream {` into `function Get-Upstream{`.
- The pinned `/opt/pagentos-recovery/reconcile.sh` sha256 changes; the pin renewal at this release is the owner's step, as the worker says.
- No secrets, no paths, nothing under `infra/`, no compose or env change. Rollback is reverting one commit.

**Evidence classes**
- Wait / 82 / 64 / reconcile-never-waits on the fake host: **PROVEN_AUTOMATED**.
- Same behaviour with real util-linux `flock` and the real script in a container: **PROVEN_PROXY**.
- A real release whose preflight meets the reconcile and passes: **NOT_RUN** (next release).
- Fresh snapshot: **NOT_RUN** on this machine; `collect-host-snapshot.ps1` is the lead's to run before the merge.

APPROVE
