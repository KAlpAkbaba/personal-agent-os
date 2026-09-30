## Report: cloud-allow-list (return fix)

**Şu an üzerinde çalışılan:** task `cloud-allow-list`, area = the six card files, machine = this worktree.

**sha:** `32a7e9856fb0d72cf120ec00f2e7488b05a37fdc` (branch pushed, tree clean)

**What changed for the return.** The two files flagged as out of area are back to `main`'s bytes:
- `services/api/app/protocol_files.py`
- `services/api/app/protocol_bundle/browser-cloud-allowlist.json`

The card text said `protocol_files.py` was inside my area, but its area list did not include it. I followed the area list. The branch now differs from `main` in exactly six files, all in the area.

**Consequence.** `app.protocol_files` reads only the bundle, so the Cloud Core cannot read the list until the lead wires it.
- **`allowlist.py`:** it resolves the bundled file on first use, not at import. Before, an unwired name raised `KeyError` at import and would have crashed every importer. `ALLOWLIST_PATH` is now `None`, meaning "use the bundle".
- **Test change:** `test_the_shipped_list_is_empty` replaces the old bundle-equality test. It reads the shared JSON. Bundle equality is covered by `test_protocol_bundle` once the name is in `BUNDLED`.

**Tests (PROVEN_AUTOMATED):**
- API: 43 passed (`test_cloud_allowlist`, `test_protocol_bundle`, `test_browser_contract_v17`), with `app.__file__` in this worktree. ruff is clean.
- Worker: 7 passed. I used the browser service's own venv. The API venv has no playwright and failed at import there.
- Wiring check: I temporarily added the name and the bundle copy, then ran `test_protocol_bundle` (12 passed). `sites()` gave `()` and `acting_allowed("https://x.com/")` gave `(False, 'not_on_owner_allow_list')`. I then restored both files, and `protocol_files.py` has the same sha256 as before: `7aa34181064a7dfd899e2847afe2e29dac3e1ed2fb97518fe9aace686274c48e`.

**Mutation rerun, API side after the change:**
- **M1:** deny-list check replaced with `if False:`. RED: `test_a_deny_listed_site_stays_refused_even_when_it_is_listed` failed. `allowlist.py` restored from backup. sha256 before and after are identical: `d13710e19d5be5aad7fb47f592d259474c01217ba6f1fa8954e6cbbf7b86e2c5`.
- **M2 (substring match) not rerun.** The match line is unchanged from the earlier round, where it was RED on both sides (2 failed each). That is the earlier run's evidence, not new. The worker file is untouched, sha256 `3ab513ab84d1811e312e1d18b3da3d5d2ddc9ea6a9b5374f34f46e9ea2711e7d`.

**NOT_RUN:** the full API unit suite, the PS suites, and the gate. Those are the lead's.

**Open risks:**
- A bare public suffix outside `sites.py`'s small suffix table would pass validation. The editor must refuse it too (this is in the ADR).
- The worker's `SITES` is a seed. The follow-up must send the owner's list with the job.

### For the lead at merge
1. In `app/protocol_files.py`, add `"browser-cloud-allowlist.json"` to `BUNDLED` (after `alarm-timing.json`).
2. Run `scripts/sync-protocol-bundle.py`. It creates `app/protocol_bundle/browser-cloud-allowlist.json`, and `test_protocol_bundle` then holds it equal to the shared file. Do this before anything imports `allowlist.sites()`, or it raises `KeyError`.
3. Put `team/plans/cloud-allow-list-adr.md` into `docs/DECISIONS.md` with a number.
4. Add ledger events `allowlist.site_added` and `allowlist.site_removed` to `app/ledger/vocabulary.py` when the editor lands.
5. `execution-target-wiring` calls `allowlist.acting_allowed(url)`. `app/execution/rule.py` is untouched.
6. The `POST /v1/team/allowlist` editor needs its own task. Its API shape is in the ADR.
