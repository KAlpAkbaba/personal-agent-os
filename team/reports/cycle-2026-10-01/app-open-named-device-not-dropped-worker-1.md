**Part (a) is done; part (b) is half-done and will not fire in production yet.** The tool side is in and tested, but the router's turn record doesn't carry the owner's words, so the new guard never triggers on a real call until one line is added to `service.py`.

**Şu an üzerinde çalışılan:** app-open-named-device-not-dropped, area as in the card, machine = worker worktree.
**sha:** a908d00c5bd2a3232896b64e1f11d0a1507ce27f, pushed, worktree clean. 4 files in the area plus the ADR text; counting all five, the changes are:
- `app/voice/intents.py`
- `app/voice/realtime_sessions/tools_operator.py`
- `tests/unit/test_voice_intents_polite_open.py`
- `tests/unit/test_operator_app_open_named_device.py`
- `team/plans/app-open-named-device-adr.md`

**(a) Polite open verbs**
- `açın`, `açınız`, `acın`, `acınız` and `aciniz` are now in `_OPEN_VERB_FORMS` and `_APP_OPEN_VERB_FORMS`, through one shared `_POLITE_OPEN_VERB_FORMS`.
- I left the screen, news, routine and bare-title tables alone. Nothing observed needs them, and each has its own false-positive history.
- RED before the change: 5 failed (four verb forms, plus "Ofis bilgisayarında hesap makinesini açın.").
- GREEN after: 7 passed. "Hesap makinesini açın." gives `app_open` calc, and "Neden açın?" and "Kapıyı açın." are not opens.
- The sentence gives `app_open` calc with alias `ofis`.
- Mutation: with the forms removed, 5 failed again. `intents.py` was restored from the backup copy and its sha256 matched before and after (`92637f0f…`).

**(b) The tool's guard**
- `operator_app_open` now checks a turn-record field I named `utterance_text`. If it holds a computer word or an "ofis…" word and `device_targets` is empty, nothing is dispatched. The tool answers one question built from the enrolled aliases: "Hangi bilgisayarda: ofis mi, iş mi, ev mi?" "Bu/şu bilgisayar" counts as this machine and is not guarded.
- A bound `ofis` target launches and the speech names the device ("Ofis cihazında Hesap Makinesi açtım efendim"). A sentence with no computer word behaves as before.
- RED before: the unbound-computer case dispatched `desktop.open_application`. GREEN after: 4 tests pass.
- Mutation: with the guard disabled, the unbound-computer test fails. `tools_operator.py` was restored from the backup copy and its sha256 matched before and after (`3cb22349…`).
- There is no device argument on the tool, as ADR-0224 requires.

**What I could not do:**
- **The turn record has no owner words.** `ctx["last_utterance"]` in `service.py` is deliberately word-free, and `service.py` is outside my area. Someone needs to add `"utterance_text": text[:300]` to that dict. Until then the real session behaves as it did before. The ADR text says this in an OPEN line.
- **Device named in the operator-route speech.** The operator path (devices that advertise `app.launch`) still doesn't say which device it launched on. Only the direct-launch path does. I didn't widen the task.
- **The real-world check (PROVEN_REAL).** This needs the owner to say the sentence from MAIL once the `service.py` line is in.

**Tests and evidence class**
- Lint and format: ruff check and format are clean on the touched files (`intents.py` needed one `ruff format`).
- Targeted run (voice intents, operator tools and fallback, spoken alias, window ref, plus the two new files): 261 passed.
- Voice corpus plus realtime sessions (`test_owner_utterance_corpus`, `test_voice_corpus_regressions`, `test_voice_realtime_sessions`): 2828 passed, 586 s.
- Not run: the rest of the unit suite and the integration suite (NOT_RUN).
- Evidence class for both parts: PROVEN_AUTOMATED.

**Risks:**
- "açın" is now an open verb everywhere `_OPEN_VERB_FORMS` is read. That includes the eye and camera, native and other generic open paths. The corpus stayed green, but "Neden hesap makinesini açın?" still resolves to `app_open`, the same as "açsana" does today. I didn't add a question filter.
- The two new test files are in the unit suite and depend on the main checkout's venv with `PYTHONPATH` set to the worktree.
