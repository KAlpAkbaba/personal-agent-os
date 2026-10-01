Inspector report: app-open-named-device-not-dropped

**Pass 1 — run it**
- The two task test files give 17 passed (named-device 10, polite-open 7). The worker's report says 11 and 7, so its named-device count is off by one; the total of 17 matches.
- The wider subset `-k "voice or realtime or intent or operator or alias or device"` over `tests/unit` gives 3284 passed, 0 failed, in 241 s. It imports the worktree code (`app.__file__` checked).
- `ruff check` is clean on `app` and the two new test files.
- My own mutation was different from the worker's: `_names_an_unbound_machine` made to return `False` regardless of the turn record. The mutation produced 4 RED, including the `Mutfaktaki…` and `Ofisü…` pins. I restored from a backup copy. The sha256 before and after is `6dd479de…2824`, equal to the worker's committed hash. The git status after restore was clean.
- Probes on the router, in the worktree:
  - `Ofis bilgisayarında hesap makinesini açın.` resolves to `app_open`.
  - `Hesap makinesini açın.` resolves to `app_open`.
  - `açınız` resolves to `app_open`.
  - `Neden açın?` resolves to `none`.
  - `Kamera açık mı?` resolves to `explain`, so the adjective is not turned into an action.
- Not run: the full unit corpus, integration tests, `quality-gate.ps1`, and the dev stack (no real Temporal or device). The mock-free relay test is the nearest real run available, and it counts as PROVEN_AUTOMATED.

**Pass 2 — break it**
- **Privacy:** the relay records only `machine_named_unbound` (bool), with no sentence text and no forbidden key part. A test asserts the text is absent.
- **Contract halves:** the writer is in `service.py` and the reader is in `tools_operator.py`. The relay tests drive both through the real session, so they cannot drift apart silently. Mutating the writer gave RED per the worker, and my reader mutation also gave RED.
- **Safe default:** an unbound named machine launches nothing and asks one question. The alias lookup is wrapped, so a DB failure still yields a question. Revoked devices are excluded.
- **Weak points (all low, all disclosed or known):**
  1. `Neden hesap makinesini açın?` still resolves to `app_open`. The card says "açın inside a question does not become an open". Only the bare `Neden açın?` is covered.
  2. This is not a regression: the existing `Neden hesap makinesini aç?` and `açar mısın` forms already resolve to `app_open`. The polite forms inherit the router's current behaviour, and the unbound-machine guard is the only safeguard in front of it.
  3. `\bofis\w*` and `\bbilgisayar\w*` over-trigger. `Bilgisayarımı kapat` and `Ofis programını aç` return True. The flag is read only by `operator_app_open`, so the cost is one needless question on a rare sentence; it never causes a wrong launch.
  4. The card's speech example was "Ofiste Hesap Makinesi açtım", but the existing helper says "Ofis cihazında …". The worker kept the helper. This is a wording deviation, not a functional one.
- **Area:** the diff is inside the card's area plus the team files that were already on main at branch time. I did not see a stray source file.
- **Contract drift and secrets:** none found in the diff. There are no new paths or secrets.
- **Rollback:** a plain revert; there is no migration and no schema change. The `machine_named_unbound` key in the turn record is additive.

**Evidence classes**
- Polite open verbs: PROVEN_AUTOMATED.
- Unbound named machine gives one question and no dispatch through the real session: PROVEN_AUTOMATED.
- Bound `ofis` dispatches to the office device through the broker port: PROVEN_AUTOMATED.
- Owner re-trial from MAIL, with Calculator opening on GMKADIRAKBABA: READY_FOR_OWNER.
- Full gate and the full corpus: NOT_RUN.

APPROVE
