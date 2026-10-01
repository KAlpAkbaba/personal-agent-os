**Şu an üzerinde çalışılan:** maintenance-reboot-script, alan `scripts/cloud/maintenance-reboot.sh` + test + ADR taslağı, makine: bu PC (worktree worker-maintenance-reboot-script). Bu blok lead'in HANDOFF'a yazması içindir; ben HANDOFF'a dokunmadım.

**sha:** f1b5193b6e88b94af9d9fa131d3d95a725923fa4 (branch pushed, worktree clean)
**Files changed:** 3, all inside the area. They are `scripts/cloud/maintenance-reboot.sh`, `scripts/tests/maintenance-reboot.tests.ps1` and `team/plans/maintenance-reboot-script-adr.md`.

**Script**
- `--preflight` runs steps 1-5 as named lines, each saying what it read. It exits 10 if any check fails and changes nothing.
- `--preflight --run` runs steps 6-10: backup, stop the reconcile timer, remove the leftover container, apt upgrade, wait for containers, write the marker, reboot. `--run` alone is refused with 64.
- `--verify` exits 20 without a marker and 21 on a failed check (the marker stays, so it can be re-run). On success it writes `LAST_MAINTENANCE.json` with `downtime_seconds`, measured from the marker to the first good health probe, and removes the marker.
- The recovery pin is read from `$recovery_root/APPROVED_SHA`.

**Tests**
- RED first: before the script existed, the suite failed 12 of its 13 checks. The only pass was "preflight changes nothing", which a missing script satisfies trivially.
- GREEN: 23 passed, 0 failed with the script. `bash -n` is ok.
- Preflight refuses, with the exit code and failing line asserted, on each of: health not ok, failing_checks set, health unreachable, docker-ce held, disk 85 %, `apt-get -s upgrade` removing a package, backup older than 24 h, a failure marker, and pin != RELEASE. It passes when everything holds.
- `--run` without `--preflight` is refused and does nothing. With a failed preflight it stops before `systemctl stop` and `reboot`.
- Order in `--run`: backup, then timer stop, then leftover removal, then apt upgrade. The fake `reboot` records whether the marker existed when it was called (yes).
- `--verify` refuses on the old kernel, a reconcile line without OK, and `reboot-required` still present. Its success case writes both kernels and a downtime between 120 and 200 s, and removes the marker.

**Mutation proof** (working copy backed up to a scratch file, restored byte-for-byte; sha256 of the restored file equals the original `2d0fbccf…49a6` both times)
- Pin check removed: RED, "preflight refuses (10): recovery pin != RELEASE" fails.
- Marker written after `reboot`: RED, "the maintenance marker exists when reboot is called" fails.

**Evidence class:** PROVEN_AUTOMATED (fakes) for everything above. PROVEN_REAL only at the first window; NOT_RUN on a real host.

**For the lead at merge**
- Add `scripts/tests/maintenance-reboot.tests.ps1` to `quality-gate.ps1` and `.github/workflows/ci.yml`.
- Number the ADR from `team/plans/maintenance-reboot-script-adr.md` into `docs/DECISIONS.md`.
- Clear "Sıradaki işler" in HANDOFF.
- I did not touch BUILD_STATE.json or THIRD_PARTY_COMPONENTS.md.

**Not done / open risks**
- Step 3 (team cycle, owner mid-task) cannot be seen from the host. The script only checks the blue/green lock and says so in its output.
- Step 5 (device presence) is stored as the raw health body in the marker, not parsed. The "same two device ids online" check after boot stays with the lead.
- The `failing_checks` parsing accepts `""`, `[]` or an absent key. I read the real `/v1/system/health` output only through the fakes, so a real-host dry `--preflight` is the first thing to run.
- The container wait matches names by substring (`postgres redis minio temporal edge api-blue godseye`). I have not checked that against the real `docker ps` names.
- Downtime starts at the marker, just before `reboot`, so it includes shutdown time.
- I did not write a mutation for "timer stopped after the upgrade". The order assertion covers it in principle but I did not run it.
