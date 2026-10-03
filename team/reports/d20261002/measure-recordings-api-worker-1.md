## Şu an üzerinde çalışılan
`measure-recordings-api` (cycle d20261002) · alan: `services/api/app/voice/measurement/*`, `app/main.py`, üç test dosyası, `team/plans/measure-recordings-api-adr.md` · makine: ev PC, worktree `worker-measure-recordings-api`. Durum: kod bitti ve push edildi; **tam birim paketi yeşil DEĞİL** — alanım dışındaki iki sabitlenmiş beklenti lead'in birer satırını bekliyor.

**sha:** `5843e31ea27997e60ad6af1564882e241e53872e` (pushed, tree clean). 8 files, all inside the area, +1616.

**Cannot be finished inside the area (lead action needed):** the daily purge is registered as the sweep `measurement_recordings` on the existing `RetentionSweeper`, and that turns two pinned tests red.
- `tests/unit/test_health_endpoint.py::test_health_ok_shape`: add `"measurement_recordings"` before `"audit_retention"` in the pinned sweeps list.
- `tests/unit/test_maintenance.py::test_the_application_sweeps_memory_sessions_and_assets`: stub `Recordings.purge` to return 0 and add `"measurement_recordings": 0` to the expected dict.
- A separate lifespan loop was my first build; `test_bounded_delivery` (a loop `/health` cannot see) refused it, and that route also needs two out-of-area edits.

**Deviations from the card**
- The first purge runs on the sweeper's first pass (300 s after start by default), not at second zero; then every 24 h, with a failed purge retried on the next hourly pass.
- The service is a class, `Recordings(store, root=...)`, with the seven named methods rather than module functions, so the MinIO test can work under its own root.

**Evidence (PROVEN_AUTOMATED)**
- Red first: 3 collection errors, `ModuleNotFoundError: No module named 'app.voice.measurement'`.
- New unit tests: 78 passed (`test_measurement_recordings.py`, `test_measurement_routes.py` through `create_app`).
- MinIO on the dev stack (`S3ObjectStore.from_settings`): 1 passed. It runs under a per-run root `voice-measurement-it-<uuid>` (not the real `voice-measurement` prefix) so a test run cannot delete dev recordings; it removes its own keys.
- Integration suite: 154 passed, 11 xfailed.
- Full unit suite on the committed sha: **3 failed, 14 777 passed, 5 skipped, 1 xfailed**.
  - Two failures are the pinned tests above.
  - The third is `test_qualification_evidence::test_every_proof_marked_row_points_at_something_that_exists` (doc row 41.7). It failed the same way in the earlier full run with the first wiring; I did not run it on the base commit, and no doc is in this diff.
- `test_postgres_coverage_ratchet.py` passes untouched. `ruff check` and `ruff format --check` are clean on the touched files.

**Mutation RED** (`service.py` restored from a backup copy each time; sha256 `a57fae79…8a2a` before and after):
1. Sample-rate check removed → `test_each_refusal…[wav_wrong_sample_rate-4]` and the matching route test fail.
2. Expiry filter removed → `test_list_items_does_not_return_an_expired_item_when_purge_did_not_run` fails.
3. `delete_all` walking `ev` only → `test_delete_all_with_recordings_in_both_places…` fails (plus two more).

**Statements the card asks for**
- No migration, no new dependency, no new Settings field; the release is automatic under ADR-0214 addendum 9.
- The owner's approval of the idea is his permission for twenty scripted sentences of his voice to rest 30 days on the Cloud Core.
- The spoken sentences "Ölçüm kaydını başlat" / "ölçüm kayıtlarını sil" are deferred until `intents.py` is free.
- Nothing is READY_FOR_OWNER: no recording exists until `measure-recording-page`.

**Open risks**
- `wav_info` does not return the format tag, so "PCM" is enforced only as 16-bit / mono / 16 kHz.
- In a blue-green overlap, a purge landing between the audio and sidecar writes of another process drops that one reading; the owner reads it again (recorded in the ADR).
- `GET /v1/voice/measurement` makes about 120 store calls (purge, then list): fine on a same-host store, slow on a remote one.
