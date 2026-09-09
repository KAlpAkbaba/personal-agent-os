# M26 Executive Autonomy — milestone report (2026-09-09)

Owner directive: master directive "CLOSE M18.4 AND COMPLETE M19 -> M28" (M26 section), plus the three additive directives the owner sent during the milestone — **morning briefing + latest news mode**, **owner location context for weather**, and (from the alarm track) the wake-media correction. Decision records: ADR-0089 (+ addenda 1, 2), ADR-0091 (+ addendum 1), ADR-0092 (+ addendum 1). Specs: `docs/M26_EXECUTIVE_AUTONOMY_SPEC.md`, `docs/M26_LATEST_NEWS_MODE_SPEC.md`. QUALIFICATION Stage 24 rows 24.1–24.17. Released to production: `e6f08ff`.

**The one line that matters:** a multi-step job is now a validated graph the owner can ask about, pause, correct and stop at any moment; it ends completed, partly done with what is missing NAMED, or stopped; and no step can send, pay, delete or publish, because no such step kind exists — read back from the deployed code on production as **15 step kinds, `high_risk_kinds` [], bound 2 active runs**.

---

## What was proven ON PRODUCTION

Released blue/green on 2026-09-09: `e6f08ff` behind the edge, `52d5ddb` kept as last known good, `ui_state` contract **11** served where it served 10 before, idle colour drained 60 s and stopped.

**One executive run, completed end to end** — `ec20975f-a3ee-4bf5-9636-c2696d8fa0c7`, shape (a), **4/4 steps `verified`**:

| step | kind | evidence |
|---|---|---|
| s1 | `research.run` | research task `affac381`, report artifact `d6bd27ee`, **2 sources, 2 findings** (attempt 2) |
| s2 | `research.synthesize` | *"Kısa araştırma bütçesinde yalnızca 2 kaynak doğrulanabildi; bulgular sınırlı."* |
| s3 | `artifacts.create` | document artifact **`dd283204-9208-4463-bb63-458ede89e090`**, read back through `/v1/artifacts/{id}` as state **READY** |
| s4 | `synthesis` | *"document hazır (artifact dd283204-…)"* |

The owner-facing receipt, composed from the row rather than from what the model remembered saying: **"Tamamlandı efendim. document hazır (artifact dd283204-9208-4463-bb63-458ede89e090)."** Note s2's own sentence: the synthesis names its limit (two sources) instead of dressing it up. That is the product's rule working where it is easiest to break.

**The additive tracks, driven against the real services inside the production container** — they have no REST surface by design (ADR-0091 decision 9), so citing a route would have been citing something that does not exist:

- **Live weather**: `executed`/`verified`, provider `open_meteo`, observed 2026-09-09T04:15Z, 20.5 °C, `location_source` `explicit_owner_request` — *"İstanbul: genelde açık, 20°C. Bugün en yüksek 26°C, en düşük 20°C. Yağış olasılığı yüzde 0."* — with a durable `weather_query_evidence` row, so "hangi konumun havasını söyledin?" is answered from the record.
- **Location**: `resolved: false`, `reason: unresolved`, no default row. With no device fix, no default and no place named, it says it does not know where the owner is. It does not guess.
- **Morning briefing**: *"İyi geceler efendim"* at 04:16 local (not a false "Günaydın"), the deployed SHA, real uptime, and **13 real overnight autonomous-development events — 13 completed, 0 failed** — counted from ledger rows, never asserted. The absent news section says so honestly.
- **News**: 0 sources configured, so "Show Ana Haber" is bound to nothing (owner item 36).

Evidence: `docs/evidence/m26-runtime-2026-09-09.json`.

---

## IMPLEMENTED

- **Cloud Core executive** (`services/api/app/executive/`): `plan.py`/`graph.py` (the closed vocabulary, the DAG validated in both directions including through `amend`, the bounds, `high_risk` defined and mapped to nothing with its absence asserted positively); `planner.py` (the directive's three shapes from the ONE router's slots, with the model seam validated by the same rules, never around them); `workflow.py` (topological order, fan-out ≤ 3, the workflow holding no state); `activities.py` (one idempotent activity per step keyed by `(run, step, attempt)`, evidence read back before anything is called `verified`); `service.py`, `routes.py`, migration `0033_executive_runs`; `tools_executive.py` and the `EXEC_*` intents through the ONE router; UI contract v11 `executive.run`.
- **Owner Location Context / Live Weather / Morning Briefing** (`app/location`, `app/weather`, `app/briefing`, migration `0034_location_weather_briefing`, ADR-0091): the six-source provenance model and the one resolution order; Open-Meteo as a genuinely keyless real default; the briefing assembled from live registries and ledger evidence with per-section durable preferences; seven intents and seven tools through the ONE router; the `weather` corpus category (107 cases).
- **Latest News Mode** (`app/news`, migration `0035_news_mode`, ADR-0092): durable sources, channel identity resolved only from an authoritative signal, "latest" from real `published_at`, the three content policies, governed playback on the browser worker's **third** persistent Chrome profile, and a summary mode that delegates to the existing M13 research pipeline rather than duplicating it; three intents and their tools; the `news` corpus category (64 cases).
- **Web**: contract v11 (additive, append-only) and the Cockpit "Görevler" panel with the owner's three controls and an honest partial state, both halves held to each other by tests that read the *other* side's source.

## TESTED

`services/api` **7312 passed / 2 skipped**; `services/browser` **405 passed**; `apps/web` **74 files / 1397 tests** with `tsc --noEmit` clean and `oxlint` 0 errors; `ruff check .` clean in both Python services. Voice corpus **1636 cases across 19 categories** — `executive` 120 multi-turn (with `preceding_turns`, so "Devam et" is tested after "Bu işi durdur" after a start), `weather` 107, `news` 64 — every earlier category unchanged, forbidden side effects 0.

`test_health_endpoint.py` is excluded from the API figure: it makes real db/redis/object-store/temporal connects and hangs in this sandbox. That is an environmental fault recorded in agent memory since 2026-09-02, and it is named here rather than quietly dropped.

## SECURITY — three independent reviews, three real HIGHs, ten findings

Every finding was closed with a regression test, and **every one of those tests was watched to fail against the old code first**.

1. **Executive Autonomy (ADR-0089 addendum 2).** *HIGH*: an exception `run_step_activity` could not classify escaped the activity, `asyncio.gather` let it terminate the whole Temporal execution, and because the workflow holds no state the row stayed `running` for ever — a false **still working**, which is the same dishonesty as a false `completed`, inverted and arguably worse. Trigger: a research summary past `ArtifactSpec`'s 20 000-character bound. *MEDIUM*: the two-run bound was a count-then-insert race, proven with two threads. *LOW*: the restart-mid-run proof was narrower than decision 5 claimed — narrowed rather than defended. And one assertion of mine the review refuted outright.
2. **Location / Weather / Briefing (ADR-0091 addendum 1).** *HIGH*: an unguarded evidence commit whose failure leaves the session needing a rollback, so the realtime dispatcher's own commit then fails the **whole tool-call turn** — the same bug this branch had already fixed once in `record_receipt`, reintroduced one frame away, which is the argument for fixing a class rather than a site. *MEDIUM*: "the owner's words win" was implemented for thirteen cities and fell through to the model's own argument for every other place name — a durable state mutation an injected instruction could aim at. *MEDIUM*: `location_context` was insert-only, i.e. the location archive the product forbids, dormant only until a device can write. *LOW*: a 200 that is not JSON escaped every typed handler.
3. **Latest News Mode (ADR-0092 addendum 1).** *HIGH*: the Shorts exclusion was **structurally inert on the only provider that ships** — YouTube's Atom feed carries no duration and no flag, and the provider builds every URL as `/watch?v=`, so `is_short` could only ever fire on an uploader voluntarily typing "#shorts". An untagged Short posted after the day's bulletin was *selected* for `latest_full_broadcast` and flagged `ambiguous`, which nothing downstream reads. All 288 resolver tests passed while this was live, because every fixture supplied evidence the real provider cannot produce. Plus four MEDIUMs (a substring `youtube.com` host check that is also an SSRF primitive; untrusted titles into verbatim TTS; the `news` profile not media-only despite the contract; raw `ElementTree` with no size cap) and a LOW.

## BUGS FOUND AND FIXED, beyond the reviews

1. **My own first fix for the HIGH broke the retry loop.** The new `except Exception` went in between `except StepError: error = exc` and the retry decision that followed it, leaving that branch with no `break` — every classified step failure re-dispatched the step in an unbounded hot loop, against a real service. It was found by a test suite **hanging**, which is the worst way to find anything. The retry decision now lives after the try/except and serves both branches, and the regression asserts the attempt *count* through `asyncio.wait_for`, so the same shape fails in ten seconds instead of stalling.
2. **The third route to "still working", found by running it on production.** The first real run ended with all four steps stopped and told the owner *"Çalışıyorum efendim: 3/4 adım tamam."* `_derive_run_outcome` gated on "not terminal", and `failed_recoverable` satisfies that — but a step nothing will retry unless the owner asks has *stopped*. It belonged to neither set and the code had only one. `STEP_IN_FLIGHT_STATES` now decides, and a run whose every step has stopped is `partial` and names what is missing.
3. **A step killed at 90 s by its own liveness bound.** `research.run` died with *"activity Heartbeat timeout"*: the activity heartbeated only between retry attempts, so a single dispatch taking minutes looked stuck from the first second. Its `timeout_s` of 900 was never reached. A step now beats every 20 s while its dispatch is in flight. Worth recording separately: the crashed-step settler the security review added **caught this on its first real encounter** and the run reached `partial` naming all three failures instead of hanging. The second wall worked; it should not have had to.
4. **A connection pool per call, which exhausted production's database.** *"FATAL: sorry, too many clients already."* `build_artifact_context` called `create_engine` every time, and an Engine owns a pool nothing disposed — 27 call sites, more than twenty in the executive activity layer, plus `get_device_action`'s own. The comment directly above that code read *"a worker-lifetime shared engine is a later performance pass, not a correctness requirement."* Production disagreed. One engine per database URL now; that comment says why.

The four production runs, in order, because the sequence is the evidence: run 1 failed at the research pipeline's own ranking bound (*"0 contract-valid item(s), 3 required"*) and then lied about still working; run 2 died at the heartbeat bound; run 3 exhausted the database; run 4, with all three fixes live, **completed 4/4 with a real document artifact**. I am not claiming one root cause for all three — run 1's ranking failure may or may not have been downstream of connection pressure, and I did not prove it either way. What is proven is that each fix was necessary and that the run after the last of them succeeded.
5. **An "offline" test suite that started talking to the internet.** Three provider tests patched `httpx.get`; the size-cap fix changed the call to `httpx.stream`; the patches silently stopped intercepting and those tests began fetching the real YouTube feed — one returned fifteen genuine NASA uploads where it expected two fixtures. Only an unrelated count assertion noticed. The fetch now lives behind one named `fetch_feed_bytes` seam in our own module.
6. **Merge collisions resolved rather than papered over.** Two parallel tracks each claimed ADR-0090 (main had taken it for the device identity chain), owner item 34 (already `SET_OWNER_DEFAULT_WAKE_MUSIC_URL`), and migration 0034. Resolved to ADR-0091/0092, items 35/36, and migration `0035_news_mode` chained behind `0034_location_weather_briefing`, with the chain verified to have a single tip. The news branch had also named itself "M27", which collides with Creative Tools; it is the **M26 addendum** the owner's directive actually described.

## THE PATTERN, stated plainly

Three reviews, one runtime verification, four HIGH-class defects, and **not one of them was being caught by any suite**. Every one is the same shape: two self-consistent halves that had never been driven against each other. A fixture that supplies evidence the real provider cannot. A monkeypatch that stops matching and falls through to the network. A test that "catches" a bug by hanging. A comment that predicts a defect and dismisses it.

The transferable rules, now in agent memory: build a regression's inputs through the **real** parser; patch a named seam in your **own** module; and run it on production before you claim it works — three of this milestone's defects existed only there, and the fourth run is the one that produced a document.

## OWNER ACTIONS (nothing below blocks anything already built)

| item | what | time |
|---|---|---|
| **34** | `SET_OWNER_DEFAULT_WAKE_MUSIC_URL` — your real wake song, so a plain alarm never falls back to a tone | 30 s |
| **35** | `SET_DEFAULT_WEATHER_LOCATION` — say *"Varsayılan hava durumu konumumu &lt;şehir&gt; yap."* Until then a bare "Hava nasıl?" honestly says it does not know where you are | 10 s |
| **36** | The exact YouTube channel URL for "Show Ana Haber". This track will not guess a channel from a display name — a similarly-named one is a wrong answer that would look right for months | 1 min |
| 28 | The elevated agent update (UAC) — the documents/projects/scene families stay unreachable from a graph until it lands | 2 min |
| 29 | OpenAI credits — real narration audio stays `PROVEN_PROXY` without them | — |
| 30 | A mail account and calendar on the host — executive shapes (b) and (c) stay unit-proven until then | — |
| 32 | Unity Hub sign-in | 2 min |
| 33 | A JDK (or Android Studio) — needed by M28, not before | 10 min |

## NOT PROVEN, stated rather than hidden

- Reattachment to a workflow that is **still in flight** (ADR-0089 addendum 2 point 3). Idempotent replay at the activity layer is proven directly; mid-run reattachment is not.
- Executive shapes **(b)** (folder compare → spreadsheet) and **(c)** (mail thread → draft) on production: they need the documents family (owner item 28) and a mail account (item 30). Unit- and corpus-proven only.
- Latest News Mode against a **real channel**: no source has a resolved identity, by design, until owner item 36.
- Real narration audio (owner item 29). The receipts are proven; the voice rendering them is the loopback proxy.
- The `ClaudeExecutivePlanner` seam stays inert, per the spec's own instruction.
