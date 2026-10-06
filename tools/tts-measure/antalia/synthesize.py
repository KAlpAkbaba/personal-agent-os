"""Antalia 1 measurement, the container half (tts-antalia-measure), beside FreyaTTS.

Runs INSIDE the pagentos-antalia-measure image (Linux, CPU torch). Speaks exactly the
stdin/stdout contract of ``tools/tts-measure/synthesize.py`` (FreyaTTS). Three commands:

  selfcheck  import the whole chain once; prints ``IMPORT_OK <torch> <cuda> <threads> <vocoder>``.
  fill       (network allowed) fetch the four weight files by their PINNED URLs into
             ``--models`` and check their sha256; a mismatch deletes the file and exits 3.
  synth      (run with ``--network none``) check the sha256 again - a mismatch exits 3 before
             anything is loaded or synthesized - then build both models by hand from the
             verified local files (never ``synthesize_crossflow`` / ``from_pretrained``: the
             pinned loaders have no revision argument, team/plans/tts-antalia-integration-plan.md
             section 2.3), read JSON lines ``{index, text}`` on stdin and for each write
             ``<NN>.wav`` (24 kHz, the model's rate) into ``--out`` and one JSON line on stdout:
             ``{index, chars, audio_ms, synth_ms, first_audio_ms, streamed, peak_rss_mb}``.
             The model does not stream, so ``first_audio_ms == synth_ms`` and ``streamed: false``.
             The load time is ONE separate line ``{"event": "load", ...}`` before the sentences.

Recipe v2 (inference-recipe.json @ eaec2aad, plan section 4), single seed, no best-of-8; the
recipe's ``pin_noise_envelope`` is not read by the public code and is not applied.
stdout carries only JSON lines; everything else goes to stderr. No Windows-only path here.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

CODE_COMMIT = "20f9bfeaaefefb3ef723c292fe2bb0306e08823d"
BIGVGAN_COMMIT = "7d2b454564a6c7d014227f635b7423881f14bdac"
ANTALIA_REV = "eaec2aad2da8c0db5fc359734470874dae82c603"
VOCODER_REV = "c329ede9e9bbc100ddf5c91e2330a61921262370"
_ANTALIA = f"https://huggingface.co/cloud0day3/antalia-1/resolve/{ANTALIA_REV}"
_VOCODER = f"https://huggingface.co/nvidia/bigvgan_v2_24khz_100band_256x/resolve/{VOCODER_REV}"
#: file under --models -> (pinned URL, sha256). team/plans/tts-antalia-integration-plan.md 2.2.
WEIGHTS: dict[str, tuple[str, str]] = {
    "antalia-1/config.json": (
        f"{_ANTALIA}/config.json",
        "7c52eb29997ff38d106cf9082a6e9f76ecf1a2cfab9ff50333e5c0414c6ed8cf",
    ),
    "antalia-1/model.safetensors": (
        f"{_ANTALIA}/model.safetensors",
        "853a117ef95fa44efff785a6b674f380878da57879cb879d6d099d1e1444266e",
    ),
    "bigvgan/config.json": (
        f"{_VOCODER}/config.json",
        "d77e2c96583ca2296ac112a56ec7cc6bd5da4bf7681ceff18448bedc4fcf6512",
    ),
    "bigvgan/bigvgan_generator.pt": (
        f"{_VOCODER}/bigvgan_generator.pt",
        "6f9c5715550c9d0f11159ceb8935638da5aeb19e27d1e63677632df095e376f5",
    ),
}
EXIT_HASH_MISMATCH = 3
SAMPLE_RATE = 24000
DEFAULT_SEED = 20260803
SPEAKER = "voicedata-candidate-b"
#: inference-recipe.json v2 @ eaec2aad (the card's Quick start); plan section 4.
RECIPE: dict[str, Any] = {
    "duration_scale": 1.0,
    "prosody": [-1.2398956, 1.1943912, -2.1267404, -0.9549347, 0.9637866, 0.5145879],
    "text_guidance_scale": 4.0,
    "speaker_guidance_scale": 1.0,
    "sway_coefficient": -0.8,
    "solver": "euler",
    "guidance_rescale": 0.5,
    "mel_clamp": 5.0,
    "reference": None,
    "context_guidance_scale": 1.0,
    "min_seconds_per_char": 0.085,
    "chunk_character_limit": 120,
    "chunk_pause_seconds": 0.16,
}


def _emit(row: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _log(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mismatched(models: Path) -> list[str]:
    """Names of the weight files that are missing or whose sha256 is not the pinned one."""
    return [
        name
        for name, (_, expected) in WEIGHTS.items()
        if not (models / name).is_file() or sha256_of(models / name) != expected
    ]


def peak_rss_mb() -> float | None:
    try:
        import resource
    except ImportError:  # not Linux: the container always is
        return None
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)


def cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


# ------------------------------------------------------------------ fill


def fill_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="synthesize.py fill")
    parser.add_argument("--models", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    import ssl
    import urllib.request

    import certifi

    context = ssl.create_default_context(cafile=certifi.where())
    bad: list[str] = []
    for name, (url, expected) in WEIGHTS.items():
        target = args.models / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and sha256_of(target) == expected:
            _log(f"fill: {name} already present and verified")
            continue
        part = target.with_name(target.name + ".part")
        _log(f"fill: downloading {url}")
        with urllib.request.urlopen(url, timeout=args.timeout, context=context) as response:  # noqa: S310 - fixed https URL
            with part.open("wb") as handle:
                for chunk in iter(lambda: response.read(1 << 20), b""):
                    handle.write(chunk)
        actual = sha256_of(part)
        if actual != expected:
            part.unlink()
            bad.append(name)
            _log(f"fill: {name} sha256 {actual} != pinned {expected}")
            continue
        part.replace(target)
    if bad:
        _emit({"event": "error", "error": "weight_hash_mismatch", "files": bad})
        return EXIT_HASH_MISMATCH
    _emit({"event": "fill", "ok": True, "files": sorted(WEIGHTS)})
    return 0


# ------------------------------------------------------------------ synth


class Loaded:
    """What ``load_models`` returns: ``speak(text, steps, seed) -> waveform`` (float32 mono at
    ``SAMPLE_RATE``) and the thread count torch actually uses."""

    def __init__(self, speak: Callable[[str, int, int], Sequence[float]], threads: int) -> None:
        self.speak = speak
        self.threads = threads


def load_models(models: Path, threads: int, seed: int) -> Loaded:
    """Build the acoustic model and the vocoder from the verified local files (plan section
    2.3/2): ``load_release_payload`` on the local directory (safetensors, strict) and BigVGAN by
    hand with ``torch.load(weights_only=True)`` and a strict key check - never the hub path."""
    import numpy as np
    import torch
    from bigvgan import BigVGAN
    from env import AttrDict
    from turkish_tts.crossflow_release import load_release_payload
    from turkish_tts.crossflow_train import (
        CharacterTokenizer,
        CrossFlowTrainConfig,
        MelNormalizer,
        _resolve_speaker_id,
        _synthesize_loaded_crossflow,
    )

    torch.set_num_threads(threads)
    torch.manual_seed(seed)
    np.random.seed(seed)
    model, payload = load_release_payload(models / "antalia-1", "cpu")
    model.set_adapter_scale(1.0)
    tokenizer = CharacterTokenizer(payload["vocabulary"])
    train_config = CrossFlowTrainConfig(**payload["train_config"])
    if train_config.sample_rate != SAMPLE_RATE:
        raise RuntimeError(f"model rate {train_config.sample_rate} != {SAMPLE_RATE}")
    mel_normalizer = MelNormalizer(train_config, "cpu")
    # explicit: speaker None would be id 0, the unconditioned foundation path (plan section 4)
    speaker_id = _resolve_speaker_id(payload, SPEAKER)
    hparams = AttrDict(json.loads((models / "bigvgan" / "config.json").read_text(encoding="utf-8")))
    vocoder = BigVGAN(hparams, use_cuda_kernel=False)
    checkpoint = torch.load(
        str(models / "bigvgan" / "bigvgan_generator.pt"), map_location="cpu", weights_only=True
    )
    vocoder.load_state_dict(checkpoint["generator"], strict=True)
    vocoder.remove_weight_norm()
    vocoder = vocoder.eval()

    def speak(text: str, steps: int, seed: int) -> Sequence[float]:
        waveform, _normalized, _frames = _synthesize_loaded_crossflow(
            model=model,
            tokenizer=tokenizer,
            vocoder=vocoder,
            mel_normalizer=mel_normalizer,
            text=text,
            sample_rate=train_config.sample_rate,
            hop_length=train_config.hop_length,
            device="cpu",
            steps=steps,
            seed=seed,
            speaker_id=speaker_id,
            text_normalization=train_config.text_normalization,
            **RECIPE,
        )
        return waveform

    return Loaded(speak, torch.get_num_threads())


def _write_wav(path: Path, wav: Any) -> None:
    import soundfile

    soundfile.write(str(path), wav, SAMPLE_RATE)


def synth_main(
    argv: list[str],
    *,
    stdin: Iterable[str] | None = None,
    load: Callable[..., Loaded] = load_models,
) -> int:
    parser = argparse.ArgumentParser(prog="synthesize.py synth")
    parser.add_argument("--models", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--threads", required=True, type=int)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--steps", type=int, default=32)
    args = parser.parse_args(argv)
    bad = mismatched(args.models)
    if bad:
        _emit({"event": "error", "error": "weight_hash_mismatch", "files": bad})
        _log(f"synth: weight files missing or not the pinned bytes: {bad}; nothing synthesized")
        sys.exit(EXIT_HASH_MISMATCH)
    os.environ["OMP_NUM_THREADS"] = str(args.threads)
    started = time.perf_counter()
    # BigVGAN's remove_weight_norm() prints to stdout; stdout is the JSON-lines contract.
    with contextlib.redirect_stdout(sys.stderr):
        loaded = load(args.models, args.threads, args.seed)
    load_ms = (time.perf_counter() - started) * 1000.0
    _emit(
        {
            "event": "load",
            "load_ms": round(load_ms, 1),
            "peak_rss_mb": peak_rss_mb(),
            "cpu": cpu_name(),
            "threads": loaded.threads,
            "seed": args.seed,
            "steps": args.steps,
            "speaker": SPEAKER,
            "code_commit": CODE_COMMIT,
            "bigvgan_commit": BIGVGAN_COMMIT,
            "noise_envelope_pinning": False,
        }
    )
    args.out.mkdir(parents=True, exist_ok=True)
    for raw in stdin if stdin is not None else sys.stdin:
        line = raw.strip()
        if not line:
            continue
        index: Any = None
        try:
            row = json.loads(line)
            index = int(row["index"])
            text = str(row["text"])
            began = time.perf_counter()
            with contextlib.redirect_stdout(sys.stderr):
                wav = loaded.speak(text, args.steps, args.seed)
            synth_ms = (time.perf_counter() - began) * 1000.0
            _write_wav(args.out / f"{index:02d}.wav", wav)
            _emit(
                {
                    "index": index,
                    "chars": len(text),
                    "audio_ms": round(len(wav) / SAMPLE_RATE * 1000.0, 1),
                    "synth_ms": round(synth_ms, 1),
                    "first_audio_ms": round(synth_ms, 1),
                    "streamed": False,
                    "peak_rss_mb": peak_rss_mb(),
                }
            )
        except Exception as error:  # noqa: BLE001 - one sentence failing is a row, not a stop
            _emit({"index": index, "error": f"{type(error).__name__}: {error}"[:300]})
    return 0


def selfcheck_main() -> int:
    import torch
    import turkish_tts.crossflow  # noqa: F401
    import turkish_tts.crossflow_release  # noqa: F401
    import turkish_tts.crossflow_train  # noqa: F401
    from bigvgan import BigVGAN

    print(
        "IMPORT_OK",
        torch.__version__,
        torch.cuda.is_available(),
        torch.get_num_threads(),
        BigVGAN.__name__,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _log(
            "usage: synthesize.py selfcheck | fill --models DIR"
            " | synth --models DIR --out DIR --threads N"
        )
        return 2
    command, rest = argv[0], argv[1:]
    if command == "selfcheck":
        return selfcheck_main()
    if command == "fill":
        return fill_main(rest)
    if command == "synth":
        return synth_main(rest)
    _log(f"unknown command {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
