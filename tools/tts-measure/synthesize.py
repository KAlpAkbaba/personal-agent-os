"""FreyaTTS-small measurement, the container half (tts-freya-measure).

Runs INSIDE the pagentos-freya-measure image (Linux, CPU torch). Three commands:

  selfcheck  import the whole chain once; prints ``IMPORT_OK <torch> <cuda> <threads> <vae class>``.
  fill       (network allowed) fetch the three weight files by their PINNED URLs into
             ``--models`` and check their sha256; a mismatch deletes the file and exits 3.
  synth      (run with ``--network none``) check the sha256 again - a mismatch exits 3 before
             anything is loaded or synthesized - then build the pipeline from the verified
             local files (never ``from_pretrained``: the pinned loader has no revision argument,
             team/plans/tts-freya-integration-plan.md section 2.3), read JSON lines
             ``{index, text}`` on stdin and for each write ``<NN>.wav`` (48 kHz, the model's
             rate) into ``--out`` and one JSON line on stdout:
             ``{index, chars, audio_ms, synth_ms, first_audio_ms, streamed, peak_rss_mb, retries}``.
             The model does not stream, so ``first_audio_ms == synth_ms`` and ``streamed: false``.
             The load time is ONE separate line ``{"event": "load", ...}`` before the sentences.

stdout carries only JSON lines; everything else goes to stderr. No Windows-only path here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

CODE_COMMIT = "146d36c1cb6660646be57d31339db4eed9315de3"
FREYA_REV = "d124e07493615208f58bdd21d432736849ee4230"
VOXCPM_REV = "32279effe8c19989596f05d353d1447f51d9e915"
#: file name -> (pinned URL, sha256). team/plans/tts-freya-integration-plan.md section 2.2.
WEIGHTS: dict[str, tuple[str, str]] = {
    "config.json": (
        f"https://huggingface.co/freyavoice/Freya-TTS/resolve/{FREYA_REV}/config.json",
        "898c9a951a20f960e0936d4e222c2a4044d383f5691efa1a614d812812956a67",
    ),
    "model.safetensors": (
        f"https://huggingface.co/freyavoice/Freya-TTS/resolve/{FREYA_REV}/model.safetensors",
        "9e5828ce9eb6aaf197adc5cb098e2e80e8ff301d238add631418c5557fa08b22",
    ),
    "audiovae.pth": (
        f"https://huggingface.co/openbmb/VoxCPM2/resolve/{VOXCPM_REV}/audiovae.pth",
        "94b5d51e107e0507d4acc976cfdadb64edd6fd06d1f751dadbf2fd1594274bf1",
    ),
}
EXIT_HASH_MISMATCH = 3
SAMPLE_RATE = 48000


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
    args.models.mkdir(parents=True, exist_ok=True)
    bad: list[str] = []
    for name, (url, expected) in WEIGHTS.items():
        target = args.models / name
        if target.is_file() and sha256_of(target) == expected:
            _log(f"fill: {name} already present and verified")
            continue
        part = args.models / (name + ".part")
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
    """What ``load_pipeline`` returns: the pipeline and its retry counter."""

    def __init__(self, tts: Any, counter: list[int]) -> None:
        self.tts = tts
        self.counter = counter


def load_pipeline(models: Path, threads: int, seed: int) -> Loaded:
    """Build FreyaTTS from the verified local files, exactly as pipeline.py:146-157 and
    vae.py:18-26 do, but with no hub call and with the VAE's key check made strict."""
    import numpy as np
    import torch
    from freyatts.model import FreyaDiT
    from freyatts.pipeline import FreyaTTS
    from safetensors.torch import load_file
    from voxcpm.modules.audiovae import AudioVAEConfigV2, AudioVAEV2

    torch.set_num_threads(threads)
    torch.manual_seed(seed)
    np.random.seed(seed)
    cfg = json.loads((models / "config.json").read_text(encoding="utf-8"))
    model = FreyaDiT(
        vocab=cfg["vocab"], d=cfg["d"], depth=cfg["depth"], heads=cfg["heads"], ff=cfg["ff"]
    )
    model.load_state_dict(load_file(str(models / "model.safetensors")), strict=True)
    model = model.to("cpu").eval()
    vae = AudioVAEV2(AudioVAEConfigV2())
    ckpt = torch.load(str(models / "audiovae.pth"), map_location="cpu", weights_only=True)
    keys = vae.load_state_dict(ckpt.get("state_dict", ckpt), strict=False)
    if keys.missing_keys or keys.unexpected_keys:
        raise RuntimeError(
            f"audiovae.pth keys differ: missing {len(keys.missing_keys)}, "
            f"unexpected {len(keys.unexpected_keys)}"
        )
    vae = vae.to("cpu").float().eval()
    for parameter in vae.parameters():
        parameter.requires_grad = False
    import freyatts.pipeline as pipeline_module

    vocab_path = Path(pipeline_module.__file__).with_name("char_vocab.json")
    char_to_id = json.loads(vocab_path.read_text(encoding="utf-8"))
    tts = FreyaTTS(model, vae, char_to_id, device="cpu", seed=seed)
    counter = [0]
    original = tts._synth_one

    def counted(text: str, steps: int = 32, seed: int | None = None) -> Any:
        # pipeline.py re-synthesizes a collapsed clause with seed + 1..3: count those calls
        if seed is not None and seed != tts.seed:
            counter[0] += 1
        return original(text, steps=steps, seed=seed)

    tts._synth_one = counted  # the vendor file is never edited
    return Loaded(tts, counter)


def _write_wav(path: Path, wav: Any) -> None:
    import soundfile

    soundfile.write(str(path), wav, SAMPLE_RATE)


def synth_main(
    argv: list[str],
    *,
    stdin: Iterable[str] | None = None,
    load: Callable[..., Loaded] = load_pipeline,
) -> int:
    parser = argparse.ArgumentParser(prog="synthesize.py synth")
    parser.add_argument("--models", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--threads", required=True, type=int)
    parser.add_argument("--seed", type=int, default=9)
    parser.add_argument("--steps", type=int, default=32)
    args = parser.parse_args(argv)
    bad = mismatched(args.models)
    if bad:
        _emit({"event": "error", "error": "weight_hash_mismatch", "files": bad})
        _log(f"synth: weight files missing or not the pinned bytes: {bad}; nothing synthesized")
        sys.exit(EXIT_HASH_MISMATCH)
    os.environ["OMP_NUM_THREADS"] = str(args.threads)
    started = time.perf_counter()
    loaded = load(args.models, args.threads, args.seed)
    load_ms = (time.perf_counter() - started) * 1000.0
    import torch

    _emit(
        {
            "event": "load",
            "load_ms": round(load_ms, 1),
            "peak_rss_mb": peak_rss_mb(),
            "cpu": cpu_name(),
            "threads": torch.get_num_threads(),
            "seed": args.seed,
            "steps": args.steps,
            "code_commit": CODE_COMMIT,
            "torch": torch.__version__,
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
            before = loaded.counter[0]
            began = time.perf_counter()
            wav = loaded.tts.synthesize(text, steps=args.steps, seed=args.seed)
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
                    "retries": loaded.counter[0] - before,
                }
            )
        except Exception as error:  # noqa: BLE001 - one sentence failing is a row, not a stop
            _emit({"index": index, "error": f"{type(error).__name__}: {error}"[:300]})
    return 0


def selfcheck_main() -> int:
    import freyatts  # noqa: F401
    import freyatts.vae  # noqa: F401
    import torch
    import voxcpm.modules.audiovae as audiovae

    print(
        "IMPORT_OK",
        torch.__version__,
        torch.cuda.is_available(),
        torch.get_num_threads(),
        audiovae.AudioVAEV2.__name__,
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
