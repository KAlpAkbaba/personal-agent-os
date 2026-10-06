"""Speaker-engine measurement, the container half (speaker-engine-measure): MEASUREMENT ONLY.

Runs INSIDE the pagentos-speaker-measure image (Linux, sherpa-onnx 1.13.8 on CPU). Commands:

  selfcheck  import sherpa_onnx + numpy and check the API names the measurement calls;
             prints ``SELFCHECK_OK <sherpa-onnx> <numpy>``.
  fill       (network allowed; fetch_models.py) download the pinned files into ``--models``.
  splice     cut the SPLICED conversation from a plan (``app.voice.speaker_measure.splice_plan``
             wrote it): owner and guest turns with silences, one 16 kHz WAV. Stdlib only.
  measure    (run with ``--network none``) re-hash every model file it will load - a missing
             or different file prints one error line and exits 3 before anything is loaded -
             then reads JSON-lines jobs on stdin and writes one JSON line per result on
             stdout, after a first ``{"kind": "load", ...}`` line:
               {kind: embed, model, path, label[, chunk_s]} -> {kind, model, label, chunks,
                   audio_ms, wall_ms, peak_rss_mb}; the vector stays in this process's memory
                   and is NEVER printed;
               {kind: score, model, name, pairs: [[glob, glob], ...]} -> {kind, model, name, n,
                   scores}: cosine scores, the arithmetic of app/voice/speaker.py:99-111;
               {kind: diarize, model, segmenter, path, label, num_speakers: 2 | null} ->
                   {kind, model, label, num_speakers, segments: [{start_s, end_s, speaker}],
                   speakers_found, audio_ms, wall_ms, peak_rss_mb}.
             A job that fails is a row with ``error``, never a stop.

stdout carries only JSON lines; everything else goes to stderr. Pins and API calls:
team/plans/speaker-engine-integration-plan.md sections 2-4. A WAV that is not 16 kHz mono
PCM16 is refused, never converted.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import sys
import time
import wave
from pathlib import Path
from typing import Any, NamedTuple


class ModelFile(NamedTuple):
    path: str
    url: str
    bytes: int
    sha256: str


_EMB = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
_SEG = (
    "https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/"
    "9403a6902bb58e3d5ae8c7e77c3422de279db2e0/"
)
#: id -> the pinned file (plan section 2.2). The sha256 is the pin, not the URL.
MODEL_FILES: dict[str, ModelFile] = {
    "campplus-zh-en-advanced": ModelFile(
        "3dspeaker/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
        _EMB + "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
        28281164,
        "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2",
    ),
    "eres2netv2-zh-cn": ModelFile(
        "3dspeaker/3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx",
        _EMB + "3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx",
        71441526,
        "bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239",
    ),
    "campplus-voxceleb": ModelFile(
        "3dspeaker/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
        _EMB + "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
        29596978,
        "357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b",
    ),
    "pyannote-seg-3-0": ModelFile(
        "pyannote-seg-3-0/model.onnx",
        _SEG + "model.onnx",
        5992913,
        "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
    ),
    "pyannote-seg-3-0-license": ModelFile(
        "pyannote-seg-3-0/LICENSE",
        _SEG + "LICENSE",
        1061,
        "14d7016ad68e7394d6e6b78d96cc2ae431c905287b89674cfdf021e79e62b8ba",
    ),
}
EMBEDDING_IDS = ("campplus-zh-en-advanced", "eres2netv2-zh-cn", "campplus-voxceleb")
SEGMENTER_ID = "pyannote-seg-3-0"
EXIT_BAD_INPUT = 2
EXIT_HASH_MISMATCH = 3
RATE = 16_000
#: Clustering of the threshold row (num_speakers null) and the duration filters: the upstream
#: example's values (plan section 4).
CLUSTER_THRESHOLD = 0.5
MIN_DURATION_ON = 0.3
MIN_DURATION_OFF = 0.5


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


def mismatched(models: Path, ids: list[str]) -> list[str]:
    """Ids whose file is missing, of another size or not the pinned sha256."""
    bad = []
    for key in ids:
        entry = MODEL_FILES[key]
        target = models / entry.path
        if (
            not target.is_file()
            or target.stat().st_size != entry.bytes
            or sha256_of(target) != entry.sha256
        ):
            bad.append(key)
    return bad


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


def read_pcm16(path: Path) -> bytes:
    with wave.open(str(path), "rb") as handle:
        shape = (handle.getnchannels(), handle.getsampwidth(), handle.getframerate())
        if shape != (1, 2, RATE):
            raise ValueError(
                f"{path.name}: needs 16 kHz mono 16-bit PCM, got {shape[2]} Hz, "
                f"{shape[0]} channel(s), {8 * shape[1]}-bit; never converted"
            )
        return handle.readframes(handle.getnframes())


def cosine_similarity(a: list[float], b: list[float]) -> float:
    # app/voice/speaker.py cosine_similarity, the arithmetic line for line (the image has no
    # app code; a test compares the two bodies), so a score here means what it means there.
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} != {len(b)}")
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return max(-1.0, min(1.0, dot / (na * nb)))


# ------------------------------------------------------------------ splice


def splice_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="measure.py splice")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    sources = {
        who: b"".join(read_pcm16(Path(p)) for p in paths) for who, paths in plan["sources"].items()
    }
    parts: list[bytes] = []
    for segment in plan["segments"]:
        start = round(float(segment["source_start_s"]) * RATE) * 2
        length = round(float(segment["duration_s"]) * RATE) * 2
        piece = sources[segment["speaker"]][start : start + length]
        parts.append(piece + b"\x00" * (length - len(piece)))
        parts.append(b"\x00" * (round(float(segment["gap_after_s"]) * RATE) * 2))
    data = b"".join(parts)
    with wave.open(str(args.out), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(data)
    _emit(
        {
            "kind": "splice",
            "segments": len(plan["segments"]),
            "audio_ms": round(len(data) / 2 / RATE * 1000.0, 1),
        }
    )
    return 0


# ----------------------------------------------------------------- measure


class Engines:
    """The loaded extractors and diarizers. Vectors live in ``held`` only, in memory."""

    def __init__(self, models: Path, ids: list[str], threads: int, segmenter: bool) -> None:
        import sherpa_onnx

        self.sherpa = sherpa_onnx
        self.extractors: dict[str, Any] = {}
        self.diarizers: dict[tuple[str, int | None], Any] = {}
        self.load_ms: dict[str, float] = {}
        self.held: dict[str, dict[str, list[float]]] = {key: {} for key in ids}
        for key in ids:
            began = time.perf_counter()
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(models / MODEL_FILES[key].path),
                num_threads=threads,
                debug=False,
                provider="cpu",
            )
            if not config.validate():
                raise RuntimeError(f"{key}: SpeakerEmbeddingExtractorConfig does not validate")
            self.extractors[key] = sherpa_onnx.SpeakerEmbeddingExtractor(config)
            self.load_ms[key] = round((time.perf_counter() - began) * 1000.0, 1)
        if segmenter:
            began = time.perf_counter()
            for key in ids:
                for speakers in (2, None):
                    self.diarizers[(key, speakers)] = self._diarizer(models, key, threads, speakers)
            self.load_ms[SEGMENTER_ID] = round((time.perf_counter() - began) * 1000.0, 1)

    def _diarizer(self, models: Path, key: str, threads: int, speakers: int | None) -> Any:
        so = self.sherpa
        config = so.OfflineSpeakerDiarizationConfig(
            segmentation=so.OfflineSpeakerSegmentationModelConfig(
                pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(models / MODEL_FILES[SEGMENTER_ID].path)
                ),
                num_threads=threads,
                debug=False,
                provider="cpu",
            ),
            embedding=so.SpeakerEmbeddingExtractorConfig(
                model=str(models / MODEL_FILES[key].path), num_threads=threads, provider="cpu"
            ),
            clustering=so.FastClusteringConfig(
                num_clusters=speakers if speakers else -1, threshold=CLUSTER_THRESHOLD
            ),
            min_duration_on=MIN_DURATION_ON,
            min_duration_off=MIN_DURATION_OFF,
        )
        if not config.validate():
            raise RuntimeError(f"{SEGMENTER_ID} + {key}: diarization config does not validate")
        diarizer = so.OfflineSpeakerDiarization(config)
        if diarizer.sample_rate != RATE:
            raise RuntimeError(f"diarizer sample rate {diarizer.sample_rate} != {RATE}")
        return diarizer

    def _vector(self, key: str, samples: Any) -> list[float]:
        import numpy as np

        extractor = self.extractors[key]
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=RATE, waveform=samples)
        stream.input_finished()
        if not extractor.is_ready(stream):
            raise RuntimeError("the extractor is not ready (recording too short)")
        return [float(v) for v in np.asarray(extractor.compute(stream), dtype=np.float64)]

    def embed(self, job: dict[str, Any]) -> dict[str, Any]:
        import numpy as np

        key, label = str(job["model"]), str(job["label"])
        pcm = read_pcm16(Path(job["path"]))
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        began = time.perf_counter()
        chunk_s = job.get("chunk_s")
        if chunk_s:
            size = int(float(chunk_s) * RATE)
            count = len(samples) // size
            if count == 0:
                raise ValueError(f"{label}: shorter than one {chunk_s} s chunk")
            for k in range(count):
                piece = samples[k * size : (k + 1) * size]
                self.held[key][f"{label}#{k:03d}"] = self._vector(key, piece)
            used = count * size
        else:
            count = 1
            self.held[key][label] = self._vector(key, samples)
            used = len(samples)
        wall_ms = (time.perf_counter() - began) * 1000.0
        return {
            "kind": "embed",
            "model": key,
            "label": label,
            "chunks": count,
            "audio_ms": round(used / RATE * 1000.0, 1),
            "wall_ms": round(wall_ms, 1),
            "peak_rss_mb": peak_rss_mb(),
        }

    def score(self, job: dict[str, Any]) -> dict[str, Any]:
        key = str(job["model"])
        held = self.held[key]
        scores: list[float] = []
        for left, right in job["pairs"]:
            a = [label for label in held if fnmatch.fnmatchcase(label, left)]
            b = [label for label in held if fnmatch.fnmatchcase(label, right)]
            if left == right:
                pairs = [(a[i], a[j]) for i in range(len(a)) for j in range(i + 1, len(a))]
            else:
                pairs = [(x, y) for x in a for y in b if x != y]
            scores += [round(cosine_similarity(held[x], held[y]), 6) for x, y in pairs]
        return {
            "kind": "score",
            "model": key,
            "name": str(job["name"]),
            "n": len(scores),
            "scores": scores,
        }

    def diarize(self, job: dict[str, Any]) -> dict[str, Any]:
        import numpy as np

        key = str(job["model"])
        speakers = job.get("num_speakers")
        diarizer = self.diarizers[(key, int(speakers) if speakers else None)]
        pcm = read_pcm16(Path(job["path"]))
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        began = time.perf_counter()
        result = diarizer.process(samples).sort_by_start_time()
        wall_ms = (time.perf_counter() - began) * 1000.0
        segments = [
            {
                "start_s": round(float(s.start), 3),
                "end_s": round(float(s.end), 3),
                "speaker": int(s.speaker),
            }
            for s in result
        ]
        return {
            "kind": "diarize",
            "model": key,
            "segmenter": SEGMENTER_ID,
            "label": str(job["label"]),
            "num_speakers": speakers,
            "segments": segments,
            "speakers_found": len({s["speaker"] for s in segments}),
            "audio_ms": round(len(samples) / RATE * 1000.0, 1),
            "wall_ms": round(wall_ms, 1),
            "peak_rss_mb": peak_rss_mb(),
        }


def measure_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="measure.py measure")
    parser.add_argument("--models-dir", required=True, type=Path)
    parser.add_argument("--model", action="append", required=True, choices=EMBEDDING_IDS)
    parser.add_argument("--threads", required=True, type=int)
    parser.add_argument("--segmenter", action="store_true")
    args = parser.parse_args(argv)
    ids = list(dict.fromkeys(args.model))
    needed = ids + ([SEGMENTER_ID] if args.segmenter else [])
    bad = mismatched(args.models_dir, needed)
    if bad:
        _emit({"kind": "error", "error": "weight_hash_mismatch", "files": bad})
        _log(f"measure: model files missing or not the pinned bytes: {bad}; nothing measured")
        return EXIT_HASH_MISMATCH
    began = time.perf_counter()
    engines = Engines(args.models_dir, ids, args.threads, args.segmenter)
    _log(f"measure: loaded in {(time.perf_counter() - began) * 1000.0:.0f} ms")
    _emit(
        {
            "kind": "load",
            "load_ms": engines.load_ms,
            "peak_rss_mb": peak_rss_mb(),
            "cpu": cpu_name(),
            "threads": args.threads,
            "sherpa_onnx": str(getattr(engines.sherpa, "__version__", "")),
        }
    )
    handlers = {"embed": engines.embed, "score": engines.score, "diarize": engines.diarize}
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            job: dict[str, Any] = {}
            try:
                job = json.loads(line)
                _emit(handlers[str(job["kind"])](job))
            except Exception as error:  # noqa: BLE001 - one job failing is a row, not a stop
                _emit(
                    {
                        "kind": job.get("kind"),
                        "model": job.get("model"),
                        "label": job.get("label") or job.get("name"),
                        "error": f"{type(error).__name__}: {error}"[:300],
                    }
                )
    finally:
        # the vectors existed only here; nothing of them was printed or written
        for held in engines.held.values():
            held.clear()
    return 0


def selfcheck_main() -> int:
    import numpy
    import sherpa_onnx

    needed = (
        "OfflineSpeakerDiarization",
        "OfflineSpeakerDiarizationConfig",
        "OfflineSpeakerSegmentationModelConfig",
        "OfflineSpeakerSegmentationPyannoteModelConfig",
        "FastClusteringConfig",
        "SpeakerEmbeddingExtractor",
        "SpeakerEmbeddingExtractorConfig",
    )
    missing = [name for name in needed if not hasattr(sherpa_onnx, name)]
    if missing:
        _log(f"selfcheck: sherpa_onnx lacks {missing}")
        return 1
    print("SELFCHECK_OK", getattr(sherpa_onnx, "__version__", "?"), numpy.__version__)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _log(
            "usage: measure.py selfcheck | fill --models DIR | splice --plan F --out F"
            " | measure --models-dir DIR --model ID [--model ID] --threads N [--segmenter]"
        )
        return EXIT_BAD_INPUT
    command, rest = argv[0], argv[1:]
    if command == "selfcheck":
        return selfcheck_main()
    if command == "fill":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import fetch_models

        return fetch_models.main(rest)
    if command == "splice":
        return splice_main(rest)
    if command == "measure":
        return measure_main(rest)
    _log(f"unknown command {command}")
    return EXIT_BAD_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
