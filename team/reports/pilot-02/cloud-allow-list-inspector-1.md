## Inspector report: cloud-allow-list (return fix), sha `32a7e985`

**Pass 1: I ran it**
- **Tests:** API side 43 passed (`test_cloud_allowlist`, `test_protocol_bundle`, `test_browser_contract_v17`). `app.__file__` points into this worktree. ruff is clean.
- **Worker side:** 7 passed with the browser service's own venv. `browser_agent.__file__` is also in this worktree.
- **Area:** the branch differs from `main` in exactly 6 files, all inside the card's area. `protocol_files.py` and the bundle copy are untouched, as the last return demanded.
- **My mutation 1 (dot dropped, `endswith(site)`):** both sides RED. The failing test is `test_a_lookalike_of_a_listed_site_is_not_allowed[https://notmagaza.com.tr/]`. This differs from the worker's substring mutation.
- **My mutation 2 (worker deny-list precedence disabled):** RED on `test_a_deny_listed_site_stays_refused_even_when_it_is_listed`. The worker did API-side precedence only, so this covers the other side.
- **Restore:** both files were restored from backup copies, not `git checkout`. Full sha256 matched the values before the mutations:
  - `allowlist.py`: `d13710e19d5be5aad7fb47f592d259474c01217ba6f1fa8954e6cbbf7b86e2c5`
  - `cloud_allowlist.py`: `3ab513ab84d1811e312e1d18b3da3d5d2ddc9ea6a9b5374f34f46e9ea2711e7d`
- **Tree:** clean afterwards.

**Pass 2: adversarial**
- **Lookalikes:** the tests cover `evil.example` with the site as a suffix, path or query, a `notmagaza` prefix, a non-http scheme and garbage input. Each has a matching hit beside it.
- **Copies held equal:** the API test parses the worker source with `ast` and compares it to the shared JSON. The worker test compares its own `SITES` to the JSON. Both fail on a missing file instead of skipping. This is the cross-read pattern the card asked for.
- **Deny-list:** it is asked first on both sides. The deny-list is the existing one (`sites.denied` / `task_denylist.denied`), so there is no second copy to drift.
- **Import safety:** `sites()` loads lazily, so the module imports even before the lead wires `BUNDLED`. The unwired state raises `KeyError` on the first call, not at import. The report states this and the lead is told to wire before use.
- **Secrets, paths, KVKK:** none in the code or in the JSON. The module writes nothing to logs or audit.
- **Cost on CPX32:** one small JSON read, cached.
- **Rollback:** revert the six files. Nothing else consumes them yet.
- **Contract drift:** `rule.py` is untouched, and BROWSER_CAPABILITIES and DEVICE_PROTOCOL are untouched.

**Findings that do not block**
1. **Bundle equality is not shown on this branch.** The card's acceptance says the bundle equals the shared file. `test_protocol_bundle` cannot cover the new file until the lead adds it to `BUNDLED`. I mark this `READY_FOR_OWNER`-style for the lead's merge step: run `test_protocol_bundle` after wiring. The worker reported a temporary wiring run (12 passed). I did not repeat it.
2. **The real bundled read is unproven here.** Every API rule test monkeypatches `sites()`. Only `test_the_shipped_list_is_empty` reads the real JSON, through `ALLOWLIST_PATH`, not through the bundle path.
3. **The worker's `SITES` is a seed.** The ADR says the editor must push the owner's list with the job. Until then a cloud job's list is fixed at deploy time.
4. **A bare public suffix outside `sites.py`'s table would pass validation.** The ADR records this and requires the editor to refuse it.

**Evidence classes**
- Empty list allows nothing, subdomain match, lookalike refused, deny-list precedence, and the two copies held equal: `PROVEN_AUTOMATED`.
- Bundle equality and the real bundled read through `protocol_file`: `NOT_RUN` on this branch, pending the lead's wiring.
- Full API unit suite, PS suites and the gate: `NOT_RUN` (the lead's).

**For the lead at merge** (the worker's list holds, with one addition)
1. Add `"browser-cloud-allowlist.json"` to `BUNDLED`.
2. Run `scripts/sync-protocol-bundle.py`.
3. Run `test_protocol_bundle` and `test_cloud_allowlist` afterwards. Also call `allowlist.sites()` once on the wired tree.
4. Put the ADR text into `docs/DECISIONS.md` with a number.
5. Add the ledger events in `vocabulary.py` when the editor lands.

APPROVE
