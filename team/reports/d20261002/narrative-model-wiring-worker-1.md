## Şu an üzerinde çalışılan
- **Görev:** `narrative-model-wiring` (roadmap 2c, ADR-0244 B) — `activity.explain` oturumun sohbet sağlayıcısını anlatıya verir; ayar arkasında, varsayılan KAPALI.
- **Alan:** `services/api/app/voice/realtime_sessions/tools.py`, `services/api/app/config.py`, `services/api/tests/unit/test_narrative_model_wiring.py`, `team/plans/narrative-model-wiring-adr.md`
- **Makine:** sahibin geliştirme PC'si, worktree `.claude/worktrees/team/d20261002/worker-narrative-model-wiring`
- **Durum:** bitti, commit edildi ve itildi; worktree temiz. `docs/HANDOFF.md`'ye dokunulmadı (lead yazar).

## Report
**sha:** `f4da5aeb9381da2a1154fb4f92c591ec8e50d7ef` on `team/d20261002/worker-narrative-model-wiring` (pushed, origin matches). 4 files changed, all inside the area.

**What was built**
1. `Settings.narrative_model_enabled` (`PAGENTOS_NARRATIVE_MODEL_ENABLED`), default `False`; nothing else touched in `config.py`.
2. `activity_explain` reads the switch from the session's settings (`ctx.live["settings"]`, never `get_settings()`). Off, or no settings on the session: the `explain_to_briefing` call has no `chat_provider`, no provider is built and nothing is sent. On: `narrative_chat_provider(ctx.live)` is handed over.
3. The `voice.explained` ledger detail of a narrative answer gains `narrator` (`model` / `rule`) and `narrator_reason`. Reasons: `setting_off`, `no_provider`, `not_asked`, `timeout`, `provider_error`, the provider's own error class, `audit_rejected`. The provider's text is never written. Other explain kinds get no such keys.
4. ADR text is in `team/plans/narrative-model-wiring-adr.md`: one synchronous call per ask, nominal worst case 41.5 s (20 + 1.5 + 20), and ledger summaries leave for the provider.

**Tests — PROVEN_AUTOMATED**
- 15 new cases through the real app's tool-call route. RED before the change: 14 failed, 1 passed (the one asserting absence). GREEN after: 15 passed.
- Off: fake key in the environment and in the session settings, transport spy and build spy both empty, call kwargs hold no `chat_provider`.
- On: a draft that drops failures is repaired (`model`); a draft that invents a number is replaced by the rule text (`rule`, `audit_rejected`); a provider raising `TimeoutError` or `httpx.ReadTimeout` gives `rule`, `timeout`.
- Existing suites: narrative / explain / office01 / assistant-chat / config and related, 424 passed; all 38 `test_voice_*.py`, 852 passed. `ruff check` and `ruff format --check` are clean.

**Mutation RED** (backup copy restored, sha256 identical before and after each; no `git checkout --`)
- `tools.py` (`d3c6965f…`): setting ignored → 4 failed; fallback reason not recorded → 10 failed; provider not handed on → 7 failed; "model" recorded unconditionally → 1 failed.
- `config.py` (`3e5a5154…`): default flipped to `True` → 2 failed.

**Could not do / open risks**
- **Timeout over the real provider is not recorded as `timeout`.** `AnthropicChatProvider.answer` swallows every `httpx.HTTPError` and returns `chat_unavailable`, so in production the note reads `rule`, `chat_unavailable`. The acceptance's `reason=timeout` holds only for a provider that raises. Pinned by a test; the fix belongs in `app/assistant_chat.py`, outside the area.
- **The 41.5 s is not a hard deadline.** httpx applies the 20 s per phase, so a trickling response can run longer on the tool thread. A wall-clock ceiling would also be `assistant_chat.py`'s.
- **No compose line.** `PAGENTOS_NARRATIVE_MODEL_ENABLED` is not passed to the api container, so production cannot switch it on yet; that line goes with the owner's decision.
- **Detail key name.** The card says `reason=`; the key is `narrator_reason`, to keep it unambiguous on the row.
- **The model draft must name every completed area, including `ses`.** Session creation writes its own completed ledger row, and a draft that omits it is rejected (`audit_rejected`). Measured in the tests; expect it with the real model.
- Router gap (ADR-0244 A) is unchanged: "ne başarısız oldu" with Turkish letters still does not reach the narrative.
- NOT_RUN: the real Haiku model, the full unit suite, the integration suite, and the gate.
- READY_FOR_OWNER (batched): the decision to switch the setting on, plus the compose line.
