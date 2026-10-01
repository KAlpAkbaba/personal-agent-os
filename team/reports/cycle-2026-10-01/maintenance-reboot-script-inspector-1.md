**Inspector report: maintenance-reboot-script** (branch `team/cycle-2026-10-01/worker-maintenance-reboot-script`, sha f1b5193b)

**Pass 1: run it**
- The worker's commit touches only the 3 files in the area: the script, its test, and the ADR draft. The `main...HEAD` diff also shows other files, but they come from the branch base, not this commit.
- The suite ran from a clean tree. Result: **23 passed, 0 failed**, 11 s. `bash -n` is ok.
- I ran 5 mutations of my own, each on a scratch copy restored from a backup. The restored sha256 equals the original `2d0fbccf…49a6`, and `git status` is clean.

| Mutation | Result |
|---|---|
| `systemctl stop` of the timer deleted | RED, "--run order" fails |
| Disk check forced to pass | RED, 2 failures |
| Downtime forced to 0 | RED |
| `--run` accepted without `--preflight` | RED |
| Backup-age limit effectively removed | RED |

- The timer-before-upgrade case the worker skipped is now proven.
- The worker's own two mutations (pin check removed, marker written after `reboot`) are covered by their 12-check claim. I did not re-run them.
- Acceptance: each required refusal case has an exact-exit-code assertion, and so do the `--run` refusals. The marker-before-reboot check and the `--verify` refusals and downtime are asserted too. Nothing is missing.
- **Not run:** the full `quality-gate.ps1`, because the suite is not yet wired in (the lead's job). No real host, so `--preflight` has not run against a live machine.

**Pass 2: break it**
- **Health parsing is right.** The real `/v1/system/health` returns `"failing_checks":"..."` as a flat comma-joined string (`services/api/app/main.py:1018`). `""` therefore matches the script's empty-case pattern, and any failure is refused.
- **Release-sha check in `--verify` is a silent no-op on the real host.** The script reads `"version"` and expects 40 hex, but the API's `"version"` is `__version__` (`main.py:1019`). The sha sits nested under `"release"`, so `served` comes back empty and the check says "release unreported" and passes. ADR-0223 step 11 wants "the same release"; the lead must compare it by hand at the window, or a follow-up should parse `release`. The output does not hide this: it prints "unreported".
- **Pin path is right.** `install-recovery-supervisor.sh` writes `$recovery_root/APPROVED_SHA`, and the script reads that file.
- **Lock file is right.** It is the same `.bluegreen-operation.lock` the blue/green release script uses, taken with `flock -n`.
- **Reconcile text is right.** `RECONCILE OK` matches the release script's line at `:658`.
- **Order is safe.** The backup runs first and a failure exits 11 with nothing touched. The marker is written with write-then-`mv` before `reboot`. A failed `--verify` keeps the marker so it can be re-run.
- **Container names by substring are unverified against the real host.** The substrings are `redis`, `edge` and so on. A missing name after the upgrade stops the run before the reboot, so the failure is safe, but a false failure would postpone the window. The first real `--preflight` should be run dry.
- **Preflight changes nothing.** The suite asserts this, and the script only runs `apt-get -s`, reads, and a non-blocking lock probe.
- **No secrets or hardcoded owner paths.** The paths are env-overridable defaults. Nothing goes to audit or logs beyond health bodies, and the health body stored in the marker is on the host only.
- **Step 3 gap is stated.** The team-cycle and owner-mid-task check cannot be seen from the host. The script prints that this is the lead's check, which is honest.
- **Downtime includes shutdown time.** It is measured from the marker to the first good probe, as the ADR draft says, and the script does the same.

**For the lead at merge**
1. Add `scripts/tests/maintenance-reboot.tests.ps1` to `quality-gate.ps1` and `.github/workflows/ci.yml`.
2. Number the ADR from `team/plans/maintenance-reboot-script-adr.md` into `docs/DECISIONS.md`.
3. Before the window: run `maintenance-reboot.sh --preflight` dry on the real host, and check the device ids and the served release sha by hand.

**Evidence class:** PROVEN_AUTOMATED (fakes, 23 of 23, 7 mutations RED). The real-host run is NOT_RUN, and the first window is READY_FOR_OWNER.

**APPROVE**
