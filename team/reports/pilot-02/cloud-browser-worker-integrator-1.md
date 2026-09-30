**Integration report: cloud-browser-worker (ADR-0213 PR 2).** The plan is at `team/plans/cloud-browser-worker-integration.md`. I wrote only that file. I added no dependency and no feature code. I did not run anything, measure anything or mutate anything.

**Choice: ADAPT.** No ready-made library speaks our device protocol, so nothing can be adopted. Only the container recipe exists in the world: `--init`, `--shm-size`, a memory cap. I take that recipe and build the bridge myself.
- **Reused unchanged:** the worker (`browser_agent.worker`), headless `ManagedBackend` and `RESEARCH_SESSION_CLASSES` (READ+NAVIGATE). The worker starts without `--allow-private-destinations` and without `--trusted-origin`.
- **Why a bridge is needed:** the worker only speaks stdio to a companion, and on Windows that companion is the C# agent. On Linux nothing plays that role.
- **What the worker builds:** a small Python companion under `browser_agent/cloud/`. Its modules are:
  - config, which refuses to start without enrollment material;
  - policy, which refuses anything wider than READ+NAVIGATE;
  - broker, covering enroll over REST, then the WS handshake (hello, challenge, ECDSA auth, heartbeat) and command acks with idempotency, expiry and cancel;
  - the entry point.

**Dependencies.** Two libraries are needed, both already locked in `services/api`. They would go into a new optional extra `cloud` in `services/browser/pyproject.toml`, so the Windows agent's venv does not grow. That pyproject stanza sits outside the card's area, so the worker will flag it and the lead may prefer to add it at merge.

| Library | Licence | Locked version | Used for |
|---|---|---|---|
| `websockets` | BSD-3 | 17.1 | WS client to the broker |
| `cryptography` | Apache-2.0 OR BSD-3 | 50.0.1 | ECDSA P-256 keypair and signature |

Neither phones home. Chromium comes via Playwright, which is already in THIRD_PARTY. I rejected aiohttp, running the .NET agent on Linux, a managed cloud browser, and embedding in the api process.

**Contract findings (read from the code).**
- **No `device_kind` field exists.** The enroll body is `{token, name, platform, public_key_spki_b64, capabilities}`, and `platform` is a free string of 1–64 characters. The worker sends `platform="cloud"` and `name="bulut"`, which the api accepts as is. The tests will pin those two values as the module's device kind.
- **The alias `bulut` is separate.** `execution/rule.py` and `narrative/collector.py` match it, and it lives in `devices.metadata_json.aliases` as owner data. Enroll does not set it, so someone must add it after enrollment.
- **Enrollment tokens can't be minted by the container.** They need an owner session and a loopback peer. The token is minted on the Cloud Core host and handed over once as a file. The worker then persists `device_id` and its private key in its state volume. With neither a persisted identity nor a token file it exits with code 2.

**Footprint (estimate only, not measured).**
- **Limits:** `mem_limit 2g`, `memswap_limit 2g`, `cpus 2`, `shm_size 2g`, `init: true`, at most 2 concurrent sessions. `shm` counts inside the cgroup limit, so the 2g is deliberate headroom rather than extra memory.
- **Where it is tight:** on CPX32 (8 GB), next to the existing stack of about 2–3 GB plus the embedder ×2 during a blue-green switch. The `docker stats` run on the Cloud Core decides.
- **Sources:** the blog sources are medium quality.
- **Evidence file:** the worker writes it as `NOT_RUN` unless Docker runs on this PC. It includes the exact command for the lead to run on the Cloud Core.

**Risks.**
- **Data-centre IP:** sites may block or CAPTCHA the cloud worker; this is unmeasured.
- **Chromium memory:** a leak is contained by the separate container and its memory cap, not by design.
- **Owner-only step:** minting the token cannot be automated.

**For the lead** (the api side is untouched; nothing in `app/devices` needs changing for the worker to connect).
1. **Alias:** set alias `bulut` on the cloud device row after first enrollment. I did not find the route that sets aliases (my grep of `devices/routes.py` was inconclusive), so check for an owner path, otherwise it is a DB or metadata update.
2. **Token:** mint one enrollment token on the Cloud Core host (owner session + loopback) and place it in the worker's token file.
3. **Compose:** wire `infra/docker/cloud-browser/compose.fragment.yml` into the production compose yourself, as the card says.
4. **THIRD_PARTY:** add the entry text from the plan.

**Worker instructions.** Follow the plan's "Files to touch" list. Tests need no Docker and no network; the list is in the plan. The two mutations are the default policy widened and `mem_limit` removed, each shown RED, with the file restored by its full 64-hex sha256. Evidence class is PROVEN_AUTOMATED for the module. The memory measurement stays NOT_RUN until it is run on the Cloud Core.
