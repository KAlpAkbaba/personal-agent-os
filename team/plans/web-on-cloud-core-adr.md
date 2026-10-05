## ADR (unnumbered) - The owner's web shell runs on the Cloud Core, reached over the tailnet with HTTPS (2026-10-03)

**Why.** Owner decision 2026-10-03: he wants the web shell (Ofis, Onay Merkezi, the voice
page) on his PHONE when he is outside. Until now it ran only on the home PC
(`scripts/voice/start-web-voice.ps1`: `pnpm --dir apps/web dev --port 3000`, API over the
tailnet through the same-origin `/api` rewrite), so it was gone whenever the PC was off. He
chose to host it ON the Cloud Core; the phone reaches it over his tailnet (Tailscale), over
HTTPS - a browser gives the microphone only to a secure context. Nothing is exposed to the
public internet.

**The shape.**

- **Image** `infra/docker/web/Dockerfile` (multi-stage, Node 24 bookworm-slim). Build stage:
  pnpm 11.24.0 from the workspace lockfile (`--frozen-lockfile --filter @pagentos/web...`),
  `next build` with `NEXT_PUBLIC_API_BASE=/api`. Final stage: only Next's traced `standalone`
  output + `.next/static` + `public/`, run as uid 10003 on fixed port 3000, with a
  HEALTHCHECK on `/` (a prerendered page - it answers whether or not the api is up or
  mid-release). The build context is the repository root, cut to an allowlist by
  `infra/docker/web/Dockerfile.dockerignore` (lockfile, workspace file, `apps/web`, and the one
  file outside the package the shell imports, `packages/protocol/realtime-session-contract.json`
  - the first build failed on exactly that); every `.env*`, `node_modules`, `.next` and the
  rest of the repository never reach the build daemon. No secret in the image or its build
  arguments: the only arguments are `/api` and `http://edge:8001`.
- **`standalone`, not bare `next start`.** `apps/web/next.config.ts` gains an opt-in
  (`PAGENTOS_WEB_STANDALONE=1`, set only by the Dockerfile) for `output: "standalone"` and
  the workspace as tracing root. Unset, the config is byte-for-byte what the dev server and
  the quality gate's build already used (a vitest file holds that). Reason: the traced output
  carries only what the server needs (45 MB layer) instead of the workspace's whole
  `node_modules` plus pnpm in the final image. It is Next's own production server.
- **The upstream is a build argument, by necessity.** `next build` evaluates the rewrite
  and stores it in `.next/routes-manifest.json`; `next start`/`server.js` do not re-read
  `PAGENTOS_API_UPSTREAM` (proved: a container started with a different value still proxied to
  the baked `edge:8001`). The value is `http://edge:8001` - the edge, which follows the
  blue/green switch, never an api colour - so a release or a rollback of the api needs no
  touch of this container. A test holds that the compose argument, the Dockerfile default and
  nginx's `listen` agree.
- **Compose service `web`** in `docker-compose.prod.yml`, profile `aux` (the godseye
  precedent, ADR-0197): `127.0.0.1:3000:3000` - **loopback only**, no tailnet or public
  bind; `mem_limit`/`memswap_limit` 512m, `pids_limit` 256, read-only root with a `/tmp`
  tmpfs, `cap_drop: ALL`, `no-new-privileges`, `init`, healthcheck, `restart: unless-stopped`,
  no `depends_on`, no `env_file`, no environment at all.
- **HTTPS: `tailscale serve`, never `funnel`.** `scripts/cloud/enable-web-tailnet-https.sh`
  (run once on the host by the lead): `tailscale serve --bg --https=443 http://127.0.0.1:3000`
  - `--bg` makes the configuration persist across reboots and tailscaled restarts. Idempotent
  (reads `serve status`; already served = no call), `--status` (exit 0 served / 1 not /
  4 funnel on), `--off` (`tailscale serve --https=443 off`). It refuses `funnel` in any
  form (its single tailscale wrapper runs only `serve` and `status`; a Funnel found ON stops
  it with exit 4 and a message - it does not even reach for it to switch it off). If the
  tailnet's HTTPS certificates are not enabled it exits 3 and prints the one owner line.

**Memory budget (measured locally on the built image, 2026-10-03, Docker Desktop).** Image
94 MB. Idle 40 MiB (cgroup), 47 MiB after a burst of page loads and proxied calls, 95 MB peak
RSS of the node process. Limit 512 MiB = five times the peak, on a 7.7 GiB host that also
runs the api, Postgres, Temporal, Redis, MinIO and godseye (2 GiB limit). **The build is the
expensive part, not the run:** `next build` (Turbopack) peaks near 2.1 GiB (measured with the
build stage under a cgroup: SIGKILLed at 1 GiB and at 1.5 GiB, passes at 2 GiB; 2 and 4 CPUs
alike; ~10 s). `--webpack` needs ~1 GiB but fails type-checking on a pre-existing
`app/gods-eye/page.tsx` export (`GODS_EYE_URL`), so it is not an option without touching the
app. Hence a guard in the release: `aux_up` reads `MemAvailable` and, below
`PAGENTOS_WEB_BUILD_MIN_AVAILABLE_MB` (default 3072), starts the web image that already exists
with `--no-build` instead of building (said in the output); unreadable meminfo errs safe.
Compose layer caching makes an api-only release rebuild nothing; only a web source change
builds.

**A release and a rollback.** `release-cloud-core-bluegreen.sh`'s `aux_up` (unchanged in
kind) now loops `godseye`, `web`, one `compose up -d --no-deps` call each, AFTER the api's
transaction (after `RELEASE OK`), each failure swallowed and printed (`aux: web NOT up`),
never `--wait`, never `--force-recreate`. A failing web build therefore cannot fail, delay or
roll back the api release (red-first tests with a failing fake `up`, with both aux services
failing, and with low memory). A rollback is the api's own switch: the web container is not
part of it, keeps serving, and follows the edge to the old colour. The reconcile and the
recovery path never name `web` (a test holds it); a compose change makes the pinned recovery
bundle stale as always - re-pin with `install-recovery-supervisor.sh <sha>` (an owner/lead
step, existing rule). A broken web image is rolled back by redeploying the previous sha; the
api is unaffected either way. The maintenance-reboot window's container list is unchanged
(it still names godseye, not web): the web container restarts by policy after a reboot.

**What the owner must do (his, not ours).** (1) Install the Tailscale app on the phone and
sign it into his tailnet. (2) If HTTPS certificates are not yet on: Tailscale admin console ->
DNS -> MagicDNS on, then HTTPS Certificates -> Enable HTTPS. (3) Open
`https://<host>.<tailnet>.ts.net/` (the lead reads the exact name from `--status`) and sign in
ONCE with the owner credential: the session token lives in the browser's `localStorage`
(`apps/web/app/lib/session.ts`, its own trade-off paragraph); it is per-origin, so the tailnet
origin starts signed out, and a phone that is lost is revoked from another client. Allow the
microphone for that origin. Note: a Tailscale HTTPS certificate puts the machine's `.ts.net`
name in public Certificate Transparency logs (the name, not the content).

**Known, not done here.** (a) The `/gods-eye` page frames `http://<host>:4173/`; on an https
origin that is mixed content and the browser blocks it - the page's new-tab link still works,
the inline globe does not. A follow-up could serve godseye through `tailscale serve --https=8443`
too. (b) `docker compose up` on the host builds on the VM; a pre-built image from the home PC
would remove the 2 GiB transient. (c) The web shell has no server-side auth of its own - the
api's owner session is the boundary, and the tailnet is the network boundary. (d) Not verified
on the real host: the `Dockerfile.dockerignore` lookup needs BuildKit (the compose v2 default;
Docker Desktop 28 / compose 2.38 here), `tailscale serve`'s wording on the host's version,
and the first real build's time and memory on a CPX32.

**Evidence.** PROVEN_AUTOMATED: `services/api/tests/unit/test_compose_web_shell.py` (14: aux
profile, loopback-only ports, memory + swap limit, healthcheck port, edge upstream agreeing
across compose / Dockerfile / nginx, no secret, non-root final stage, allowlist context, aux
loop and its place after `RELEASE OK`, no `web` in the reconcile/recovery logic, the tailnet
script's port equals the published port); `scripts/tests/cloud-release-bluegreen.tests.ps1`
(+7: aux build order, failure isolation, low-memory path); `scripts/tests/web-tailnet-https.tests.ps1`
(21, with a fake tailscale; mutations of the target and of the idempotence check turn five
red); `apps/web/tests/deploy/next-config.test.ts` (4). PROVEN_REAL locally only: the image
built, started read-only with all capabilities dropped, answered `/`, `/voice`, `/core/office`
and proxied `/api/*` to an `edge:8001` stand-in. NOT_RUN: the real host and a real tailnet.
