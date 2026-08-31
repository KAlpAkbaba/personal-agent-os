# Recovery & Self-Healing

## 1. Principle

The main Agent OS cannot be the only process responsible for deciding whether the Agent OS is healthy.

## 2. Recovery Supervisor

Deploy outside the normal application containers where possible, e.g. a small host-level systemd service on Linux and updater/watchdog service on Windows.

Responsibilities:

- know active release ID;
- know last-known-good release ID;
- poll health endpoints;
- observe crash loop/restart counters;
- validate DB migration state;
- roll back application release if release health policy fails;
- report recovery incident after restoration.

It should not contain model reasoning.

## 3. Release directories/manifests

Versioned releases, not in-place overwrite.

Example:

```text
/releases/1.4.1
/releases/1.4.2
/current -> 1.4.2
/last-known-good -> 1.4.1
```

Container deployment uses equivalent immutable image tags/digests.

## 4. Health policy

A candidate is unhealthy when combinations of these exceed thresholds:

- required service health fails;
- crash loop;
- API cannot complete basic synthetic request;
- Temporal worker unavailable beyond grace period;
- DB migration mismatch;
- device broker cannot accept test connection;
- severe error-rate jump.

Avoid rollback on one transient request.

## 5. Database safety

Before risky migration:

- validate migration on copy/test DB;
- create backup/snapshot;
- use expand/contract migration when possible;
- do not make code rollback impossible with a destructive migration unless a restore path has been tested.

## 6. Windows update recovery

Windows Agent updater:

1. download candidate;
2. verify hash/signature manifest;
3. stage in new version directory;
4. preserve old version;
5. activate;
6. perform service + companion health checks;
7. if failure, restore old binaries/config pointer;
8. record incident.

## 7. Automated incident repair

After service restoration, Evolution Engine may diagnose the failed release in an isolated environment and produce a new candidate. Recovery occurs first; coding happens second.
