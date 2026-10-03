# ADR (draft, staging-stack): a free staging stack on the home PC for the test team

Status: accepted (owner's idea 2026-10-03, "Ücretsizle başlayalım"; a paid VM / mini PC is
a later owner decision).

## Context

711 of 750 v1.0 items are DONE but only 153 are PROVEN_REAL: the gap is real use, and only the
owner tries things. The owner wants a test team that uses the product "like the device owner".
First it needs a place where nothing it does can touch the owner's memory, ledger, devices or
accounts, and that does not share the dev stack the gate resets (the gate of 2026-10-03 22:29
was red because a gate and agents shared the dev stack's Temporal queue).

## Decision

`infra/docker/docker-compose.staging.yml`, compose project `pagentos-staging`, on the home PC's
Docker Desktop: its own Postgres (pgvector, db `pagentos_staging`), Temporal (namespace
`pagentos-staging`, task queue `pagentos-staging`), Redis, MinIO (bucket
`pagentos-staging-artifacts`), the api (one colour, embedded worker, `PAGENTOS_ENVIRONMENT=staging`)
and the web shell (rewrites `/api` to `http://api:8001`). Named volumes `pagentos-staging-*`.

Ports (127.0.0.1 only, the 28xxx block; nothing in the repo or on the PC used it on 2026-10-04):
web **28000** (the url), api 28001, Postgres 28432, Temporal 28233, MinIO 28900; Redis not
published. Dev stack keeps 15432/16379/17233/18233/19000/19001, dev web 3000/3100, dev api 8001.

Scripts: `scripts/staging/deploy.ps1 <sha>` (refuses a sha not on main / team/nightly/lead,
exit 2; `git archive` of that commit -> images `pagentos-staging/{cloud-core,web}:<sha12>` ->
up.ps1), `up.ps1` (refuses under 6 GB free, exit 3; data services -> `alembic upgrade head` ->
api + web -> health wait; an older image on a newer schema is kept as is - expand-only),
`down.ps1` (`-Wipe` drops the staging volumes), `seed.ps1` (mints staging's OWN owner credential
in the container via `app.identity.recover --rotate --json`, exchanges it for a `web` session,
proves it on `/v1/identity/sessions/current`, stores both in
`%LOCALAPPDATA%\PagentOS\staging\owner.json`, user-only ACL; prints no secret).

Isolation (the point): `services/api/tests/unit/test_staging_isolation.py` reads the compose file
and the four scripts and fails on any production marker (100.90.158.26, pagentos-core, the
tailnet DNS name, pagentos_prod / pagentos-prod, /mnt/pagentos-data, /opt/pagentos), on a real
mail/calendar domain or a non-empty mail/calendar/IMAP/SMTP setting, on a key that is not a
`staging-only` constant or a `PAGENTOS_STAGING_*` interpolation, on ANY `${...}` that is not
`PAGENTOS_STAGING_*` (so a shell or `.env` carrying the owner's real key cannot leak in), on an
env_file, on a volume that is external / not `pagentos-staging-*` / named like the dev or prod
stack, on a bind mount, on a non-loopback or colliding port, and on the namespace / queue / bucket
/ database not being staging's own. Mail and calendar: none connected (send and calendar writes
off); voice: the simulator answers (free); a real vendor only with a separate
`PAGENTOS_STAGING_VOICE_OPENAI_API_KEY` test key.

Differences from production, deliberate: one api colour, no edge; the embedder is
`deterministic` (no 100 MB model download; health says `semantic: false`); no backup mounts
(health: `backup: skipped`).

## Footprint (measured 2026-10-04, docker stats, idle after deploy)

| container | memory | limit |
|---|---|---|
| api | 293-313 MiB | 2 GiB |
| minio | 226-233 MiB | 512 MiB |
| postgres | 178-180 MiB | 1 GiB |
| temporal | 82-98 MiB | 1 GiB |
| web | 39-41 MiB | 512 MiB |
| redis | 5 MiB | 256 MiB |
| **total** | **~0.85 GiB** | 5.25 GiB cap |

Images: cloud-core 1.38 GB + web 391 MB per deployed sha (old tags are not pruned by deploy;
`docker image rm pagentos-staging/...:<old>` frees them). First deploy 7m43s (cold web build),
a deploy of an already-built sha 44 s. With ~0.85 GiB idle and a 5.25 GiB cap against 48 GB,
staging and a gate FIT together; the guard is up.ps1's 6 GB-free floor (a start under memory
pressure is refused and says to ask the test-slot queue for `heavy`). Staging need not be OFF
while a gate runs.

## Release (optional, not wired here)

The release step may gain one line: "deploy to staging first" -
`scripts/staging/deploy.ps1 <candidate sha on team/nightly/lead>` + `seed.ps1` + the test team's
run, before the Cloud Core release. Not wired into the release by this task.

## Consequences

The test team (next card) has a url, an owner session and a database of its own. Gate/CI wiring
of `scripts/tests/staging.tests.ps1` needs `scripts/quality-gate.ps1` and `.github/workflows/ci.yml`
(outside this card's area); the pytest file runs in the unit suite already.
