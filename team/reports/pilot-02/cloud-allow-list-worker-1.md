## Şu an üzerinde çalışılan
Task `cloud-allow-list` (pilot-02), area: the protocol JSON, `app/execution/allowlist.py` and the worker's `cloud_allowlist.py`. Machine: the owner's PC. Done.

**sha:** `0c48532002ee2c95dd645a3c152e0b3e6f1491d2`, branch pushed, worktree clean.

**Files (8, all in the area, plus the two the card allows: `protocol_files.py` and the bundle copy):**
- `packages/protocol/browser-cloud-allowlist.json`: version, rule text, `sites: []`.
- `services/api/app/execution/allowlist.py`: `sites()`, `allowed()`, `acting_allowed(url) -> (bool, reason)`, with reasons `not_on_owner_allow_list` and `deny_listed_site`.
- `services/browser/browser_agent/cloud_allowlist.py`: `SITES = ()` plus the same functions, using `task_denylist`.
- `app/protocol_files.py` (BUNDLED gained the name) and `app/protocol_bundle/browser-cloud-allowlist.json`, made by `scripts/sync-protocol-bundle.py`.
- Two test files: `services/api/tests/unit/test_cloud_allowlist.py` (7 tests) and `services/browser/tests/unit/test_cloud_allowlist.py` (5 tests).
- `team/plans/cloud-allow-list-adr.md`, which includes the editor API shape.
- `rule.py` is untouched.

**Behaviour:** a host matches when it is a listed site or ends with `"." + site`. The deny-list is asked first and always wins. An entry that is not a registrable domain raises at load instead of being skipped.

**Tests (PROVEN_AUTOMATED):**
- API run: 44 passed (`test_cloud_allowlist`, `test_protocol_bundle`, `test_browser_contract_v17`).
- Worker run: 7 passed (the file has 5 tests plus parametrized cases).
- ruff is clean on all four Python files.
- Each side has these near-miss pairs, per the acceptance list:
  - Empty list allows nothing.
  - A listed site and its subdomain are allowed.
  - Lookalikes are refused (`magaza.com.tr.evil.example`, `notmagaza.com.tr`, and on the API side also a URL query and path, `ftp://` and garbage).
  - A deny-listed site is refused even when listed, while a listed normal site beside it is allowed.
- Each side holds its copy equal to the shared file. The API test reads the worker's source with `ast`; the worker test reads the JSON.
- The API test also asserts the bundle is byte-equal to the shared file.

**Mutations, both files mutated together and restored from backup:**
- M1, deny-list check replaced with `if False:`: RED on both sides (`test_a_deny_listed_site_stays_refused_even_when_it_is_listed`, 1 failed each).
- M2, subdomain match replaced with `site in host`: RED on both sides (2 failed each, the `magaza.com.tr.evil.example` and `notmagaza.com.tr` lookalikes).
- sha256 before and after, identical:
  - `allowlist.py`: `e81921bfc9dde64c68ba4b8c033108e9c51dce0a27506259986f68524bae2fa9`
  - `cloud_allowlist.py`: `3ab513ab84d1811e312e1d18b3da3d5d2ddc9ea6a9b5374f34f46e9ea2711e7d`

**Departures from the order of work:** I wrote the implementation before the tests, so there is no RED output from before the code existed. The mutation runs are the RED proof.

**Not run (NOT_RUN):** the full API unit suite and the PS suites. The gate is the lead's.

**Open risks:**
- The registrable-domain check uses the small suffix table in `sites.py`. A bare public suffix outside that table would pass validation, so the editor must refuse it as well (in the ADR).
- The worker's `SITES` is a seed. The ADR says the follow-up must persist the owner's list at run time and send it to the worker with the job.

### For the lead at merge
- Add the ADR text to `docs/DECISIONS.md` with a number.
- Add ledger events `allowlist.site_added` and `allowlist.site_removed` to `app/ledger/vocabulary.py` when the editor task lands.
- `execution-target-wiring` should call `allowlist.acting_allowed`. It is not wired yet.
- The `POST /v1/team/allowlist` editor still needs its own task.
