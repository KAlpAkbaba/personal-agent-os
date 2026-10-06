# ruff: noqa: E501 - the fake sherpa_onnx below is one literal program
"""Unit tests: ``tools/speaker-measure/measure.py`` as a CHILD PROCESS (speaker-engine-measure).

sherpa-onnx is replaced by a FAKE ``sherpa_onnx`` module put first on the child's
``sys.path``: an "embedding" is derived from each waveform's dominant frequency, so two
synthetic tones are two "voices", and "diarisation" labels 0.1 s frames by energy and tone.
The pinned hash table is pointed at the fake model files by a two-line runner (the real
table is checked against the integration plan by its own test). The tool's code itself is
never edited.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import math
import os
import re
import struct
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from app.voice import speaker
from app.voice import speaker_measure as sm

REPO = Path(sm.__file__).resolve().parents[4]
TOOL = REPO / "tools" / "speaker-measure"
PLAN = REPO / "team" / "plans" / "speaker-engine-integration-plan.md"
RATE = 16_000
RUN_DEADLINE_S = 120
MODEL = sm.MODEL_IDS[0]
TIMING_KEYS = {"wall_ms", "load_ms", "peak_rss_mb"}

FAKE_SHERPA = r'''
"""FAKE sherpa_onnx for the measure.py tests: tone -> voice."""
import numpy as np
from pathlib import Path

CENTERS = [200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0]


def _dominant(x):
    x = np.asarray(x, dtype=np.float64)
    spectrum = np.abs(np.fft.rfft(x))
    spectrum[0] = 0.0
    return float(np.argmax(spectrum)) * 16000.0 / len(x)


class SpeakerEmbeddingExtractorConfig:
    def __init__(self, model="", num_threads=1, debug=False, provider="cpu"):
        self.model, self.num_threads, self.debug, self.provider = model, num_threads, debug, provider

    def validate(self):
        return Path(self.model).is_file()


class _Stream:
    def __init__(self):
        self.x = None
        self.done = False

    def accept_waveform(self, sample_rate, waveform):
        assert sample_rate == 16000
        assert waveform.dtype == np.float32
        self.x = waveform

    def input_finished(self):
        self.done = True


class SpeakerEmbeddingExtractor:
    def __init__(self, config):
        assert config.validate()
        self.dim = len(CENTERS)

    def create_stream(self):
        return _Stream()

    def is_ready(self, stream):
        return stream.done

    def compute(self, stream):
        f = _dominant(stream.x)
        return [float(np.exp(-((f - c) / 60.0) ** 2)) for c in CENTERS]


class OfflineSpeakerSegmentationPyannoteModelConfig:
    def __init__(self, model="", window_shift_ratio=0.1):
        self.model = model


class OfflineSpeakerSegmentationModelConfig:
    def __init__(self, pyannote=None, num_threads=1, debug=False, provider="cpu"):
        self.pyannote = pyannote


class FastClusteringConfig:
    def __init__(self, num_clusters=-1, threshold=0.5, compute_confidence=False):
        self.num_clusters, self.threshold = num_clusters, threshold


class OfflineSpeakerDiarizationConfig:
    def __init__(self, segmentation=None, embedding=None, clustering=None, min_duration_on=0.3, min_duration_off=0.5):
        self.segmentation, self.embedding, self.clustering = segmentation, embedding, clustering

    def validate(self):
        return Path(self.segmentation.pyannote.model).is_file() and self.embedding.validate()


class _Segment:
    def __init__(self, start, end, speaker):
        self.start, self.end, self.speaker = start, end, speaker


class _Result:
    def __init__(self, segments):
        self.segments = segments

    def sort_by_start_time(self):
        return sorted(self.segments, key=lambda s: s.start)


class OfflineSpeakerDiarization:
    sample_rate = 16000

    def __init__(self, config):
        assert config.validate()

    def process(self, x):
        x = np.asarray(x, dtype=np.float32)
        hop = 1600
        labels = []
        for i in range(0, len(x) - hop + 1, hop):
            frame = x[i:i + hop]
            if float(np.sqrt(np.mean(frame.astype(np.float64) ** 2))) < 0.01:
                labels.append(None)
            else:
                labels.append(0 if _dominant(frame) < 600.0 else 1)
        segments, start = [], None
        for k, who in enumerate(labels + [None]):
            if start is not None and who != labels[start]:
                segments.append(_Segment(start * 0.1, k * 0.1, labels[start]))
                start = None
            if who is not None and start is None:
                start = k
        return _Result(segments)
'''

RUNNER = r"""
import json, os, sys
sys.path.insert(0, os.environ["SPK_TOOL_DIR"])
import measure
for key, (size, digest) in json.loads(os.environ["SPK_PINS"]).items():
    measure.MODEL_FILES[key] = measure.MODEL_FILES[key]._replace(bytes=size, sha256=digest)
raise SystemExit(measure.main(sys.argv[1:]))
"""

BLOCK_SOCKETS = r"""
import os, socket

class _Blocked(socket.socket):
    def __init__(self, *args, **kwargs):
        raise OSError("network blocked by the test")

def _no_connection(*args, **kwargs):
    raise OSError("network blocked by the test")

socket.socket = _Blocked
socket.create_connection = _no_connection
with open(os.environ["SPK_SITE_MARKER"], "w") as handle:
    handle.write("sockets blocked")
"""


# ------------------------------------------------------------------ helpers


def _tone(seconds: float, freq: float) -> list[float]:
    return [0.5 * math.sin(2 * math.pi * freq * i / RATE) for i in range(int(seconds * RATE))]


def _write_wav(path: Path, samples: list[float], rate: int = RATE) -> None:
    data = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
    )
    path.write_bytes(header + data)


def _measure_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("speaker_measure_tool", TOOL / "measure.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["speaker_measure_tool"] = module
    spec.loader.exec_module(module)
    return module


def _setup(tmp_path: Path) -> dict[str, Any]:
    tool = _measure_module()
    fake = tmp_path / "fake"
    (fake / "sherpa_onnx").mkdir(parents=True)
    (fake / "sherpa_onnx" / "__init__.py").write_text(FAKE_SHERPA, encoding="utf-8")
    models = tmp_path / "models"
    pins: dict[str, tuple[int, str]] = {}
    for key, entry in tool.MODEL_FILES.items():
        target = models / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"fake weights of {key}".encode())
        data = target.read_bytes()
        pins[key] = (len(data), hashlib.sha256(data).hexdigest())
    inputs = tmp_path / "in"
    inputs.mkdir()
    for n in (1, 2, 3):
        _write_wav(inputs / f"sahip-{n:02d}.wav", _tone(2.0, 300.0))
    _write_wav(inputs / "konuk.wav", _tone(12.0, 900.0))
    # the constructed conversation: owner 0-3 s, guest 3.5-6.5 s, owner 7-9 s
    talk = _tone(3.0, 300.0) + [0.0] * 8000 + _tone(3.0, 900.0) + [0.0] * 8000 + _tone(2.0, 300.0)
    _write_wav(inputs / "konusma.wav", talk)
    _write_wav(inputs / "bad-rate.wav", _tone(1.0, 300.0), rate=8000)
    return {"models": models, "pins": pins, "fake": fake, "inputs": inputs}


def _jobs(inputs: Path) -> list[dict[str, Any]]:
    return [
        *[
            {
                "kind": "embed",
                "model": MODEL,
                "path": str(inputs / f"sahip-{n:02d}.wav"),
                "label": f"sahip-{n:02d}",
            }
            for n in (1, 2, 3)
        ],
        {
            "kind": "embed",
            "model": MODEL,
            "path": str(inputs / "konuk.wav"),
            "label": "konuk",
            "chunk_s": 3.0,
        },
        {
            "kind": "score",
            "model": MODEL,
            "name": "same",
            "pairs": [["sahip-[0-9][0-9]", "sahip-[0-9][0-9]"]],
        },
        {
            "kind": "score",
            "model": MODEL,
            "name": "diff",
            "pairs": [["sahip-[0-9][0-9]", "konuk#*"]],
        },
        {
            "kind": "diarize",
            "model": MODEL,
            "segmenter": sm.SEGMENTER_ID,
            "path": str(inputs / "konusma.wav"),
            "label": "konusma",
            "num_speakers": 2,
        },
        {"kind": "embed", "model": MODEL, "path": str(inputs / "bad-rate.wav"), "label": "kotu"},
    ]


def _run(
    tmp_path: Path, setup: dict[str, Any], *, block: bool = False, pins: dict | None = None
) -> tuple[int, list[dict[str, Any]], str]:
    env = dict(os.environ)
    path = [str(setup["fake"])]
    if block:
        site = tmp_path / "site"
        site.mkdir(exist_ok=True)
        (site / "sitecustomize.py").write_text(BLOCK_SOCKETS, encoding="utf-8")
        path.insert(0, str(site))
        env["SPK_SITE_MARKER"] = str(tmp_path / "site-ran.txt")
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(path),
            "SPK_TOOL_DIR": str(TOOL),
            "SPK_PINS": json.dumps(pins if pins is not None else setup["pins"]),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    stdin = "".join(json.dumps(job) + "\n" for job in _jobs(setup["inputs"]))
    child = subprocess.run(  # noqa: S603 - this interpreter on the tool under test
        [
            sys.executable,
            "-c",
            RUNNER,
            "measure",
            "--models-dir",
            str(setup["models"]),
            "--model",
            MODEL,
            "--threads",
            "2",
            "--segmenter",
        ],
        input=stdin.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=RUN_DEADLINE_S,
        check=False,
    )
    out = child.stdout.decode("utf-8")
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return child.returncode, rows, out


def _without_timing(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = []
    for row in rows:
        row = {k: v for k, v in row.items() if k not in TIMING_KEYS and k != "cpu"}
        clean.append(row)
    return clean


# ------------------------------------------------------------------- tests


def test_end_to_end_two_tones_give_load_score_and_diarize_lines(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    code, rows, out = _run(tmp_path, setup)
    assert code == 0, out
    load = rows[0]
    assert load["kind"] == "load"
    assert set(load["load_ms"]) == {MODEL, sm.SEGMENTER_ID}
    assert load["threads"] == 2
    assert "peak_rss_mb" in load and "cpu" in load
    embeds = [r for r in rows if r["kind"] == "embed" and "error" not in r]
    assert [r["label"] for r in embeds] == ["sahip-01", "sahip-02", "sahip-03", "konuk"]
    assert embeds[0]["audio_ms"] == 2000.0
    assert embeds[3]["chunks"] == 4  # 12 s in 3 s chunks
    scores = {r["name"]: r for r in rows if r["kind"] == "score"}
    assert scores["same"]["n"] == 3  # 3 sentences -> 3 unordered pairs
    assert all(s > 0.99 for s in scores["same"]["scores"])
    assert scores["diff"]["n"] == 12  # 3 sentences x 4 guest chunks
    assert all(s < 0.01 for s in scores["diff"]["scores"])
    [diarize] = [r for r in rows if r["kind"] == "diarize"]
    assert diarize["audio_ms"] == 9000.0
    assert diarize["speakers_found"] == 2
    hypothesis = [
        sm.Segment(s["start_s"], s["end_s"], str(s["speaker"])) for s in diarize["segments"]
    ]
    truth = [
        sm.Segment(0.0, 3.0, "sahip"),
        sm.Segment(3.5, 6.5, "konuk"),
        sm.Segment(7.0, 9.0, "sahip"),
    ]
    assert sm.der(truth, hypothesis, collar_s=sm.COLLAR_S).der_pct == 0.0
    # a WAV that is not 16 kHz mono PCM16 is a refused job, never converted
    [bad] = [r for r in rows if r.get("label") == "kotu"]
    assert "16 kHz" in bad["error"]


def test_with_sockets_blocked_the_run_is_unchanged(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    code, rows, out = _run(tmp_path, setup)
    blocked_code, blocked_rows, blocked_out = _run(tmp_path, setup, block=True)
    assert (tmp_path / "site-ran.txt").read_text(encoding="utf-8") == "sockets blocked"
    assert (code, blocked_code) == (0, 0), blocked_out
    assert _without_timing(blocked_rows) == _without_timing(rows)


def test_a_weight_hash_mismatch_exits_3_and_prints_no_result_line(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    tool = _measure_module()
    entry = tool.MODEL_FILES[MODEL]
    (setup["models"] / entry.path).write_bytes(b"replaced weights")
    code, rows, out = _run(tmp_path, setup)
    assert code == 3
    assert [r for r in rows if r.get("kind") in {"load", "embed", "score", "diarize"}] == []
    assert any(r.get("error") == "weight_hash_mismatch" for r in rows)


def test_no_embed_line_ever_contains_a_vector(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    code, rows, out = _run(tmp_path, setup)
    assert code == 0
    embed_lines = [line for line in out.splitlines() if '"kind": "embed"' in line]
    assert len(embed_lines) == 5
    float_run = re.compile(r"\[\s*-?\d+(\.\d+)?(e-?\d+)?(\s*,\s*-?\d+(\.\d+)?(e-?\d+)?){3,}\s*\]")
    for line in embed_lines:
        assert not float_run.search(line), line
        lowered = line.lower()
        assert "vector" not in lowered and "embedding" not in lowered, line


def test_the_splice_command_cuts_the_plan_into_one_wav(tmp_path: Path) -> None:
    setup = _setup(tmp_path)
    plan = sm.splice_plan(6.0, 12.0, seed=sm.SPLICE_SEED)
    splice = {
        "sample_rate": RATE,
        "sources": {
            "sahip": [str(setup["inputs"] / f"sahip-{n:02d}.wav") for n in (1, 2, 3)],
            "konuk": [str(setup["inputs"] / "konuk.wav")],
        },
        "segments": [
            {
                "speaker": s.speaker,
                "start_s": s.start_s,
                "duration_s": s.duration_s,
                "gap_after_s": s.gap_after_s,
                "source_start_s": s.source_start_s,
            }
            for s in plan
        ],
    }
    plan_file = tmp_path / "splice.json"
    plan_file.write_text(json.dumps(splice), encoding="utf-8")
    target = tmp_path / "out" / "eklenti.wav"
    target.parent.mkdir()
    child = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(TOOL / "measure.py"),
            "splice",
            "--plan",
            str(plan_file),
            "--out",
            str(target),
        ],
        capture_output=True,
        timeout=RUN_DEADLINE_S,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    import wave

    with wave.open(str(target), "rb") as handle:
        frames = handle.getnframes()
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (1, 2, RATE)
    expected = sum(round(s.duration_s * RATE) + round(s.gap_after_s * RATE) for s in plan)
    assert frames == expected
    row = json.loads(child.stdout.decode("utf-8").strip())
    assert row["kind"] == "splice" and row["segments"] == len(plan)


def test_cosine_is_speaker_py_arithmetic_line_for_line() -> None:
    tool = _measure_module()

    def body(function: Any) -> list[str]:
        lines = [line.strip() for line in inspect.getsource(function).splitlines()]
        return [line for line in lines if line and not line.startswith("#")][-6:]

    assert body(tool.cosine_similarity) == body(speaker.cosine_similarity)
    a = [0.3, -0.2, 0.9, 0.1]
    b = [0.25, -0.1, 0.8, 0.4]
    assert tool.cosine_similarity(a, b) == speaker.cosine_similarity(a, b)
    assert tool.cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_the_model_table_is_the_plans_pins() -> None:
    tool = _measure_module()
    plan = PLAN.read_text(encoding="utf-8")
    assert set(tool.MODEL_FILES) == {*sm.MODEL_IDS, sm.SEGMENTER_ID, "pyannote-seg-3-0-license"}
    assert tool.EMBEDDING_IDS == sm.MODEL_IDS
    assert tool.SEGMENTER_ID == sm.SEGMENTER_ID
    names = {Path(entry.path).name for entry in tool.MODEL_FILES.values()}
    assert {
        "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
        "3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx",
        "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
        "model.onnx",
    } <= names
    plan_flat = plan.replace(" ", "")
    for entry in tool.MODEL_FILES.values():
        assert re.fullmatch(r"[0-9a-f]{64}", entry.sha256)
        assert entry.bytes > 0
        assert entry.sha256 in plan, entry.path
        assert entry.url in plan, entry.url
        assert f"|{entry.bytes}|" in plan_flat or str(entry.bytes) in plan_flat, entry.path


def test_fetch_models_deletes_a_mismatching_download_and_exits_3(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("speaker_fetch_models", TOOL / "fetch_models.py")
    assert spec is not None and spec.loader is not None
    sys.path.insert(0, str(TOOL))
    try:
        fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetch)
    finally:
        sys.path.remove(str(TOOL))
    calls: list[str] = []

    def opener(url: str, target: Path) -> None:
        calls.append(url)
        target.write_bytes(b"not the pinned bytes")

    models = tmp_path / "models"
    code = fetch.fill(models, opener=opener)
    assert code == 3
    assert calls and all(url in PLAN.read_text(encoding="utf-8") for url in calls)
    assert [p for p in models.rglob("*") if p.is_file()] == []
