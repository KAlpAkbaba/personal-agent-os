# ADR (draft, the lead numbers it): FreyaTTS-small is measured in its own container, measurement only

Task `tts-freya-measure`, cycle d20261004. Plan: `team/plans/tts-freya-integration-plan.md`.

## Decision

- **Measurement only.** No `TTSProvider`, no `TTSRouter` change, no setting, no web change, nothing added to
  `services/api` (pyproject / uv.lock untouched). Wiring is a separate owner approval.
- **Own container, not an API dependency.** `tools/tts-measure/` builds `pagentos-freya-measure:<tag>` from the
  pinned `python:3.12-slim-bookworm@sha256:54c85f3c…`, `pip install --no-deps --require-hashes` of the plan's 62-line
  lock (torch 2.11.0+cpu), and the FreyaTTS code at `146d36c1…` fetched file by file with each file's sha256
  checked (`fetch_code.py`; a mismatch fails the build). torch never enters the API image (CLAUDE.md voice rule:
  narration TTS is its own subsystem). The tag is content-addressed (sha256 over Dockerfile, lock, fetch_code.py,
  synthesize.py), so a stale image is never reused after a change.
- **Pins and offline synthesis.** The pinned loader has no revision argument (plan §2.3), so `synthesize.py`
  never calls `from_pretrained` / `load_audio_vae`: `fill` (the only networked run) fetches the three weight
  files by their pinned-revision URLs into the named volume `pagentos-freya-weights` and checks sha256; `synth`
  checks the hashes again (mismatch = exit 3, nothing loaded, no evidence written) and builds the pipeline by
  hand from the verified files (VAE key check made strict). `synth` runs `--network none --read-only`, uid
  10001, weights mounted read-only, `HF_HUB_OFFLINE=1`, no `HF_TOKEN`, `--memory 8g --memory-swap 8g` (a first
  run at 4g reached 4.2 GB RSS and swapped, which distorted RTF; an overrun is now an OOM failure, not a slowdown).
- **Upstream's silent guard is made loud.** In the read-only non-root container numba cannot cache, so
  `librosa.pyin` raised and upstream `_voiced_ok` (`except Exception: return True`) silently switched the
  clause-collapse guard off. `NUMBA_CACHE_DIR=/tmp/numba` (tmpfs) and an unguarded pyin warm-up in the load step
  (fails the load if pyin cannot run; its JIT compile goes into load time, not sentence 1). The time spent in
  that check is reported per sentence (`voiced_check_ms`, inside synth_ms) and as a share per machine.
- **Numbers.** `app.voice.tts_measure` (pure, not imported by the application) uses `OWNER_SENTENCES` itself;
  pooled RTF = sum synth / sum audio (not the mean of ratios); p50/p95 nearest rank (`stt_compare.percentile`);
  a malformed line or a missing index is a failed sentence with its reason; load time is one separate line;
  the model does not stream, so first-audio = synth (`streamed: false`, said in the .md). The verdict states
  only real time or not (pooled RTF < 1) and that quality is the owner's ear.
- **Proxy vs real CPX32.** `cpx32-bicimi` is the home PC limited to `--cpus 4 -Threads 4`, labelled VEKİL
  (proxy). The real CPX32 run is a remote write on the production host: the lead hands it to the release step /
  owner; the .md carries `cpx32-gercek: NOT_RUN`. The script runs unchanged on Linux PowerShell 7
  (`/usr/bin/docker`, `--user $(id -u)` for the bind mount, no Windows path in synthesize.py).
- **Where it runs.** An allow-list (`MAIL`, `pagentos-core`), checked before any docker call. The employer's
  office PC never runs it.
- **KVKK.** The twenty sentences are scripted and synthetic; nothing of the owner's voice is used or produced.
  WAVs go to `%LOCALAPPDATA%\PagentOS\tts-measure\<label>\` (Linux `~/.local/share/...`), never into the repo;
  the .md refuses a WAV folder inside the repository.

## Open follow-up (not this card)

A wiring card would add a `LocalTurkishTTSProvider` (`TTSProvider` over HTTP or stdio to this container, no torch
in the API), registered OFF by default as the last-resort provider behind the paid one in `TTSRouter`, decide
48 kHz → the narration player's format, and whether to emit per clause (the upstream split exists) to cut the
first-audio latency. That depends on the owner's 'kullanılır' verdict and on the measured RTF.

## Disk note

Docker's WSL disk is on C:. The script refuses to start under 20 GB free (`-MinFreeGB`, plan §3). At the end of
this card C: had 15.9 GB free (other seats' work plus two rebuilds), so the inspector's run needs space freed
first or an explicit, recorded lower `-MinFreeGB`.
