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
`staging-only` constant or a `PAGENTOS_STAGING_*` interpolation, on ANY `$` in the compose
file's RAW text (comments included) that is not on the allow-list - the `$$` escape, or
`${PAGENTOS_STAGING_X}` with at most a `:-`/`-` default of fixed text holding no `$` (the only
other: the `${...}` placeholder in a comment, which names no variable). Inspector's fourth
return, 2026-10-04: an unbraced `PAGENTOS_REDIS_URL: $PAGENTOS_REDIS_URL` passed 47/47 and
`docker compose config` filled it from the shell; now `$NAME`, `${NAME}`, `${HOME}`, `${X:?..}`,
a nested default and a list-form `- KEY=$VAR` are each refused (so a shell or `.env` carrying
the owner's real values cannot leak in). A build context is a `.`-relative path inside the
repository - never a git URL, `git@`, a bare `github.com/...` or an absolute path. On an
env_file, on a non-loopback or colliding port, and on the namespace / queue / bucket / database
not being staging's own. What the compose file may SAY is an ALLOW-LIST (inspector's third
return, 2026-10-04: `volumes_from: [container:pagentos-postgres]` - the dev stack's data - and a
top-level `secrets: owner: file: C:/Users/...` - a bind from the owner's home - both passed a
deny-list green): the top-level keys are the closed set `name`, `services`, `volumes`,
`networks`, `x-production-blackhole`; each service's keys are the closed set it actually uses
(image, build{context,dockerfile,args}, container_name, extra_hosts, command, environment, ports,
volumes, networks, healthcheck, depends_on, init, mem_limit, memswap_limit, pids_limit,
read_only, tmpfs, cap_drop, security_opt=no-new-privileges, restart); ANY other key -
volumes_from, secrets, configs, network_mode, pid, ipc, extends, env_file, devices, privileged,
cap_add, links, include, ... - is refused without having to be foreseen. A mount is only a named
volume declared in this file, both key and name `pagentos-staging-*`, with no key but `name`
(no external, no driver / driver_opts - a local driver with `o: bind` is a host bind by another
name); a bind / host path / `container:` source is refused. Networks: only staging's own,
declared here, named `pagentos-staging-*`, keys `name`/`ipam` only, never external. Proved on
the real compose file (planted, run, restored byte for byte, sha256 `a7079543d312aebd` before
and after each): volumes_from RED (2 failed), secrets `file:` RED (1 failed), a `driver_opts`
bind RED (1 failed), `PAGENTOS_REDIS_URL: $PAGENTOS_REDIS_URL` RED
(`test_staging_connects_no_real_account` failed); loosening the allow-list itself turns 5
planted cases RED, and scanning only `${` instead of every `$` turns 4 planted cases RED.
Mail and calendar: none connected (send and calendar writes
off); voice: the simulator answers (free); a real vendor only with a separate
`PAGENTOS_STAGING_VOICE_OPENAI_API_KEY` test key.

Secrets and accounts by FIELD, not by env name (inspector's second return, 2026-10-04: a real
CalDAV url/user/password and a hand-written `PAGENTOS_VOICE_AZURE_SPEECH_KEY` passed green, the
name lists missed `CALDAV_` and `_SPEECH_KEY`). Now the env-name lists cover `CALDAV_` and any
name ending in KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL, and - the sound check - the test walks the
api's RESOLVED `Settings`: every str field whose name holds key/secret/password/token/credential
is empty, a `staging-only*` literal, or set by exactly `${PAGENTOS_STAGING_*}` with no default
(a `:-sk-real` default is a real key); every account field (`caldav_*`, `calendar_ics_url`,
`mail_imap/smtp_host|user|password`, `mail_from`) is empty. MinIO's root user became
`staging-only-artifacts` so the S3 access key passes the same rule. Real-account domains gained
fastmail, zoho, gmx, mail.ru, proton.me, nextcloud, radicale and any `caldav.`/`carddav.` host.

Reaching production (inspector's return, 2026-10-04). The compose text was clean but the api
INHERITED `gods_eye_url = http://pagentos-core:4173/` from a Settings default, and from inside
`pagentos-staging-api` the name `pagentos-core` resolved to 100.90.158.26 with 22/443/8001/4173
open. Now: (a) the compose sets `PAGENTOS_GODS_EYE_URL: ""`, and the test builds the api's
`Settings` from staging's own env (no `.env`, the host's `PAGENTOS_*` cleared) and fails on any
production marker in ANY setting value - so the next inherited default is caught too; (b) every
service maps `pagentos-core` and `pagentos-core.tail0e6789.ts.net` to 192.0.2.1 (TEST-NET-1,
RFC 5737) via one `extra_hosts` anchor - the only place a production name may be written; (c) by
ADDRESS: staging's network `pagentos-staging-net` has subnet **100.64.0.0/10**, the tailnet's
own CGNAT range, so every tailnet address (the Cloud Core, the owner's PC and phone) is on-link
for the containers: ARP gets no answer and connect fails with `EHOSTUNREACH` instead of being
routed out through Docker Desktop and the host's Tailscale. The internet (vendors) stays reachable.
Measured 2026-10-04 00:5x with the api image on `pagentos-staging-net`: `pagentos-core` and the
tailnet name -> 192.0.2.1, every port timed out; 100.90.158.26:22/443/8001/4173 -> `[Errno 113]
No route to host`; api.openai.com:443 open. Known limit, honest: production's PUBLIC addresses
(the VM's internet IP, if it has one) are not blocked - only the tailnet range and the names are.
A full egress block (an internal network plus a vendor-allowlist proxy) is a separate card.
`deploy.ps1` now trusts only `origin/main` and `origin/team/nightly/lead` (a hand-made local
`main` cannot wave a sha through), and after a deploy removes `pagentos-staging/*` images except
this sha's and the previous one's (each sha is ~1.8 GB; C: ran out on 2026-10-03).

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

Images: cloud-core 1.38 GB + web 391 MB per deployed sha (deploy keeps this sha's and the previous
sha's tags and removes the rest). First deploy 7m43s (cold web build),
a deploy of an already-built sha 44 s. With ~0.85 GiB idle and a 5.25 GiB cap against 48 GB,
staging and a gate FIT together; the guard is up.ps1's 6 GB-free floor (a start under memory
pressure is refused and says to ask the test-slot queue for `heavy`). Staging need not be OFF
while a gate runs.

Real run, 2026-10-04 09:03-09:16 UTC, under test-slot ticket `ts-3801d5c1e4e5` [heavy], while
two gates ran on the dev stack (`gate-faster` [database,heavy] from 08:41Z, the lead's gate "API
unit tests" [heavy] from 09:06Z): health ok on `6a21294c` (schema `0065`, environment `staging`,
web 200) -> seed: owner session valid -> deploy `eac453fc` (origin/main) exit 0 in 470 s, health
ok -> deploy back `6a21294c` exit 0 in 293 s, health ok -> seed again: valid. A probe row written
before the first deploy and the owner sessions (2) were still there after both moves. docker
stats after: api 305 MiB, minio 239, postgres 128, temporal 99, web 39, redis 4 (~0.8 GiB).
Dev reset, 11:10 UTC, ticket `ts-441ebe445f80` [database], right after the lead's gate had run
"Dev stack up" + alembic + the API integration suite on the dev stack: `docker compose` (project
`pagentos`) down -> staging api 200 while dev was gone -> `scripts/dev-up.ps1` -> dev postgres and
temporal restarted (11:10:52Z/11:10:57Z); staging's postgres/temporal kept their 07:09Z start,
the probe row and the 3 owner sessions stayed, health ok on `6a21294c`. Volumes are disjoint:
`pagentos-staging-{postgres,minio,identity}` vs `pagentos_pagentos-*-data`.

## Release (optional, not wired here)

The release step may gain one line: "deploy to staging first" -
`scripts/staging/deploy.ps1 <candidate sha on team/nightly/lead>` + `seed.ps1` + the test team's
run, before the Cloud Core release. Not wired into the release by this task.

## Consequences

The test team (next card) has a url, an owner session and a database of its own. Gate/CI wiring
of `scripts/tests/staging.tests.ps1` needs `scripts/quality-gate.ps1` and `.github/workflows/ci.yml`
(outside this card's area); the pytest file runs in the unit suite already.
