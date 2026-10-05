# ADR draft - a run's temp folder is emptied, not deleted; empty old ones are swept (ADR-0214 addendum)

Status: proposed (worker, cycle d20261005, task run-temp-keeps-git-bash-tmp)

## Context

Measured 2026-10-06 01:50: Git for Windows mounts `/tmp` as `usertemp` (`/etc/fstab`:
`none /tmp usertemp`). The folder is resolved once, from the TEMP of the first msys process of
the logon session, and shared by every msys process until the last one exits. A role run starts
with `TEMP=E:/AI/tmp-team/<task>-<role>-<id>` (ADR-0214 addendum 19). When that run's bash was the
first msys process, `/tmp` was the run's folder for the whole machine; `Remove-TeamRunTemp`
deleted the folder when the run ended, and from then on every bash on the PC printed
`could not find /tmp` and `mktemp` failed (six gate tests red with nothing wrong in the code).
`mount` showed `E:/AI/tmp-team/jarvis-calls-owner-worker-882d335e on /tmp`.

## Decision

- `Remove-TeamRunTemp` empties the run's folder (each child, best effort) and keeps the empty
  folder itself. A file still held open stays and is named in a warning; nothing is thrown and
  nothing goes to the pipeline. A folder with a link inside is still left whole.
- `Invoke-TeamRunTempSweep` (called by `Remove-TeamRunTemp` on the run temp root) removes a run
  folder only when it is empty AND last written more than 24 h ago AND is not the folder Git
  Bash's `mount` names as `/tmp`. `mount` is read through Git's own `bash.exe` (found beside
  `git.exe`, never System32's WSL bash). When bash cannot be asked, or `mount` names no `/tmp`,
  nothing is removed.
- The per-run TEMP stays (C: must not fill again); what a run leaves is still deleted at once,
  only the empty folder lingers for a day.

## Consequences

- An empty folder per run lives under `run_temp_root` for about a day (no disk cost).
- The folder that is the machine's `/tmp` is never removed while `mount` says so; once the last
  msys process exits and a new session picks another TEMP, it is swept a day later.
- Each run end asks bash once (`bash -c mount`, 30 s cap).
- `team-cycle.tests.ps1`'s case "each run gets its own temp folder ... removed when the run
  ends" must now assert "exists and is empty" instead of "is gone".

Proof: `scripts/tests/team-run-temp.tests.ps1` (9 cases; the five acceptance cases each RED under
a mutation restored from a backup).
