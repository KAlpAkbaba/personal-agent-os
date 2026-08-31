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
