**Task `cloud-browser-worker`, cycle pilot-02, rework: the out-of-area edit is removed.**

**Working on:** `cloud-browser-worker`, area `services/browser/browser_agent/cloud` plus tests and infra, on the worker's worktree.

**sha:** `539841a9244ba80ba6020a7bd2e77c2f4924d5df`, pushed. The worktree is clean.

**Fix for the returned point:**
- I reverted `services/browser/pyproject.toml` to main's version (`2e3b1668`). Its diff against main is now empty.
- Without the `cloud` extra, the Dockerfile now installs the two dependencies itself: `pip install . "websockets>=13" "cryptography>=43"`.
- The diff against main is now 12 files, all inside the area. `git diff 2e3b1668 HEAD --stat` shows nothing outside it.

**Tests:** `test_cloud_worker.py` gives 50 passed (PROVEN_AUTOMATED). I ran it with the browser venv, the worktree on `PYTHONPATH`, and the api venv's site-packages for `cryptography` and `yaml`. Nothing in the module changed, so the earlier proofs still stand:
- The RED-first run failed at collection with `ModuleNotFoundError: browser_agent.cloud`, before any code existed.
- The two mutations showed RED and the files were restored by sha256, not by `git checkout`. The full 64-hex hashes were recorded in the previous report and are unchanged:
  - `CLOUD_SESSION_CLASSES` widened with `REVERSIBLE_WRITE` gave 5 failed. `policy.py` was `6f485a54c02770ee2a59379da4a89c96f48aef1b035034b66d22afd4bc19c94a` before and after.
  - `mem_limit` removed from the compose fragment gave 1 failed. `compose.fragment.yml` was `dbc94f75cdf8a419f0a57ca4196fcc4ad2043b2e85f87957fdecf13f931fbe38` before and after.

**NOT_RUN:**
- **Memory measurement:** the Docker engine is not running on this PC. `docs/evidence/adr-0213-cloud-worker-memory-2026-09-30.json` says `NOT_RUN` and carries the commands for the Cloud Core.
- **Not run here:** `SubprocessWorker`, the websocket connect loop, `enroll()` over REST, the Dockerfile build (including the new `pip install` line) and `docker compose`. They need a real browser, the api and Docker.

**For the lead at merge:**
1. Wire `infra/docker/cloud-browser/compose.fragment.yml` into the prod compose. Its build paths are relative to `infra/docker/cloud-browser/`, so adjust them. Add the `default` network and `depends_on: api`.
2. Set the alias `bulut` in `devices.metadata_json.aliases` after the first enrollment. Enroll does not set it.
3. Mint an enrollment token on the Cloud Core host and write it to the state volume as `enroll.token`. This is an owner-only step.
4. Add `pyyaml` to the browser `dev` group, or accept that the YAML tests skip in the browser venv.
5. Pin `PLAYWRIGHT_TAG` in the Dockerfile to the Playwright version in `uv.lock`. It defaults to `v1.49.0-noble`, which is unverified.
6. Add the THIRD_PARTY entry from the integration plan. Number the ADR text in `team/plans/cloud-browser-worker-adr.md` into `docs/DECISIONS.md`.
7. Run the measurement on the Cloud Core: `MEASURE_WINDOW_S=90 infra/docker/cloud-browser/measure-memory.sh cloud-browser <evidence.json>`. Drive `session_open`, three `navigate` calls and `session_close` inside that window.

**Open risks:**
- CPX32 memory headroom is unmeasured.
- Datacenter IPs may be blocked or hit CAPTCHAs.
- Only `measure-memory.sh` exists, not the `.ps1` the plan listed, because the Cloud Core host is Linux.
- The api has no `device_kind` field. The worker sends `name="bulut"` and `platform="cloud"`, which the api accepts, so `app/devices` needs no change.
