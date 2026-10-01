## ADR (unnumbered) - The maintenance window is one script with three modes (follow-up of ADR-0223)

**Decision.** `scripts/cloud/maintenance-reboot.sh` carries ADR-0223 steps 1-11:
`--preflight` (steps 1-5 as named check lines, each saying what it read; exit 10 when any
fails; changes nothing), `--preflight --run` (steps 6-10; `--run` alone is refused, exit 64;
the marker `$base/MAINTENANCE_MARKER` with start epoch, old kernel and release is written
after the last container is back and BEFORE `reboot`), `--verify` (step 11; exit 20 without a
marker, 21 when the kernel is unchanged, reboot-required remains, the reconcile journal has no
`RECONCILE OK`, containers/timer/zombies/health are wrong; on success writes
`$base/LAST_MAINTENANCE.json` with `downtime_seconds` = first good probe minus marker start,
and removes the marker).

**Choices.** The recovery pin is read from `$recovery_root/APPROVED_SHA`. Step 3 (team cycle,
owner mid-task) lives on the home PC, so the script only sees a release in flight (the
blue/green operation lock) and says the rest is the lead's check. Step 5 (device presence) is
stored as the health body in the marker, not parsed. The downtime starts at the marker (just
before `reboot`), so it includes the shutdown, not the last good probe of the old boot.

**Evidence.** PROVEN_AUTOMATED with fakes (scripts/tests/maintenance-reboot.tests.ps1);
PROVEN_REAL only at the first window.
