# ADR (unnumbered) - Measurement recordings: twenty scripted sentences, 30 days, on the owner's own object store

Context: ADR-0242 delivered the STT measuring instrument and left no recording to measure ("how they are recorded
without the owner becoming an operator is an open follow-up"). Proposal `team/proposals/2026-10-02-olcum-kaydi.md`,
approved by the owner on 2026-10-02; this is its storage half. The page (`measure-recording-page`) and the
download-and-measure side (`measure-compare-from-core`) are separate cards against the same contract.

Decision:
- Package `app/voice/measurement`: `service.py` (pure of FastAPI) and `routes.py` (owner session). Sentences are
  `app.voice.stt_compare.OWNER_SENTENCES`, index 1..20, never retyped; place is `ev` or `ofis`; a recording is
  `(place, index)`, file name `<place>-<NN>.wav`. Audio: WAV, PCM 16-bit, mono, 16 000 Hz, 0 < length <= 30 s,
  <= 1 048 576 bytes, validated with `app.voice.providers.wav_info` / `wav_duration_ms`.
- No table. The existing `ObjectStore` (`artifacts.store`, one bucket) holds `voice-measurement/<place>/<NN>.wav`
  and its sidecar `voice-measurement/<place>/<NN>.json`. The store has no list call, so the layout is ENUMERABLE:
  2 places x 20 sentences is every key that can exist. There is no shared index object to read-modify-write, and
  "delete everything" walks all forty pairs, so an orphan cannot survive. A table would add a migration and a
  second source of truth beside the bytes for forty slots that never grow.
- Write order audio then sidecar; a sidecar write that fails removes both (best effort) and answers 503 - an empty
  slot the owner reads again is better than an audio described by another reading's metadata. A re-save
  (`tekrar`) replaces both and restarts the 30 days. One lock per process guards save / delete / purge.
- Retention: `expires_at = recorded_at + 30 days`; a recording is expired when `now > expires_at`. Three triggers:
  (1) every read filters on the expiry, so an expired recording is never listed, served or put in the manifest even
  when no purge has run; (2) `GET /v1/voice/measurement` purges before it lists; (3) `DailyPurge`, registered as the
  sweep `measurement_recordings` on the API's existing `RetentionSweeper` (started and cancelled by the lifespan,
  named in `/health` under `retention` with its last count or error): it purges on the sweeper's first pass after
  start (the sweeper's own `retention_sweep_initial_delay_s`, so a booting process does no housekeeping) and then
  once every 24 h; a purge that failed is retried on the next hourly pass. No second loop was added: the guard
  `test_every_background_loop_the_app_starts_can_be_seen_in_health` refuses a loop `/health` cannot see, and the
  sweeper is the loop that is already seen. The 30 days are held by the server process on the host, never by a session
  (TEAM_PROTOCOL 9). Purge also removes half-written pairs (no sidecar, unreadable sidecar, sidecar without audio).
- API: `GET /v1/voice/measurement`, `PUT|DELETE /v1/voice/measurement/recordings/{place}/{index}`,
  `GET .../{index}/audio` (audio/wav), `DELETE /v1/voice/measurement/recordings`, `GET /v1/voice/measurement/manifest
  [?place=]` - exactly what `stt_compare.load_manifest` reads, plus `ready_transcripts: {"chrome-web-speech": ...}`
  only when the browser transcript is not null (null = Chrome's recogniser did not run, "" = it ran and wrote
  nothing) and `browser_engine`. The PUT body is JSON with base64 (no multipart parser is added), read under a byte
  bound and refused (413 `audio_too_large`) before it is parsed or decoded. Every refusal is
  `{detail: {code, message}}` with a Turkish message; a store fault is 503 `store_unavailable`, never a 500.
- Logs carry place, index, bytes and sha256 only - never the transcript, the base64, the capture settings or audio.

KVKK: a voice recording is personal data. Only the twenty scripted sentences are accepted (the index names the
sentence; free speech has no slot). They rest on the owner's own Cloud Core object store (Hetzner NBG1), 30 days,
and are deleted at once on his word (one or all). They are never used for speaker verification (ADR-0171
unchanged). The recording is what the browser delivered with its noise suppression on - "the sound the system
hears", not raw audio; the capture settings stored beside it say which. The owner's approval of the idea is his
permission for these recordings to rest 30 days on the Cloud Core.

Consequences: the release carries NO migration and no new dependency or setting (automatic under ADR-0214
addendum 9). Accepted risk: during a blue-green overlap two processes may purge while one saves; a purge landing
between the two writes removes the new audio and the owner reads that sentence again. Lead at merge: the new sweep name must be added to two pinned expectations outside this card's area
(`tests/unit/test_health_endpoint.py` sweeps list; `tests/unit/test_maintenance.py` expected results). Open
follow-up: the spoken sentences "Ölçüm kaydını başlat" / "ölçüm kayıtlarını sil" are NOT built here - `app/voice/intents.py` is in three
other cards' areas; they wait until it is free.
