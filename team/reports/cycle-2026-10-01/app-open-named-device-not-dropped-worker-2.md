**Şu an üzerinde çalışılan:** app-open-named-device-not-dropped (return round), area voice relay and operator tools, machine: dev PC.

**Report: sha `2d1ba0214b83964e9f38f28ff2bd693a48ef58ee`** (pushed, worktree clean)

**Files changed in this round (4, all inside the area, with `service.py` widened as the inspector directed):**
- `app/voice/realtime_sessions/service.py`: writes `machine_named_unbound: bool` into `last_utterance`, next to `device_targets`.
- `app/voice/realtime_sessions/tools_operator.py`: a new pure `names_unbound_machine(text, bound)`. The tool guard now reads the flag from the turn record. It no longer reads `utterance_text`, and nothing writes that key.
- `tests/unit/test_operator_app_open_named_device.py`
- `team/plans/app-open-named-device-adr.md`

**What changed against the return list:**
1. **Word-free signal:** the owner's sentence is never stored. A relay test asserts `utterance_text` is absent from the record. The key name has no forbidden part such as "text".
2. **Relay test through the real session:** I ran the utterance → router → `handle_tool_call` → `operator.app_open` path with "Ofisü bilgisayarında hesap makinesini açın."
   - It was RED before the fix. Calculator launched on MAIL.
   - GREEN after: no dispatch, `needs_clarification`, one question.
3. **Branch pinning:**
   - "Mutfaktaki bilgisayarda …" pins the `bilgisayar` branch.
   - "Ofisü hesap makinesini açın." pins the `ofis` branch.
   - "evdeki bilgisayarda" binds `ev`, so it is not an unbound sentence. I used "mutfaktaki" instead.
   - `açın` alone is not pinnable. The router folds it to `acın`, so dropping `açın` alone changes nothing. The ADR says the mutation unit is the whole polite table.
4. **sha256:** the committed `intents.py` is `ed2b69c3d6634a335a1a4d03c77e959054723e5899a9a6480e5f70aac6ba4080`. The earlier `92637f0f…` was the pre-format hash. The other two are `service.py` `64d595ba84e1569b382cc75eddf010eabd5fee1ebb7ebc7a39016849601edaaf` and `tools_operator.py` `6dd479deb3fbbd2a038eadbc240b14166ce0b7b33da85f71f1a731db58562824`. `ruff format` only touched the test file, so the mutation hashes are valid.

**Tests:**
- New and updated: 11 tests in the named-device file (4 by-hand, 5 relay, 1 pure-function, plus the bound and no-word relay cases) and 7 in the polite-open file. All 17 pass.
- **Mutations**, restored from backup copies, with sha256 equal before and after:
  - Guard removed: 4 RED.
  - `service.py` writer returning False: 3 RED.
  - `bilgisayar` branch dropped: 2 RED. `ofis` branch dropped: 2 RED.
  - Polite table emptied: 5 RED.
- Checks run:
  - ruff check and format: clean.
  - The new files plus spoken-alias ×2, realtime sessions and open-application fallback: 164 passed.
  - `-k "realtime or operator_tool or local_mode or spoken or tools_operator"`: 433 passed.

**Evidence classes:**
- Polite open verbs: PROVEN_AUTOMATED.
- Unbound machine → one question and no dispatch, through the real session: PROVEN_AUTOMATED.
- Bound `ofis` → dispatch to GMKADIRAKBABA: PROVEN_AUTOMATED. The relay test uses the real broker port and checks the recorded `device_id`.
- Speech wording: the card wanted "Ofiste Hesap Makinesi açtım", but the existing helper says "Ofis cihazında …". I left it as is.
- Owner re-trial from MAIL: READY_FOR_OWNER, so PROVEN_REAL is pending.

**NOT_RUN:**
- The full 2828-test corpus. I did not re-run it after the `service.py` change. The subsets above are green.
- The full unit suite, integration tests and `quality-gate.ps1`.

**Open risks:**
- "Neden hesap makinesini açın?" still resolves to `app_open`, as disclosed before.
- A bare `ofis\w*` also flags a sentence like "ofis programı"; Office is not on the launch allowlist, so the risk is low.
- The lead numbers the ADR and moves it into `docs/DECISIONS.md` at merge.
