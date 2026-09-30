## Şu an üzerinde çalışılan
Task `cloud-browser-worker` (pilot-02, ADR-0213 PR 2). Area: `browser_agent/cloud`, `infra/docker/cloud-browser`. Machine: dev PC. I did not touch HANDOFF.

**sha:** `98821a8a9211ae7d70e0702b6ee93362d19c59be` (pushed, tree clean). 13 files, all inside the area except `services/browser/pyproject.toml`.

**Out-of-area edit (flagged):** I added only the `[project.optional-dependencies] cloud = [websockets>=13, cryptography>=43]` stanza to `services/browser/pyproject.toml`, as the plan allows. Revert it if you would rather add it yourself at merge.

**Tests:** 50 tests in `test_cloud_worker.py`, each named as a sentence, with a near miss beside each rule.
- **RED first:** collection failed with `ModuleNotFoundError: browser_agent.cloud`, before any code existed.
- **GREEN:** 50 passed. The full browser unit suite gives 991 passed, 7 skipped, and ruff is clean.
- **Skips:** the 7 skips are the crypto and YAML tests. They need `cryptography` and `yaml`, which the browser venv lacks. I ran the 50 with the api venv's site-packages on `PYTHONPATH`, importing the worktree's `browser_agent`.
- **Two-sided checks:** the enroll body's field set equals the api's `EnrollRequest` fields, read from the api source. The protocol version and the broker error-class list are checked the same way. A signature verifies under the broker's rule (`nonce || device_id`).

**Mutations (each shown RED, then restored):**
| Mutation | Result | sha256 before = after |
|---|---|---|
| `CLOUD_SESSION_CLASSES` widened with `REVERSIBLE_WRITE` | 5 failed | `policy.py` `6f485a54c02770ee2a59379da4a89c96f48aef1b035034b66d22afd4bc19c94a` |
| `mem_limit: 2g` removed from compose | 1 failed | `compose.fragment.yml` `dbc94f75cdf8a419f0a57ca4196fcc4ad2043b2e85f87957fdecf13f931fbe38` |

Both files were restored from a backup copy, not with `git checkout`.

**Evidence classes:**
- **PROVEN_AUTOMATED:** the module. It covers the policy clamp, the entry args (headless, chromium, dedicated profile, no `--trusted-origin`, no private destinations), the enroll and hello contract, the config refusal (exit 2), the ack order, idempotency, expiry, cancel, heartbeat, the healthcheck, and the compose YAML (`mem_limit`, `memswap_limit`, `shm_size`, `init`, `cpus`, no ports, no secrets).
- **NOT_RUN:** the memory measurement. The Docker engine is not running on this PC, so nothing was built or measured. `docs/evidence/adr-0213-cloud-worker-memory-2026-09-30.json` says `NOT_RUN` and carries the exact commands for the Cloud Core.
- **NOT_RUN:** `SubprocessWorker`, the websocket connect loop, `enroll()` over REST, the Dockerfile build and the `docker compose` file were not run. They need a real browser, the api and Docker, so I have no evidence for them.

**Contract findings:**
- There is no `device_kind` field. The worker sends `name="bulut"` and `platform="cloud"`, which the api accepts, so `app/devices` needs no change.
- The worker's `session_open` honours a wider requested policy. That is why the clamp lives in the companion: it refuses wider classes and any profile other than `research` before the worker sees them.

**For the lead at merge:**
1. Wire `infra/docker/cloud-browser/compose.fragment.yml` into the prod compose. Its build paths are relative to `infra/docker/cloud-browser/`, so adjust them. Also add the `default` network and `depends_on: api`.
2. Set the alias `bulut` on the device row after first enrollment. It lives in `devices.metadata_json.aliases`, and enroll does not set it.
3. Mint an enrollment token on the Cloud Core host (owner session plus loopback). Write it to the state volume as `enroll.token`. This is an owner-only step.
4. Add `pyyaml` to the browser `dev` group, or accept the YAML tests skipping.
5. Pin `PLAYWRIGHT_TAG` in the Dockerfile to the Playwright version in `uv.lock`. It currently defaults to `v1.49.0-noble`, which is unverified.
6. Add the THIRD_PARTY entry text from the integration plan, and number the ADR text in `team/plans/cloud-browser-worker-adr.md` into `docs/DECISIONS.md`.
7. Run the measurement on the Cloud Core: `MEASURE_WINDOW_S=90 infra/docker/cloud-browser/measure-memory.sh cloud-browser <evidence.json>`. Drive `session_open`, three `navigate` calls and `session_close` inside that window.

**Open risks:**
- CPX32 memory headroom is unmeasured.
- Datacenter IPs may be blocked or hit CAPTCHAs.
- The plan listed `measure-memory.ps1`; I shipped only the `.sh`, since the Cloud Core host is Linux.
