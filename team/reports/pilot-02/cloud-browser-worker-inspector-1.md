**Inspector report — `cloud-browser-worker`, cycle pilot-02, rework (sha `539841a9`)**

**Pass 1: I ran it**
- **Diff:** `git diff main...HEAD` is 12 files, all inside the area. `git diff 2e3b1668 HEAD` shows nothing outside it. `pyproject.toml` is untouched, so the earlier RETURN point is fixed.
- **Cloud-worker tests:** 50 passed when the YAML tests can import `yaml`. I ran them with the browser venv and the api venv's site-packages on `PYTHONPATH`. In the plain browser venv it is 43 passed and 7 skipped, because `yaml` is missing. That matches the worker's "pyyaml to the dev group" note.
- **Browser unit suite:** 991 passed, 7 skipped, in the browser venv. Nothing else broke.
- **ruff:** `ruff check` on the `cloud` package and its test file: clean.
- **My own mutations:** four different from the worker's. Each was restored from a backup copy and the sha256 matched before and after.

| Mutation | Result | sha256, before and after |
|---|---|---|
| `--headless` dropped from the worker args (`__main__.py`) | 1 failed | `5db7997e215fdecc745326d61baefcd881a07d1daba1c61182d6de2b8526e888` |
| enrollment-material check disabled (`config.py`) | 4 failed | `7f10ce92098b400c03a93fdd5239b53c6e397af2c2efee4cf4995670d0756fa1` |
| `init: true` removed (`compose.fragment.yml`) | 1 failed | `dbc94f75cdf8a419f0a57ca4196fcc4ad2043b2e85f87957fdecf13f931fbe38` |
| the `owner` profile added to `ALLOWED_PROFILES` (`policy.py`) | 1 failed | `6f485a54c02770ee2a59379da4a89c96f48aef1b035034b66d22afd4bc19c94a` |

- **Working tree:** clean after the mutations, per `git status`.
- **Docker:** not run. The engine is down on this PC, so the image build, the `pip install` line and `docker compose` are untested.

**Pass 2: adversarial**
- **Policy clamp:** every incoming command goes through `clamp_command` (`broker.py:250`). The default is READ+NAVIGATE, and a wider `allowed_risk_classes` or a non-`research` profile is refused with `SECURITY_SCOPE_ERROR`.
- **Worker arguments:** `--trusted-origin` and `--allow-private-destinations` are absent. There is no owner profile.
- **Secrets and logs:** the code has no hard-coded secrets or host paths. The enroll token file is deleted after use. The device key is written mode 600 per the code comment (the write itself I did not inspect). I found no payload logging in `broker.py`.
- **Container:** `cap_drop: ALL`, `no-new-privileges`, `pids_limit`, a non-root user, `mem_limit` and `memswap_limit` at 2g, and `cpus` 2 are all in the definition.
- **Contract:** the api has no `device_kind` field. The worker sends `name="bulut"` and `platform="cloud"`, which the api accepts, so `app/devices` needs no edit. The lead must know this: the spec's "device_kind=cloud" is carried as `platform`.
- **Memory evidence:** honestly `NOT_RUN`, with the commands for the Cloud Core.
- **Unverified for the lead:**
  - `PLAYWRIGHT_TAG` is unpinned.
  - The alias `bulut` is not set by enrollment.
  - CPX32 headroom is unmeasured.
  - The connect loop and `enroll()` over a real api are untested here.

**Evidence classes**
- **Cloud-worker module:** PROVEN_AUTOMATED.
- **Container definition:** PROVEN_AUTOMATED for its fields only. Whether it builds and runs is NOT_RUN.
- **Memory measurement:** NOT_RUN.
- **Enrollment and connect over a real api, and the alias:** READY_FOR_OWNER. The token step is owner-only.

**For the lead at merge:** the worker's seven items stand as written. Also run the measurement on the Cloud Core: `MEASURE_WINDOW_S=90 infra/docker/cloud-browser/measure-memory.sh cloud-browser <evidence.json>`.

APPROVE
