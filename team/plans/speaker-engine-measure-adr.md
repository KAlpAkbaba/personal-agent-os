# ADR (number from the lead): speaker-engine measurement - local speaker embedding + diarisation, MEASURED only

Card `speaker-engine-measure` (cycle d20261006). Plan: `team/plans/speaker-engine-integration-plan.md`.
Proposal: `team/proposals/2026-10-06-konusmaci-sesi-motoru-olcum.md`. Roadmap: order 3 "His conversations
and his people" (v1.0 item 665 speaker embedding DEFERRED; B05 owner verification stays in shadow).

## Decision

Measure, do not adopt. Three sherpa-onnx speaker-embedding ONNX models - CAM++ zh+en advanced (192-dim),
ERes2NetV2 zh-cn (192-dim), CAM++ VoxCeleb (512-dim, the plan's optional third row) - and the pyannote
segmentation-3.0 ONNX segmenter run in their own container `pagentos-speaker-measure` and produce
`docs/evidence/speaker-measure.{json,md}`: per model same-speaker / different-speaker cosine scores, EER with its
threshold, counts against `speaker.py`'s bands, embedding RTF p50/p95, peak RSS, load time; per (segmenter x
model x input) DER with a 0.25 s collar, speakers found, RTF. Nothing is wired: `app/voice/speaker.py`, the
conversation transcript store, every profile, Settings and the web are untouched; `services/api` gains no
dependency (pyproject.toml / uv.lock unchanged) and never imports `sherpa_onnx`.

Pieces: `tools/speaker-measure/` (Dockerfile on the FreyaTTS base digest, `requirements.txt` with the plan's three
`--hash` lines, `fetch_models.py`, `measure.py` - a stdin/stdout JSON-lines contract like
`tools/tts-measure/synthesize.py`), `scripts/voice/speaker-compare.ps1` (the tts-measure conventions),
`services/api/app/voice/speaker_measure.py` (pure: EER, DER, bands, RTF/percentiles, splice plan, evidence schema
v1.0, `render_tr`; imported by nothing in the application). The `-FromCore` download helpers moved from
`stt-compare.ps1` into `scripts/lib/VoiceMeasurementCore.ps1`, dot-sourced by both scripts; stt-compare's
parameters, messages and behaviour are unchanged (`test_stt_compare_from_core.py` green, not edited).

## Why these three models, and what is out

- CAM++ and ERes2NetV2 are the proposal's two candidates; both ship as ONNX in sherpa-onnx's
  `speaker-recongition-models` release, Apache-2.0 originals (plan section 1.1), CPU-only, no account. The
  VoxCeleb CAM++ is the same runtime and costs one row; it is the only English-only-trained one, a useful contrast.
- The segmenter: pyannote segmentation-3.0 as k2-fsa's ungated MIT ONNX export. The plan's STOP-1 (upstream is
  gated behind a contact form) is quoted on the evidence page; this card put the segmenter into the measurement,
  and any adoption card re-reads STOP-1 and STOP-CHECK-2 (research-only training data).
- OUT: `pyannote/speaker-diarization-community-1` (gated, HF token, PyTorch); NVIDIA Sortformer (NeMo/PyTorch, no
  sherpa-onnx support); a cloud STT provider's speaker labels (audio leaves the owner's machines; the owner's rule
  and KVKK).

## Pins and the offline run

- Image: `python:3.12-slim-bookworm@sha256:54c85f3c...` (the FreyaTTS base), `pip install --no-deps
  --require-hashes --only-binary=:all:` of numpy 2.5.3, sherpa-onnx 1.13.8, sherpa-onnx-core 1.13.8; tag
  `pagentos-speaker-measure:<12 hex of the four tool files>`; a build-time `measure.py selfcheck`.
- Models: `measure.MODEL_FILES` holds the plan's table (path, URL, bytes, sha256); a test asserts each hash and URL
  appears in the plan. The fill step (the ONLY networked run) writes `<name>.part`, checks size + sha256, renames;
  a mismatch deletes the part and exits 3. `measure` re-hashes every file it will load before the first
  constructor: a mismatch prints one error line and exits 3, nothing measured, and the script stops with no
  evidence.
- Every measurement container: `--network none --read-only --tmpfs /tmp --user 10001 --memory 4g --memory-swap 4g`,
  models `:ro`, recordings `:ro`; one container per embedding model (own load time, own peak RSS). Shapes
  `ev-pc` (all cores) and `cpx32-bicimi` (`-Cpus 4 -Threads 4`, labelled VEKIL/PROXY);
  `cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)` on every page.
- Host allow-list (`MAIL`, `pagentos-core`) before any docker call: **the employer's office PC never runs it.**

## The identical cosine and what the band counts mean for B05

`measure.cosine_similarity` is `app/voice/speaker.py` `cosine_similarity`'s arithmetic line for line (the image
has no app code; a test compares the two bodies and the values). The bands are read from `SpeakerThresholds` at
call time, never retyped (a test monkeypatches the class's default and sees the count move). Semantics follow
`verify_speaker`: OWNER needs `score >= owner_accept` (0.75), NOT_OWNER needs `score <= not_owner_max` (0.45). So
"same-speaker below 0.75" = owner utterances B05 would NOT accept as OWNER with that model; "different-speaker
above 0.45" = guest utterances B05 would NOT reject; the uncertain share is what would land in the UNCERTAIN band.
Pairwise scores (sentence vs sentence), not enrolment-mean vs probe: a conservative proxy for B05's
profile-vs-probe score. EER is the threshold-sweep crossing (smallest |FAR - FRR|, ties to the lower threshold),
not the minimum error. Against ONE guest it is a weak number, and the page says so.

## The spliced conversation as a proxy

DER needs ground truth. The tool builds an "eklenti (doğal değil)" conversation from owner.wav (or the owner's
sentences, which carry their own leading/trailing silence - this inflates "missed" and is a known bias) and
guest.wav: alternating turns of 2-8 s with 0.3-1.0 s silences from a fixed seed, no overlap; the reference is exact
by construction. A natural conversation.wav gets DER only with a hand-made reference.json; otherwise speakers found,
the timeline and "DER: referans yok, ölçülmedi". Rows: `num_clusters=2` (main) and the threshold row (`-1`, 0.5;
upstream issue #1466). sherpa-onnx's pipeline differs from pyannote's own (#1708): these are not pyannote's
published numbers.

## KVKK / TCK 133 as implemented

- A voice print is biometric (KVKK md. 6). Inputs: the owner's own `/voice/measure` recordings and ONE recording of
  ONE consenting person. A folder holding any other file name is refused before docker (exit 2).
- `consent.json {guest_consent: true, consent_date}` is required for anything of the guest: without it guest.wav,
  conversation.wav and reference.json are never opened, copied or mounted (a test reads what the container's `/in`
  mount held) and the rows say "rızasız: ölçülmedi".
- No name: the label is `konuk`; the script takes no name argument; files are staged as `sahip-NN`, `sahip-uzun`,
  `konuk`, `konusma`.
- Vectors live only in the container process's memory, are never printed (a test greps stdout) and are cleared at
  the end; nothing is written to any profile (no API code, no DB, no network in the image). The evidence schema
  (versioned) has no key naming a vector, embedding, audio or transcript (a test walks the keys).
- The staged recordings and every temp folder are removed at the end, success or not (retrying a locked file). The
  only audio kept is the two listening files under `%LOCALAPPDATA%/PagentOS/speaker-measure/<label>/`, never in the
  repository (`render_tr` refuses a listening folder inside it); the owner deletes them after listening.

## Open follow-ups (each a separate owner decision)

1. The real-voice run: `speaker-compare.ps1 -FromCore -Folder <dir>` with the owner's twenty sentences and one
   consenting person (READY_FOR_OWNER); PROVEN_REAL only from his report.
2. The real CPX32 run (a remote action, handed by the lead).
3. An adoption card: one model as a local service outside the API image, `OwnerProfile.model_id` = file + sha256,
   possibly re-tuned bands from the Turkish distribution; re-read STOP-1 / STOP-CHECK-2; THIRD_PARTY line (plan
   section 8, the lead copies it).
4. The wiring mic -> segmenter -> STT -> conversation store (speaker names in the transcript).
