## Inspector report: `measure-recordings-api` @ `5843e31ea27997e60ad6af1564882e241e53872e`

The code holds up and the proof is real, but the unit suite is red on this branch. Two pinned tests outside the card's area fail because of the new purge, and the worker cannot edit them.

**Pass 1: what I ran (from a clean tree)**
- **New and affected unit tests:** I ran six files: the two new ones, `test_health_endpoint`, `test_maintenance`, `test_postgres_coverage_ratchet` and `test_qualification_evidence`. Result: **102 passed, 3 failed**. These are the same three failures the worker reported:
  - `test_health_endpoint::test_health_ok_shape`: the `measurement_recordings` sweep is not in the pinned list. Caused by this diff.
  - `test_maintenance::test_the_application_sweeps_memory_sessions_and_assets`: the expected sweeps dict lacks the new key. Caused by this diff.
  - `test_qualification_evidence` (doc row 41.7): already broken before this task. The diff touches no file under `docs/`, and the test reads only docs.
- **Coverage ratchet:** `test_postgres_coverage_ratchet` passes untouched. No table and no migration.
- **MinIO on the dev stack, through `S3ObjectStore.from_settings`:** **1 passed** (PROVEN_AUTOMATED, real infrastructure).
  - The shared dev database is at `0065_misheard_utterances`, from another branch. On it, the integration setup errors with `Can't locate revision`.
  - So I ran on a fresh scratch database (`pagentos_insp_measure`, then dropped). The MinIO is the real one either way.
- **Full integration suite, same scratch database:** **154 passed, 11 xfailed** in 227 s. Same counts as the worker.
- **Not run by me:** the full unit suite, about 14 800 tests. I ran only the touched and affected files. The worker reports 3 failed / 14 777 passed on this sha.
- **My own mutations to `service.py`** (backed up to %TEMP%, restored by copy). sha256 `a57fae7910f106f7fa29f0395c3da8ff43a41e1692d671b6756b304145488a2a` before and after. All five went RED:
  1. Expiry test changed from `>` to `>=`: `test_an_item_is_listed_on_day_30_and_purged_on_day_31` fails.
  2. Purge deletes only the audio and leaves the sidecar: the same day-31 test fails.
  3. Capture limit raised to 21 keys: `test_each_refusal…[capture_too_many_keys-13]` fails.
  4. `ready_transcripts` always written: `test_ready_transcripts_is_present_only_for_a_non_null_browser_transcript` fails.
  5. Transcript added to the save log: `test_no_log_record_of_any_path_contains_the_transcript_or_the_base64` fails.
- **Area:** 8 files, all inside it, tree clean. The ADR covers the storage layout, KVKK, ADR-0171, no migration, and the deferred spoken sentences.

**Pass 2: trying to break it**
- **Wiring choice:** a separate lifespan loop would trip `test_bounded_delivery::test_every_background_loop_the_app_starts_can_be_seen_in_health`. That test reads the lifespan source, so it would also need an edit outside the area. Either way, the card's area could not hold the "purge in the server process" requirement. That is a card defect, not the worker's.
- **Sweeper:** `RetentionSweeper` runs each sweep in `asyncio.to_thread`, and one failing sweep does not stop the rest. A `StoreUnavailable` from the purge is contained.
- **Deviation from the card:** the first purge runs at the sweeper's first pass, about 300 s after start, not at second zero. An expired recording still cannot be served in that window, because every read filters on expiry and `GET /v1/voice/measurement` purges first. I accept it.
- **Validation and refusals:** every check runs before the lock and before any write, so a refusal writes nothing. The body is bounded by Content-Length and by a streamed byte count before parsing.
- **Store faults** become a 503 that logs only the exception's type name. No transcript or base64 is logged anywhere.
- **Minor, not blocking:**
  - `wav_info` does not return the format tag. A WAV without a `fmt ` chunk is accepted at the default 16 kHz/mono/16-bit.
  - A 16-bit non-PCM file is not refused (the worker named this).
  - `GET /v1/voice/measurement` makes about 160 store calls (purge, then list). Fine on the same host.
  - In a blue-green overlap, a purge from the other process can drop a half-written pair. The ADR records this.
- **Security and privacy:** no secrets or paths in the code. Everything is behind the owner session (the six 401s are tested). No employer-machine surface.

**Evidence classes:** red-first, unit, MinIO, integration and the mutations are PROVEN_AUTOMATED. Nothing is READY_FOR_OWNER.

RETURN (1. widen the area to `services/api/tests/unit/test_health_endpoint.py` and `services/api/tests/unit/test_maintenance.py`, and apply the two edits the worker spelled out: `"measurement_recordings"` before `"audit_retention"` in the pinned sweeps list; `Recordings.purge` stubbed to 0 and `"measurement_recordings": 0` in the expected dict; 2. re-run the full unit suite: the only failure left may be doc row 41.7, which is already broken before this task and must be shown to fail the same way on base `b2797727`; with that, this is APPROVE with no other change)
