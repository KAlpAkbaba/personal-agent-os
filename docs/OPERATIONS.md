# Operations Runbook

The final owner should rarely need this file; autonomous services use the same procedures.

## Daily automated checks

- cloud VM health;
- disk use;
- DB backup status;
- restore-test freshness;
- service health;
- Temporal backlog;
- device connectivity;
- provider error rates;
- artifact storage errors;
- failed releases/incidents.

## Release

1. CI green.
2. Immutable version created.
3. Manifest/digests generated.
4. Staging/shadow if relevant.
5. Candidate deployment.
6. Synthetic health tests.
7. Stability window.
8. Promote and update last-known-good.

## Rollback

1. Recovery supervisor detects threshold.
2. Activate previous immutable release.
3. Validate database compatibility.
4. Run synthetic check.
5. Mark incident.
6. Evolution system investigates after service restoration.

## Backup restore drill

At a regular interval:

- create disposable environment;
- restore database;
- restore sample artifacts;
- run basic application read test;
- record restore duration/result.

## Capacity triggers

Scale VM or split services when sustained metrics show:

- memory pressure;
- CPU saturation;
- disk I/O bottleneck;
- DB latency;
- Temporal backlog;
- artifact throughput issues.

Do not pre-emptively create cluster complexity.

## Recovery objectives — measured, not promised (B08 req 649/650)

These two numbers are not targets written down once. They are read off the last real run of
each half of the safety net and published on `/v1/system/health` under `checks.backup`:

| | What it is | Where the number comes from |
|---|---|---|
| **RPO** — `rpo_hours` | How much work the owner would lose if the host died right now | The age of `LAST_BACKUP.json`'s `finished_at`. The backup runs nightly, so this is normally under 24 hours; past **36 hours** the check is `fail`, because the promise the schedule makes is no longer being kept. |
| **RTO** — `rto_seconds` | How long a restore actually takes | `seconds.total` from the most recent restore-drill report. The drill restores into a scratch environment and verifies databases and objects, so this is a measurement of the real path, not an estimate of it. |

A number in this document would be a claim. A number read off the last run is a measurement,
and it is wrong the moment the thing it describes changes — which is the point.

**What these numbers do NOT cover.** Both assume the host is recoverable or replaceable and
the backup repository is reachable. Losing the host *and* the off-host copy is outside them
entirely; `checks.backup.offhost` says whether that second copy is currently succeeding, and
`failed_units` says whether the scheduled runs themselves are failing. Off-host disaster
recovery is its own batch (B09) and its RPO/RTO are, honestly, unbounded until it exists.

**Freshness of the RTO.** `drill_stale` is true when the last drill is older than 14 days or
has never run. A backup is not proven without a restore, and the proof ages: an RTO measured
two months ago describes a system that has changed since.

### When a scheduled unit fails

Every timer-driven unit carries `OnFailure=pagentos-failure-marker@%n.service`, which writes
`/var/lib/pagentos-backup/failures/<unit>.json`. That marker — not a journal line — is what
`checks.backup.failed_units` reports, and the unit's next successful run removes its own.

The marker unit deliberately needs no credential and no network: the moment a backup fails is
exactly the moment the host may be unwell, and an alert that needs the API to be up cannot
report that the API is down.

## Losing the host entirely (B09 req 644)

Everything above assumes the host is recoverable. This section is what to do when it is not.

**The gap this closes.** The nightly backup has copied every snapshot to a second repository
for weeks, and until 2026-09-13 nothing could read that copy back: `restore-cloud-core.sh`
only ever looked at `$backup_root/restic`, which lives on the disk the backup exists to
protect. The off-host copy was write-only. A backup you cannot restore from is a file.

**Order matters, because two things are deliberately NOT in the backup.**

1. **The repository password.** It is not in the snapshot — it would be locked inside what it
   opens. It is escrowed off the host by `scripts/cloud/escrow-backup-key.ps1`. Put it back at
   `/opt/pagentos/backup.password`, root-owned, mode 600, before anything else. Without it
   `--from-offhost` stops with exit 92 rather than failing later and less clearly.
2. **The off-host credentials.** `backup-offhost.env` names the second repository and carries
   its access key. On a rebuilt host you write this file from the secret store; it is never in
   a commit and never in a log. Without it, exit 91.

Then:

```
restore-cloud-core.sh --from-offhost --drill              # prove it reads, on scratch containers
restore-cloud-core.sh --from-offhost --apply --snapshot <id> --confirm "RESTORE <id> OVER PRODUCTION"
```

The drill restores into throwaway containers and verifies every file against the snapshot's
own manifest, every database against the fingerprint taken from its dump, and every object
through the MinIO API. It also refuses a snapshot that cannot rebuild a host: `config/.env`,
`config/RELEASE` and `postgres/globals.sql` must all be present.

**What comes back.** Databases, objects, `.env`, RELEASE and LAST_KNOWN_GOOD, the owner
credential root, the edge state, the pinned recovery bundle and the host's own systemd units.
**What does not:** container images (every release image is rebuilt from its commit) and the
two secrets above.

**Honest status.** The path exists and is tested; the second repository itself is not
configured yet — it needs an S3-compatible bucket and an access key, which is an owner action.
Until it is, `/v1/system/health` reports `checks.backup.advisories: ["no_offhost_copy"]`, and
the recovery objectives above cover losing the host's *data*, not losing the host.
