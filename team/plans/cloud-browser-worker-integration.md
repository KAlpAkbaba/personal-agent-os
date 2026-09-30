# cloud-browser-worker — integration plan (cycle pilot-02, integrator)

Card: ADR-0213 PR 2. Written 2026-09-30. Nothing was added to any tree by this step.

## Decision: ADAPT (our own bridge, existing worker, two already-approved libraries)

No off-the-shelf "Playwright worker that speaks our device protocol" exists, and none could:
the protocol is ours (hello / challenge / ECDSA auth / command_ack). What exists in the world is
only the *container recipe* (Playwright in Docker: `--init`, `--shm-size`, memory cap; Bug0,
Browserless, Playwright's own docs). Adopt the recipe, build the bridge.

What is reused, unchanged: `browser_agent.worker` (stdio JSON-lines worker, BROWSER_CAPABILITIES §7),
`ManagedBackend` headless, the `research` profile discipline, `RESEARCH_SESSION_CLASSES`
(READ+NAVIGATE), `require_public_destination` (private/tailnet/metadata addresses refused; the
worker is started WITHOUT `--allow-private-destinations` and WITHOUT `--trusted-origin`).

## The seam (why a bridge is needed)

The worker speaks stdio to a *companion*; the companion (C#) is what dials the broker on Windows.
On Linux nothing plays the companion. So `browser_agent/cloud/` is a small Python companion:

1. `config.py` — read env/files, refuse to start without enrollment material.
2. `policy.py` — the cloud session policy: READ+NAVIGATE. `assert_policy_within(...)` refuses
   anything wider (ADR-0213 addendum: cloud ACTS only on the owner's allow-list; that list lives
   in Core (task 5 wiring) and arrives as a per-command decision, never as a wider default here).
3. `broker.py` — REST enroll (`urllib`, stdlib) + WS client: hello → challenge → auth (ECDSA
   P-256 over `nonce_bytes || device_id_utf8`, DER or P1363), heartbeat, command → worker `exec`
   → `command_ack` (accepted → running → succeeded|failed), idempotency map, expiry, cancel.
4. `__main__.py` — build the worker child (`--headless --channel chromium --data-dir …`), run bridge.

## Contract facts found (read from the code, not assumed)

- There is NO `device_kind` field anywhere in the device protocol or the `devices` table.
  Enroll body is `{token, name, platform, public_key_spki_b64, capabilities}`; `platform` is a
  free string (1..64). The worker sends `name="bulut"`, `platform="cloud"` — accepted as is.
  The tests pin those two values as the module's "device kind".
- The alias `bulut` (what `app/execution/rule.py` and `narrative/collector.py` match) lives in
  `devices.metadata_json.aliases`, owner data; enroll does not set it. **api side must do it**
  (see "For the lead"). No change to `app/devices` was made or is needed for the worker to connect.
- Enrollment tokens are minted by an owner session AND a loopback peer. So the container cannot
  mint its own: the token is minted on the Cloud Core host and handed via a file/env once; the
  worker then persists `device_id` + private key in its state volume and never needs a token again.
  A start with neither a persisted identity nor a token file refuses (exit 2).

## Dependencies (owner-approved-list rules apply; the lead wires THIRD_PARTY)

| lib | licence | need | already in tree |
|---|---|---|---|
| `websockets>=13` | BSD-3 | WS client to broker | yes, services/api (locked 17.1) |
| `cryptography>=43` | Apache-2.0 OR BSD-3 | ECDSA P-256 keypair + signature | yes, services/api (locked 50.0.1) |

Both go in a NEW optional extra `cloud` of `services/browser/pyproject.toml` so the Windows
agent's venv (which must not grow) is untouched. Neither phones home. Playwright stays as is; the
image installs Chromium (`playwright install --with-deps chromium`), BSD/multi-licence, already in
THIRD_PARTY via Playwright. The worker does not need `--channel chrome` on Linux: `chromium`.

Rejected: aiohttp / httpx-ws (bigger, not in the browser package); running the .NET agent under
Linux (Session 0/companion model is Windows-only); a managed cloud browser (proposal alt. 3:
third party sees pages); embedding in the api process (a Chromium leak would take the API down).

## Safety review

- Device harm: none; touches no owner machine. No kernel driver, no credential access.
- Secrets: private key lives only in the worker's state volume (mode 600); the enrollment token
  file is deleted after use; nothing is baked into the image or compose.
- Phones home: only to the broker URL given (tailnet/loopback), plus whatever pages a task opens.
- Egress: the container is on the compose network with no published ports; SSRF guard is the
  worker's own `require_public_destination`. Deny-list (ADR-0207) is enforced Core-side and again
  by `task_denylist.py` in the worker.

## Footprint (estimate; the measurement is separate, see evidence file)

Idle Chromium 0.5–1 GB, 2 contexts up to ~2 GB (blog sources, medium quality). Ceiling: `mem_limit
2g`, `memswap_limit 2g`, `cpus 2`, `shm_size 2g` (shm counts inside the cgroup limit on Docker, so
2g shm under 2g mem is deliberate headroom, not extra). Concurrency cap 2 sessions. On CPX32 (8 GB)
next to ~2–3 GB stack + embedder ×2 blue-green this is the tight spot; the docker-stats run decides.
Docker was checked on this PC by the worker task, not here.

## Files to touch (worker task)

`services/browser/browser_agent/cloud/{__init__,config,policy,broker,__main__}.py`,
`services/browser/tests/unit/test_cloud_worker.py`, `infra/docker/cloud-browser/{Dockerfile,
compose.fragment.yml,measure-memory.ps1,measure-memory.sh}`,
`docs/evidence/adr-0213-cloud-worker-memory-2026-09-30.json`, `services/browser/pyproject.toml`
(extra `cloud` — outside the card's area: the worker must add ONLY this stanza and say so; if the
lead prefers, do it at merge). Tests without Docker/network: policy widened refused; kind/name/
capabilities equal the contract; no enrollment material → refuse; compose parsed as YAML has
mem_limit/shm_size/init; ack ordering and idempotent re-ack against a fake socket.
Mutations: default policy widened → RED; `mem_limit` removed → RED.

## Rollback

Delete `browser_agent/cloud` and `infra/docker/cloud-browser`; revoke the `bulut` device
(`POST revoke`); nothing else references them until task 5 wires the rule.

## THIRD_PARTY_COMPONENTS entry text (for the lead)

> **websockets / cryptography (cloud browser worker)** — Role: the Linux companion of the browser
> worker dials the Device Broker (`services/browser[cloud]`). Licences: BSD-3 / Apache-2.0 OR BSD-3.
> Already locked in services/api. Optional extra only; the Windows agent venv does not carry them.
> Chromium via Playwright in `infra/docker/cloud-browser`, 2 GB cap.

## Open, for the lead

Owner-only: minting the enrollment token needs an owner session + loopback on the Cloud Core.
