# Cloud Infrastructure — Initial Production Plan

## 1. Preferred platform

### Compute: Hetzner Cloud — Nuremberg (NBG1)

Reasoning:

- close European location for Turkey compared with US-only hosting;
- straightforward VM model;
- good cost/performance for a single-owner private service;
- private network/firewall/API support;
- separate S3-compatible Object Storage available in Nuremberg.

### Initial VM class

Target approximately:

- 8 vCPU
- 16 GB RAM
- ~300 GB local NVMe class storage
- Ubuntu current LTS
- IPv4 + IPv6 for compatibility

As of package creation, Hetzner advertises a CPX42-class shape with 8 vCPU / 16 GB RAM / 320 GB in NBG1. Claude must verify current availability/pricing in official docs/provider console before provisioning and write the selected SKU to `docs/DECISIONS.md`.

A 4 vCPU / 8 GB VM can be used for early cloud staging, but observability + Temporal + PostgreSQL + application containers are more comfortable at 16 GB.

No cloud GPU is required for the initial architecture.

## 2. Storage

### Local VM disk

Use for:

- Docker images;
- live PostgreSQL volume;
- live Temporal persistence if sharing PostgreSQL;
- caches;
- transient processing;
- logs with bounded retention.

### Hetzner Object Storage NBG1

Use S3 abstraction for:

- canonical artifact binaries;
- PDF/DOCX/audio render output;
- encrypted database backups;
- release bundles as secondary copy;
- log archives if needed.

Keep bucket private. Enable versioning/retention features where supported and appropriate.

Development uses MinIO through the same S3 interface.

## 3. Private connectivity

Install Tailscale on:

- owner Windows PC;
- owner phone;
- cloud VM;
- future enrolled devices.

Initial web UI should be available only within the tailnet through a private HTTPS/Serve path. Avoid a public login surface while the product has one owner.

Use Tailscale Grants/ACL policy to limit cloud internal ports and device communication. Because this is a single owner, policy can remain simple while still denying unnecessary lateral traffic.

## 4. DNS / Cloudflare

Cloudflare is **optional**, not required for M0-M4.

Add Cloudflare DNS later if the owner wants a custom public domain. If the UI remains tailnet-only, Tailscale DNS/Serve can be sufficient.

Do not use Cloudflare Tunnel just because it exists; it creates another public access path. Use it only if a clear requirement appears.

## 5. Container topology on the VM

Initial Docker Compose services:

```text
reverse-proxy / private ingress
api
web
orchestrator
temporal-worker
memory-service
artifact-service
voice-gateway
narration-service
model-gateway
device-broker
evolution-controller
postgres
redis
temporal-server
otel-collector
prometheus
grafana
loki
recovery-supervisor (prefer host/systemd boundary)
```

Some services may initially share a process/package to reduce operational complexity. Service boundaries are logical; do not create unnecessary microservices before load or failure isolation justifies them.

## 6. PostgreSQL

Single PostgreSQL instance initially with separate databases/schemas for:

- app domain data;
- pgvector memory indexes;
- Temporal (if supported configuration is selected).

Required:

- daily logical backup;
- pre-migration backup;
- WAL/point-in-time strategy considered before the project contains valuable long-lived history;
- health/slow-query metrics;
- migrations managed in source control.

## 7. Backups

Minimum automated policy:

- database logical backup: nightly;
- artifacts: stored in object storage, versioning/retention where appropriate;
- server configuration/IaC: git source of truth;
- encrypted backup copy to object storage using restic/kopia or equivalent;
- snapshot before risky major infrastructure upgrades;
- periodic restore test, not merely backup-success check.

Suggested retention to start:

- daily: 14
- weekly: 8
- monthly: 6

**As built (ADR-0122, 2026-09-11).** `scripts/cloud/install-backup.sh` on the host installs
restic (Ubuntu archive), a root-only repository password, the scripts pinned under
`/opt/pagentos-backup` (digest-checked by systemd before every run) and two timers:
`pagentos-backup.timer` nightly at 00:30 UTC and `pagentos-restore-drill.timer` weekly.
The repository is `/var/lib/pagentos-backup/restic` on the root disk - not the data volume
it protects - and is copied off the host whenever `/opt/pagentos/backup-offhost.env` names
a second repository. Every snapshot holds all databases (pg_dump -Fc, roles), every bucket
(through the MinIO API), the env file, the markers, the owner-credential root, the edge
state, the recovery bundle and the host's pagentos units, with a manifest of every file's
sha256 and per-table row fingerprints computed from the dumps. Both release paths take a
`pre-migration` snapshot before any migration and stop if they cannot. The drill
(`restore-cloud-core.sh --drill`) restores into scratch containers and verifies files,
rows and objects; `--apply` is the guarded real restore. The repository password lives on
the host and, escrowed, on the owner's PC (`scripts/cloud/escrow-backup-key.ps1`) - without
it no copy of any backup can be opened.

Claude should adapt after actual storage growth is known.

## 8. CI/CD

### Source

Private GitHub repository.

Branches:

- `main`: last approved/production-ready line
- `develop`: optional integration line if useful
- `agent/*`: autonomous worktree/feature branches
- `release/*`: release candidate when needed

### CI

GitHub Actions:

- lint;
- unit tests;
- integration tests;
- dependency/license/security checks;
- frontend build;
- backend build;
- Windows agent build on Windows runner;
- container build;
- SBOM/manifest;
- push container images to GHCR.

### CD

Initial:

- release manifest created after gates;
- cloud deployment service pulls versioned images;
- health validation;
- canary or staged activation;
- last-known-good pointer updated only after stability window.

Windows Agent updates:

- versioned signed/hashed package;
- download to new version directory;
- verify manifest/hash/signature;
- stop/start through updater;
- health check;
- rollback to previous version on failure.

## 9. Infrastructure as code

Before the first production deployment, Claude should create `infra/opentofu/` (or Terraform if current compatibility strongly favors it) for:

- server;
- firewall;
- network;
- SSH keys;
- object storage-related documented/manual boundary where provider API limitations apply;
- outputs for Tailscale bootstrap.

Secrets must not be embedded in IaC state committed to git.

## 10. Secrets

M0-M3:

- local `.env.local` / OS-protected bootstrap store, all gitignored;
- cloud root-owned secret files or Docker secrets with strict permissions;
- CI secrets in GitHub encrypted secrets.

Later:

- evaluate self-hosted OpenBao/other secret manager only if operational benefit exceeds complexity.

The agent must never log secret values.

## 11. Monitoring and self-healing signals

Collect at minimum:

- task success/failure rate;
- workflow retries;
- device online/offline;
- browser automation failure reason;
- TTS/STT provider latency/errors;
- narration underruns;
- PostgreSQL health;
- disk usage;
- memory/CPU;
- container restart count;
- release health window;
- user correction/retry metrics;
- evolution candidate evaluation scores.

## 12. Disaster recovery objective

Early target:

- RPO: <= 24h for non-critical memory during first prototype; tighten to <= 1h once valuable history accumulates.
- RTO: <= 1h prototype; automated rollback should recover software-release failures within minutes.

The project must later measure actual restore time and revise these targets.

## 13. Future HA

Do not implement on day one. If usage becomes critical:

- separate PostgreSQL;
- second cloud VM;
- replicated object/artifact strategy;
- Temporal HA deployment;
- load balancer.

Scale from measured need, not speculation.
