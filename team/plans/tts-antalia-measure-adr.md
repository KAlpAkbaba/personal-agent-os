# ADR (number from the lead): Antalia 1 measured beside FreyaTTS - measurement only

Card `tts-antalia-measure` (cycle d20261006); proposal `team/proposals/2026-10-05-antalia-turkce-ses-olcum.md`;
plan `team/plans/tts-antalia-integration-plan.md` (main `f33d71ec`). Sibling of the FreyaTTS measurement ADR (ADR-0296).

## Decision

Measure a second free, self-hosted Turkish narration voice, **Antalia 1**, beside FreyaTTS - never instead of it -
with the same twenty sentences (`OWNER_SENTENCES`, imported from `app.voice.stt_compare`), the same container rules and
one comparison table (`docs/evidence/tts-measure-compare.md`). **No** `TTSProvider`, no `TTSRouter` change, no Settings
field, no web change, nothing added to `services/api` dependencies (torch never enters the API image). Wiring is a
separate owner-approved card.

- `tools/tts-measure/antalia/`: own Dockerfile on the FreyaTTS base digest
  (`python:3.12-slim-bookworm@sha256:54c85f3c...`), the plan's 50-line `--hash` lock installed with
  `pip install --require-hashes`, the code fetched file by file at the pinned commits with sha256 checks
  (`0daycloud/antalia@20f9bfea`, five `turkish_tts` modules; `NVIDIA/BigVGAN@7d2b4545`), and a `synthesize.py` speaking
  exactly the FreyaTTS stdin/stdout JSON-lines contract (`{index, chars, audio_ms, synth_ms, first_audio_ms, streamed,
  peak_rss_mb}`, one separate load line, `<NN>.wav` at the model's 24 kHz, fixed seed 20260803 and thread count from
  arguments; sha256 of all four weight files checked before the first synthesis - mismatch = exit 3, nothing loaded).
- The models are built by hand from the verified local files (`load_release_payload` on the local directory, safetensors
  strict; BigVGAN with `torch.load(weights_only=True)` and a strict key check); the upstream hub loaders, which have no
  revision argument, are never called. Synthesis runs with `--network none --read-only`, uid 10001, 8 GB memory with no
  swap, `HF_HUB_OFFLINE=1`; the fill is the only networked step.
- `scripts/voice/tts-measure.ps1 -Engine freya|antalia` (default `freya`, every earlier call unchanged): per engine the
  build context, the content-addressed tag (`pagentos-antalia-measure:<12 hex>`), the weights volume
  (`pagentos-antalia-weights`), the evidence name (`tts-antalia-measure.*`) and the WAV folder
  (`%LOCALAPPDATA%/PagentOS/tts-measure/antalia/<label>/`, never in the repository). Same host allow-list (`MAIL`,
  `pagentos-core`), deadlines and container removal.
- `app/voice/tts_measure.py` stays pure (no torch, no FastAPI, imported by nothing in the application): the report
  carries `model.engine` (a report without it reads as `freya`), and `render_compare` builds one table, one row per
  engine x label; an engine or label with no evidence is an `ölçülmedi` row, never a zero or a blank. The verdict is
  from the numbers only (real time per row; which engine fits a smaller memory) and says sound quality is not judged.
  `CER: ölçülmedi` - it needs an STT pass over the WAVs (a later card).

## Why a second candidate

With one candidate the owner can only accept it or have nothing; two measured side by side, on the same sentences and
rules, give a real choice and a reference point for RTF, first-audio latency and memory.

## Pins, offline synthesis, recipe

Weights `cloud0day3/antalia-1@eaec2aad` (`model.safetensors` `853a117e...`, `config.json` `7c52eb29...`) and
`nvidia/bigvgan_v2_24khz_100band_256x@c329ede9` (`bigvgan_generator.pt` `6f9c5715...`, `config.json` `d77e2c96...`).
Recipe v2 values hard-coded from `inference-recipe.json` (32 Euler steps, speaker `voicedata-candidate-b` passed
explicitly - `None` would be the unconditioned foundation path), single seed, no best-of-8. The recipe's
`pin_noise_envelope` is read by no public code and is **not applied**; the evidence says so.

Plan correction found while building: the plan's post-patch hash of `bigvgan.py` (`fb0dee3e...`) is the **CRLF** form of
the file; the LF bytes `git apply -p4` produces hash to `a5ced3c6...`, which `fetch_code.py` checks (the patch is applied
as its exact one-hunk text replacement; the image has no git/patch tool).

Defect found by the worker's one-sentence smoke: BigVGAN's `remove_weight_norm()` prints `Removing weight norm...` to
stdout, which the merge would count as a malformed line; load and synthesis now run under
`redirect_stdout(sys.stderr)` (regression test `test_antalia_vendor_prints_never_reach_the_json_stdout`).

## Licence (OpenRAIL-M), as the plan reads it

The weights are **Antalia Open RAIL-M** (use restrictions in Attachment A bind the Output). The Proje Yöneticisi
accepted STOP-1 (licence class) and STOP-CHECK (LGPL `soxr` / `libsndfile`, unmodified, dynamically linked, in a private
container that is never distributed; FreyaTTS plan section 1.3 and `fpdf2` precedent) **for this measurement only**: the
owner's own machine, an offline container, nothing in product code. Attribution (paragraph 4.f): "Antalia 1" by Sezgin
Saygili, Emre Kaplaner, Oncel Ozgul and Fikri San Koktas (Patientdesk.ai), https://huggingface.co/cloud0day3/antalia-1 -
written into the Antalia evidence page; no endorsement implied. STOP-2 (no medical advice / result interpretation in this
voice) and STOP-3 (disclose synthetic speech to any listener other than the owner; never on outbound calls or messages)
belong to the wiring card and go on its Onay Merkezi line.

## Accepted risks

- The model is **unmaintained** ("Development of this model is discontinued"): no fixes will come. Acceptable for a
  measurement; a reason for caution before wiring.
- `bigvgan_generator.pt` is a torch pickle: fixed by sha256, loaded `weights_only=True`, inside the offline non-root
  container.

## Proxy vs real CPX32

`cpx32-bicimi` is the home PC limited with `--cpus 4 -Threads 4`, a **proxy**, labelled VEKİL. The real CPX32 run is
`cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)` - a remote action the lead hands over.

## KVKK and where it runs

Scripted synthetic sentences only; nothing of the owner's voice is used or produced. Runs only on `MAIL` and
`pagentos-core` (allow-list checked before any docker call); **the employer machine never runs it**.

## Open follow-up

The wiring card (a local `TTSProvider` behind the paid provider, default OFF, STOP-2/STOP-3 enforced at routing time,
the attribution line on the about page) - only after the owner's `kullanılır` in the Onay Merkezi. CER over the WAVs is
its own later card.
