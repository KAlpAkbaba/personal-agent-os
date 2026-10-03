# ADR (unnumbered): stt-compare reads the Cloud Core's recordings; Chrome's live sentence is a row

Status: accepted (measure-compare-from-core, cycle d20261003). The lead numbers it.

## Context

ADR-0242 built the instrument (`app.voice.stt_compare`, `scripts/voice/stt-compare.ps1`) and
measure-recordings-api put the owner's twenty readings on the Cloud Core
(`/v1/voice/measurement`). The engine the owner uses every day in local mode (Chrome's
recogniser) could not be measured: it takes a live microphone, not a file, so its row was
`NOT_RUN: no file input`. The recording page now keeps what Chrome wrote WHILE the owner read
(`browser_transcript`), and the Core's manifest hands it over as `ready_transcripts`.

## Decision

1. **The `recorded_live` row.** An engine with no provider whose label appears in at least one
   usable recording's `ready_transcripts` is scored from those sentences with the same
   `score_pair` and `intent_changed` as every other row. Every row gains `source`: `file` (this
   run sent the file) or `recorded_live`. Latency is null: nothing was timed. A recording that
   carries no sentence for it is that file's error `no ready transcript`, never a perfect and
   never an empty hearing. A label no configured engine has gets its own row after theirs, so
   nothing recorded is dropped silently. An engine left out with `-Engines` stays "not
   selected" even when a sentence exists (the selection is the caller's).
2. **Not in `audio_sent_to`.** That list is what THIS run sent where; it sent the
   `recorded_live` row nothing. The report says who heard that sound instead, in
   `heard_live_by` (Chrome: Google may have received it at recording time), and the summary
   says so in one line per row, plus one line that the recordings are the sound the system
   hears (browser noise suppression on), not raw microphone audio.
3. **null against `''`.** `browser_transcript: null` = Chrome's recogniser did not run, so the
   manifest has no `ready_transcripts` for that item = an error row entry. `''` = it ran and
   wrote nothing = a hearing in which every reference word is an edit. A `ready_transcripts`
   that is not an object of non-empty label -> string (a list, a null or a number value) is a
   `ManifestError`; so is a `browser_engine` that is not text.
4. **Schema 1.1.** `source` on every row, `heard_live_by`, `from_browser`, and the items'
   `browser_engine`. A manifest without the new fields gives the statuses, reasons and
   numbers it gave under 1.0 (held as literals in `test_stt_compare_ready.py`).
5. **Personal data stays out of the repository.** `-FromCore` downloads into a fresh
   `pagentos-stt-compare-<guid>` folder under the user's temp directory (refused when that
   directory is inside the repository), checks every file's sha256 against the Core's list
   and stops before any engine on a mismatch, and removes the folder in a `finally` with
   retries; a folder that cannot be removed is reported (exit 5), not swallowed.
6. **The token.** The caller's own `PAGENTOS_OWNER_SESSION_TOKEN` wins and is left alone;
   otherwise `Get-OwnerSessionToken` mints one from the DPAPI-stored credential. It lives in
   one header hashtable cleared after the downloads: never printed, never on disk, never a
   child's argument. The Core URL is `-CoreUrl`, else the BrokerRestUrl the installed agent
   dials (as `verify-core-device-row.ps1` reads it) - no hard-coded host.
7. **The report enters the repository.** `docs/evidence/stt-compare-<date>.json` holds the
   transcripts of the twenty SCRIPTED sentences only (what each engine and Chrome wrote for
   them); never free speech. The audio never enters it.

## Consequences

- The first real numbers need only the owner's readings; Chrome's row costs nothing extra.
- A nightly run is a separate card (`scripts/team/register-nightly.ps1` is not this area).
