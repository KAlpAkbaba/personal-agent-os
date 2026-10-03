**Şu an üzerinde çalışılan:** cloud-search-engines-probe (worker, cycle d20261003). Area: the probe module, the host script, the test file, 3 fixtures and the ADR draft. Machine: the dev PC, worktree `.claude/worktrees/team/d20261003/worker-cloud-search-engines-probe`.

**Result:** the probe tool is built, tested and pushed. The real measurement from the Cloud Core's address has not been run; that run is the lead's, after the release.

- **sha:** `ac87cf3d352a165767113d97bfc5a8047c5a6ce7`, pushed. The worktree is clean.
- **Files:** 7 new, all inside the area: `engine_probe.py`, `measure-search-engines.sh` (executable bit set), `test_engine_probe.py`, `duckduckgo-captcha.html`, `bing-wall.html`, `brave-wall.html`, `team/plans/cloud-search-engines-probe-adr.md`. Nothing outside the area changed.
- **Red first:** before the module existed, `test_engine_probe.py` failed at collection with `ImportError: cannot import name 'engine_probe'`. After implementing: **28 passed**. The 3 shell-script tests ran under Git Bash with the fake `docker`; none was skipped.
- **Mutation RED:** each mutation was restored from a backup copy, and every restore matched the original sha256 `e209b99b…bb64a`.

  | Mutation | Failed |
  |---|---|
  | Cap raised to 100 | 2 (both cap tests) |
  | Stop-after-wall removed | 2 (wall and no-retry driver tests) |
  | `interstitial` set to `'handoff'` | 2 (driver tests) |
  | Wall classified as `empty` | 8 (all wall fixtures, the error shape, the driver, the table) |

  After that, `ruff format` reflowed both new files (formatting only); every run below is on the formatted code.
- **Fast checks:** ruff check is clean and ruff format applied. `bash -n` on the script passes. Importing `engine_probe` loads no broker, config or websockets module. The broker classes are imported only inside `main`, and the driver gives them an empty broker URL.
- **Full unit suite (services/browser):** 1049 passed in 17 s. `test_search_engines.py` and `test_cloud_worker.py` are unedited and green.
- **Evidence:** PROVEN_AUTOMATED for the tool: red-first run, the counts above, the four mutation REDs.
- **Not run:**
  - The measurement from the Cloud Core's address: the lead runs it on the host after the release, read-only.
  - The PROVEN_PROXY run in a locally built image from the home address: the inspector's choice. If run, it proves only that the tool works end to end, not anything about the datacentre address.
  - mypy: not installed in the browser venv, so not run.

**Fixtures:** all three are hand-reduced, not recordings; no wall was provoked to make them.
- `duckduckgo-captcha.html` uses the wording `page_kind.py` records as seen for real on 2026-09-03.
- `bing-wall.html` uses "verify you are human" and is classified as captcha.
- `brave-wall.html` uses "automated queries" and is classified as blocked.

**Decisions made inside the area:**
- **Image name:** the script uses `pagentos/cloud-browser:local`, the name in the production compose. The fragment names a different one, `pagentos-cloud-browser:local`.
- **Memory check:** the "2 GiB available" refusal reads `/proc/meminfo` (path overridable with `PAGENTOS_PROBE_MEMINFO`), not a fake `docker` answer. It exits 4 before any docker call.
- **Container:** a 512m tmpfs at `/tmp` is the data dir, and the limits include `--memory-swap 2g`, `--cpus 2` and `--init` from the fragment.
- **Failures stop an engine:** an error that has no attempts (for example `timeout`) gives the row outcome `error` and stops that engine, like a wall.
- **Broken worker link:** if the link to the worker raises, the run aborts. `session_close` and the child's stop still run, and `main` prints one line and exits 1.

**Open risks:**
1. Whether Bing's and Brave's real wall pages contain a marker the worker's classifier knows is not verified. A real wall without one shows up as `empty`. The summary line flags `empty` as suspect, and so does the ADR.
2. The queries go out without locale or region. The research gateway sends a region for Turkish queries, so the measured pages may differ slightly from production research. The ADR does not mention this yet; the lead should add it at merge.
3. The memory floor relies on `MemAvailable` in `/proc/meminfo`, which exists on Linux kernels from 3.14 on.
4. Running the module inside the real image is unproven until the image is rebuilt (ADR-0248 release-order step 2, which needs the owner's approval). The script exits 3 with one line until then.
