"""Speaker-engine measurement, the pure half (speaker-engine-measure): MEASUREMENT ONLY.

Nothing here is wired into the application: no route, no service and no profile imports this
module, and it imports no numpy, no sherpa-onnx and no FastAPI. The container half is
``tools/speaker-measure/measure.py`` (sherpa-onnx in its own image, ``--network none``); the
driver is ``scripts/voice/speaker-compare.ps1``. This module

- builds the container's job list from the staged recordings (``prepare``),
- turns the container's JSON lines into numbers (``merge``): EER with its threshold, the
  counts against the bands of :class:`app.voice.speaker.SpeakerThresholds` (read from that
  class at call time, never retyped), DER with a collar and the best speaker mapping, RTF and
  percentiles, and
- writes ``docs/evidence/speaker-measure.json`` + ``.md`` (``render_tr``) and the two listening
  timelines under ``%LOCALAPPDATA%/PagentOS/speaker-measure/<label>/`` - never inside the
  repository.

The evidence holds scores, counts and timings only: never a vector, never audio, never a
transcript, never the guest's name (the guest is ``konuk``). Team plan:
``team/plans/speaker-engine-integration-plan.md``.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import wave
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any

from app.voice.speaker import SpeakerThresholds

SCHEMA_VERSION = "1.0"
REPO_ROOT = Path(__file__).resolve().parents[4]
SAMPLE_RATE = 16_000
COLLAR_S = 0.25
SPLICE_SEED = 20261006
CHUNK_S = 3.0
SEGMENTER_ID = "pyannote-seg-3-0"
#: The embedding models, in table order; the same ids as ``tools/speaker-measure/measure.py``.
MODEL_IDS = ("campplus-zh-en-advanced", "eres2netv2-zh-cn", "campplus-voxceleb")
MODEL_NAMES = {
    "campplus-zh-en-advanced": "CAM++ zh+en advanced (192)",
    "eres2netv2-zh-cn": "ERes2NetV2 zh-cn (192)",
    "campplus-voxceleb": "CAM++ VoxCeleb (512)",
}
REASON_NO_CONSENT = "rızasız: ölçülmedi"
REASON_NO_GUEST = "konuk kaydı yok: ölçülmedi"
REASON_NOT_SELECTED = "seçilmedi"
REASON_NO_REFERENCE = "DER: referans yok, ölçülmedi"
REAL_CPX32_NOT_RUN = "cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)"
PLAN_STOPS = (
    'STOP-1 (gate): upstream `pyannote/segmentation-3.0` is gated (`gated: "auto"`, HF account '
    "+ contact-info form). - Ölçülen dosya k2-fsa'nın kapısız MIT ONNX kopyası; kart bölütleyiciyi "
    "ölçüme koydu (benimseme kartı bunu yeniden okur).",
    "STOP-CHECK-2 (training data, not licence class): eğitim verisinde yalnız-araştırma setleri "
    "var; özel, tek sahipli ölçüm için kabul, benimsemeden önce yeniden okunur.",
)
SENTENCE_GLOB = "sahip-[0-9][0-9]"
LONG_LABEL = "sahip-uzun"
GUEST_LABEL = "konuk"

#: The evidence JSON, key by key (a test walks it: no key names a vector, an embedding, audio
#: or a transcript).
MODEL_ROW_SCHEMA: dict[str, str] = {
    "model": "str",
    "name": "str",
    "status": "RAN | LEFT_OUT",
    "reason": "str | null",
    "same_n": "int | null",
    "same_below_accept": "int | null",
    "diff_n": "int | null",
    "diff_above_reject": "int | null",
    "uncertain_share": "float | null",
    "eer_pct": "float | null",
    "eer_threshold": "float | null",
    "guest_reason": "str | null",
    "embed_rtf_p50": "float | null",
    "embed_rtf_p95": "float | null",
    "files_timed": "int | null",
    "peak_rss_mb": "float | null",
    "load_ms": "float | null",
    "job_errors": "int | null",
}
DIAR_ROW_SCHEMA: dict[str, str] = {
    "segmenter": "str",
    "model": "str",
    "input": "eklenti | konusma",
    "clusters": "2 | esik",
    "status": "RAN | LEFT_OUT",
    "reason": "str | null",
    "der_pct": "float | null",
    "der_reason": "str | null",
    "speakers_found": "int | null",
    "segments_n": "int | null",
    "rtf": "float | null",
    "input_s": "float | null",
}
EVIDENCE_SCHEMA: dict[str, Any] = {
    "schema_version": "str",
    "tool": "str",
    "thresholds": {"owner_accept": "float", "not_owner_max": "float"},
    "collar_s": "float",
    "plan_stops": ["str"],
    "not_run": ["str"],
    "verdict_tr": "str",
    "shapes": [
        {
            "label": "str",
            "proxy": "bool",
            "threads": "int",
            "cpus_limit": "str",
            "cpu": "str",
            "image": "str",
            "measured_at": "str",
            "consent": "bool",
            "voices": "str",
            "inputs": {
                "sahip_cumle": "int",
                "sahip_uzun_s": "float | null",
                "konuk_s": "float | null",
                "konusma_s": "float | null",
            },
            "listen_dir": "str",
            "models": [MODEL_ROW_SCHEMA],
            "diarization": [DIAR_ROW_SCHEMA],
        }
    ],
}


# ------------------------------------------------------------------------- EER


@dataclass(frozen=True, slots=True)
class Eer:
    eer_pct: float
    threshold: float | None


def eer(same: Sequence[float], diff: Sequence[float]) -> Eer:
    """Equal error rate by a threshold sweep over every observed score.

    Accept = ``score >= t``. FRR(t) = share of same-speaker scores below t; FAR(t) = share of
    different-speaker scores at or above t. The EER is taken where the two curves CROSS: the t
    with the smallest |FAR - FRR| (ties: the lower t), reported as (FAR + FRR) / 2 there. It is
    not the minimum of (FAR + FRR) / 2, which can sit far from the crossing.
    """
    if not same or not diff:
        raise ValueError("EER needs at least one same-speaker and one different-speaker score")
    best: tuple[float, float, float] | None = None
    for threshold in [*sorted(set(same) | set(diff)), math.inf]:
        frr = sum(1 for s in same if s < threshold) / len(same)
        far = sum(1 for d in diff if d >= threshold) / len(diff)
        gap = abs(far - frr)
        if best is None or gap < best[0]:
            best = (gap, threshold, (far + frr) / 2.0)
    assert best is not None
    return Eer(round(best[2] * 100.0, 4), None if math.isinf(best[1]) else round(best[1], 6))


# ------------------------------------------------------------------------- DER


@dataclass(frozen=True, slots=True)
class Segment:
    start_s: float
    end_s: float
    speaker: str


@dataclass(frozen=True, slots=True)
class Der:
    der_pct: float | None
    missed_s: float
    false_alarm_s: float
    confusion_s: float
    scored_s: float
    mapping: dict[str, str]


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000.0))


def der(
    reference: Sequence[Segment], hypothesis: Sequence[Segment], *, collar_s: float = COLLAR_S
) -> Der:
    """Diarisation error rate: (missed + false alarm + confusion) / scored reference speech.

    ``collar_s`` around every reference boundary is not scored (NIST md-eval's collar). The
    hypothesis labels are arbitrary, so every one-to-one mapping of hypothesis speakers onto
    reference speakers is tried (two speakers: both mappings) and the lowest error is kept.
    """
    ref = [(_ms(s.start_s), _ms(s.end_s), s.speaker) for s in reference if s.end_s > s.start_s]
    hyp = [(_ms(s.start_s), _ms(s.end_s), s.speaker) for s in hypothesis if s.end_s > s.start_s]
    collar = _ms(collar_s)
    zones = (
        [(max(0, b - collar), b + collar) for start, end, _ in ref for b in (start, end)]
        if collar > 0
        else []
    )
    points = sorted(
        {p for start, end, _ in ref + hyp for p in (start, end)} | {p for z in zones for p in z}
    )
    pieces: list[tuple[int, frozenset[str], frozenset[str]]] = []
    for left, right in zip(points, points[1:], strict=False):
        middle = (left + right) / 2.0
        if any(z0 <= middle < z1 for z0, z1 in zones):
            continue
        spoken = frozenset(who for start, end, who in ref if start <= middle < end)
        heard = frozenset(who for start, end, who in hyp if start <= middle < end)
        if spoken or heard:
            pieces.append((right - left, spoken, heard))
    ref_labels = sorted({who for _, _, who in ref})
    hyp_labels = sorted({who for _, _, who in hyp})
    scored = sum(width * len(spoken) for width, spoken, _ in pieces)
    best: tuple[int, int, int, int, dict[str, str]] | None = None
    for choice in product([None, *hyp_labels], repeat=len(ref_labels)):
        chosen = [h for h in choice if h is not None]
        if len(chosen) != len(set(chosen)):
            continue
        mapping = {h: r for r, h in zip(ref_labels, choice, strict=True) if h is not None}
        missed = false_alarm = confusion = 0
        for width, spoken, heard in pieces:
            mapped = {mapping[h] for h in heard if h in mapping}
            correct = len(spoken & mapped)
            missed += max(0, len(spoken) - len(heard)) * width
            false_alarm += max(0, len(heard) - len(spoken)) * width
            confusion += (min(len(spoken), len(heard)) - correct) * width
        total = missed + false_alarm + confusion
        if best is None or total < best[0]:
            best = (total, missed, false_alarm, confusion, mapping)
    assert best is not None
    total, missed, false_alarm, confusion, mapping = best
    return Der(
        round(100.0 * total / scored, 4) if scored else None,
        round(missed / 1000.0, 3),
        round(false_alarm / 1000.0, 3),
        round(confusion / 1000.0, 3),
        round(scored / 1000.0, 3),
        mapping,
    )


# ----------------------------------------------------------------------- bands


@dataclass(frozen=True, slots=True)
class Bands:
    owner_accept: float
    not_owner_max: float
    same_below_accept: int
    same_total: int
    diff_above_reject: int
    diff_total: int
    uncertain: int
    uncertain_share: float | None


def band_counts(same: Sequence[float], diff: Sequence[float]) -> Bands:
    """Counts against ``verify_speaker``'s bands, read from ``SpeakerThresholds`` NOW.

    OWNER needs ``score >= owner_accept``, NOT_OWNER needs ``score <= not_owner_max``
    (``app/voice/speaker.py`` verify_speaker). So a same-speaker score below owner_accept would
    not be OWNER and a different-speaker score above not_owner_max would not be NOT_OWNER; a
    score strictly between the two is UNCERTAIN.
    """
    bands = SpeakerThresholds()
    scores = [*same, *diff]
    uncertain = sum(1 for s in scores if bands.not_owner_max < s < bands.owner_accept)
    return Bands(
        bands.owner_accept,
        bands.not_owner_max,
        sum(1 for s in same if s < bands.owner_accept),
        len(same),
        sum(1 for d in diff if d > bands.not_owner_max),
        len(diff),
        uncertain,
        round(uncertain / len(scores), 4) if scores else None,
    )


# ----------------------------------------------------------- RTF, percentiles


def rtf(wall_ms: float, input_ms: float) -> float | None:
    """Real-time factor: processing time / speech time (< 1 is faster than real time)."""
    if input_ms <= 0:
        return None
    return round(wall_ms / input_ms, 4)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (the value at rank ceil(q/100 * n))."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return ordered[rank - 1]


# ----------------------------------------------------------------- splice plan


@dataclass(frozen=True, slots=True)
class SpliceSegment:
    speaker: str
    start_s: float
    duration_s: float
    gap_after_s: float
    source_start_s: float


MAX_SEGMENTS = 80


def splice_plan(owner_s: float, guest_s: float, *, seed: int = SPLICE_SEED) -> list[SpliceSegment]:
    """The schedule of the SPLICED conversation: owner and guest alternate (owner first) in
    turns of 2-8 s with 0.3-1.0 s of silence after each, each source read straight through
    from its start, until the next speaker has less than 2 s left. Ground truth is exact by
    construction; the audio cut itself happens in the tool (``measure.py splice``)."""
    rng = random.Random(seed)
    length = {"sahip": owner_s, GUEST_LABEL: guest_s}
    used = {"sahip": 0.0, GUEST_LABEL: 0.0}
    clock = 0.0
    plan: list[SpliceSegment] = []
    for index in range(MAX_SEGMENTS):
        who = "sahip" if index % 2 == 0 else GUEST_LABEL
        duration = round(rng.uniform(2.0, 8.0), 2)
        gap = round(rng.uniform(0.3, 1.0), 2)
        left = length[who] - used[who]
        if left < 2.0:
            break
        duration = min(duration, math.floor(left * 100.0) / 100.0)
        plan.append(SpliceSegment(who, round(clock, 3), duration, gap, round(used[who], 3)))
        used[who] += duration
        clock += duration + gap
    return plan


def reference_from_plan(plan: Sequence[SpliceSegment]) -> list[Segment]:
    return [Segment(s.start_s, round(s.start_s + s.duration_s, 3), s.speaker) for s in plan]


# ---------------------------------------------------------------- report rows


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def left_out_row(model: str, reason: str) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(MODEL_ROW_SCHEMA)
    row.update({"model": model, "name": MODEL_NAMES.get(model, model), "status": "LEFT_OUT"})
    row["reason"] = reason
    return row


def model_row(
    model: str,
    *,
    same: Sequence[float],
    diff: Sequence[float] | None,
    embed_runs: Sequence[tuple[float, float]],
    load_ms: float | None,
    peak_rss_mb: float | None,
    guest_reason: str = REASON_NO_CONSENT,
    job_errors: int = 0,
) -> dict[str, Any]:
    """One embedding model's row. ``embed_runs`` = (wall_ms, input_ms) per embedded file;
    ``diff`` None = no guest was measured (``guest_reason`` says why)."""
    bands = band_counts(same, diff or [])
    rates = [r for r in (rtf(wall, length) for wall, length in embed_runs) if r is not None]
    measured = None
    if same and diff:
        measured = eer(same, diff)
    row = left_out_row(model, "")
    row.update(
        {
            "status": "RAN",
            "reason": None,
            "same_n": bands.same_total,
            "same_below_accept": bands.same_below_accept,
            "diff_n": bands.diff_total if diff is not None else None,
            "diff_above_reject": bands.diff_above_reject if diff is not None else None,
            "uncertain_share": bands.uncertain_share,
            "eer_pct": measured.eer_pct if measured else None,
            "eer_threshold": measured.threshold if measured else None,
            "guest_reason": None if diff else guest_reason,
            "embed_rtf_p50": percentile(rates, 50),
            "embed_rtf_p95": percentile(rates, 95),
            "files_timed": len(rates),
            "peak_rss_mb": _round(peak_rss_mb, 1),
            "load_ms": _round(load_ms, 1),
            "job_errors": job_errors,
        }
    )
    return row


def diar_row(
    model: str,
    source: str,
    clusters: str,
    *,
    reason: str | None = None,
    der_pct: float | None = None,
    der_reason: str | None = None,
    speakers_found: int | None = None,
    segments_n: int | None = None,
    rate: float | None = None,
    input_s: float | None = None,
) -> dict[str, Any]:
    return {
        "segmenter": SEGMENTER_ID,
        "model": model,
        "input": source,
        "clusters": clusters,
        "status": "LEFT_OUT" if reason else "RAN",
        "reason": reason,
        "der_pct": der_pct,
        "der_reason": der_reason,
        "speakers_found": speakers_found,
        "segments_n": segments_n,
        "rtf": rate,
        "input_s": input_s,
    }


def build_shape(
    *,
    label: str,
    threads: int,
    cpus_limit: str,
    cpu: str,
    image: str,
    consent: bool,
    synthetic: bool,
    inputs: dict[str, Any],
    models: list[dict[str, Any]],
    diarization: list[dict[str, Any]],
    listen_dir: str,
) -> dict[str, Any]:
    return {
        "label": label,
        "proxy": bool(cpus_limit) or label.startswith("cpx32-bicimi"),
        "threads": threads,
        "cpus_limit": cpus_limit,
        "cpu": cpu,
        "image": image,
        "measured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "consent": consent,
        "voices": "sentetik ses" if synthetic else "gerçek ses",
        "inputs": inputs,
        "listen_dir": listen_dir,
        "models": models,
        "diarization": diarization,
    }


def _tr(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


def verdict_tr(report: dict[str, Any]) -> str:
    """From the numbers only. Says plainly that adoption is not decided here."""
    parts: list[str] = []
    for shape in report["shapes"]:
        ran = [m for m in shape["models"] if m["status"] == "RAN"]
        scored = [m for m in ran if m["eer_pct"] is not None]
        if scored:
            low = min(scored, key=lambda m: (m["eer_pct"], MODEL_IDS.index(m["model"])))
            parts.append(
                f"{shape['label']}: sahibi konuktan en düşük EER ile ayıran model {low['model']} "
                f"(EER %{_tr(low['eer_pct'])}, eşik {_tr(low['eer_threshold'], 3)}; "
                f"{_tr(report['thresholds']['owner_accept'])} altında kalan aynı-kişi skoru "
                f"{low['same_below_accept']}/{low['same_n']}, "
                f"{_tr(report['thresholds']['not_owner_max'])} üstündeki farklı-kişi skoru "
                f"{low['diff_above_reject']}/{low['diff_n']})."
            )
        elif ran:
            parts.append(f"{shape['label']}: EER ölçülmedi ({ran[0]['guest_reason']}).")
        else:
            parts.append(f"{shape['label']}: hiçbir model çalışmadı.")
        rates = [m["embed_rtf_p95"] for m in ran if m["embed_rtf_p95"] is not None]
        rates += [d["rtf"] for d in shape["diarization"] if d["rtf"] is not None]
        if rates:
            reached = "ulaşıldı" if max(rates) < 1.0 else "ulaşılmadı"
            parts.append(
                f"{shape['label']}: gerçek zamana "
                f"(en yüksek RTF {_tr(max(rates), 3)} < 1) {reached}."
            )
    parts.append(
        "Benimseme kararı burada verilmez: bu sayılar yalnız ölçümdür. Konuğun sesi tek bir "
        "kişidir, bir nüfus değildir; tek konuğa karşı EER zayıf bir sayıdır."
    )
    return " ".join(parts)


def merge_report(existing: dict[str, Any] | None, shape: dict[str, Any]) -> dict[str, Any]:
    """Replace (or add) this shape's label; the rest of an earlier report is kept."""
    shapes = [s for s in (existing or {}).get("shapes", []) if s.get("label") != shape["label"]]
    shapes.append(shape)
    order = {"ev-pc": 0, "cpx32-bicimi": 1}
    shapes.sort(key=lambda s: (order.get(s["label"], 2), s["label"]))
    thresholds = SpeakerThresholds()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": "speaker-engine-measure",
        "thresholds": {
            "owner_accept": thresholds.owner_accept,
            "not_owner_max": thresholds.not_owner_max,
        },
        "collar_s": COLLAR_S,
        "plan_stops": list(PLAN_STOPS),
        "not_run": [REAL_CPX32_NOT_RUN],
        "shapes": shapes,
    }
    report["verdict_tr"] = verdict_tr(report)
    return report


# ---------------------------------------------------------------- the page


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


INPUT_NAMES = {"eklenti": "eklenti (doğal değil)", "konusma": "konuşma (doğal)"}


def render_tr(report: dict[str, Any], repo_root: Path | None = None) -> str:
    """docs/evidence/speaker-measure.md. A listening folder inside the repository is refused
    (and so the page can never name a path inside it)."""
    roots = {REPO_ROOT, *([repo_root] if repo_root is not None else [])}
    for shape in report["shapes"]:
        for root in roots:
            if _inside(Path(shape["listen_dir"]), root):
                raise ValueError(
                    f"listening folder of {shape['label']} is inside the repository: "
                    f"{shape['listen_dir']}"
                )
    accept = _tr(report["thresholds"]["owner_accept"])
    reject = _tr(report["thresholds"]["not_owner_max"])
    lines = [
        "# Ses-izi motoru ölçümü (speaker-engine-measure)",
        "",
        "YALNIZ ÖLÇÜM: `app/voice/speaker.py`'ye, konuşma kayıtlarına, hiçbir profile, ayarlara ve "
        "web'e dokunulmadı; API'ye bağımlılık eklenmedi. Modeller kendi konteynerinde "
        "(`pagentos-speaker-measure`, `pip install --require-hashes`), `--network none "
        "--read-only` ile, her dosyanın sha256'sı yüklemeden önce denetlenerek çalıştı.",
        f"Bantlar `SpeakerThresholds`'tan okundu: sahip kabul ≥ {accept}, sahip-değil ≤ {reject}; "
        "skor `speaker.py`'nin kosinüsüyle aynı aritmetik. DER yakası "
        f"{_tr(report['collar_s'])} s.",
        "",
        "## Karar (yalnız sayılardan)",
        "",
        report["verdict_tr"],
        "",
    ]
    for shape in report["shapes"]:
        kind = (
            "VEKİL (cpx32 biçimi, ev PC'sinde `--cpus "
            f"{shape['cpus_limit'] or '4'}`; sunucunun kendisi değil)"
            if shape["proxy"]
            else "ev PC'si (tüm çekirdekler)"
        )
        inputs = shape["inputs"]
        lines += [
            f"## {shape['label']}",
            "",
            f"- Tür: {kind}; CPU: {shape['cpu'] or '—'}; iş parçacığı: {shape['threads']}; "
            f"imaj: `{shape['image']}`; ölçüm: {shape['measured_at']}.",
            f"- Sesler: {shape['voices']}; konuk rızası: "
            f"{'var (consent.json)' if shape['consent'] else 'yok - konuk okunmadı'}; "
            f"sahip cümlesi: {inputs.get('sahip_cumle', 0)}; sahip uzun kaydı: "
            f"{_tr(inputs.get('sahip_uzun_s'), 1)} s; konuk: {_tr(inputs.get('konuk_s'), 1)} s; "
            f"doğal konuşma: {_tr(inputs.get('konusma_s'), 1)} s.",
            "",
            f"Model | EER % | EER eşiği | aynı kişi < {accept} (n/N) | farklı kişi > {reject} "
            "(n/N) | belirsiz pay | gömme RTF p95 | tepe bellek MB | yükleme ms",
            "---|---|---|---|---|---|---|---|---",
        ]
        for m in shape["models"]:
            if m["status"] != "RAN":
                lines.append(f"{m['model']} | {m['reason']} | " + " | ".join(["—"] * 7))
                continue
            guest = m["guest_reason"]
            lines.append(
                f"{m['model']} | {guest or _tr(m['eer_pct'])} | "
                f"{guest or _tr(m['eer_threshold'], 3)} | "
                f"{m['same_below_accept']}/{m['same_n']} | "
                f"{guest or str(m['diff_above_reject']) + '/' + str(m['diff_n'])} | "
                f"{_tr(m['uncertain_share'], 3)} | {_tr(m['embed_rtf_p95'], 3)} | "
                f"{_tr(m['peak_rss_mb'], 0)} | {_tr(m['load_ms'], 0)}"
            )
        lines += [
            "",
            f"Diyarizasyon ({SEGMENTER_ID} bölütleyici × gömme modeli):",
            "",
            "Bölütleyici | model | girdi | küme | DER % | bulunan konuşmacı | RTF",
            "---|---|---|---|---|---|---",
        ]
        for d in shape["diarization"]:
            source = INPUT_NAMES.get(d["input"], d["input"])
            if d["status"] != "RAN":
                lines.append(
                    f"{d['segmenter']} | {d['model']} | {source} | {d['clusters']} | "
                    f"{d['reason']} | — | —"
                )
                continue
            lines.append(
                f"{d['segmenter']} | {d['model']} | {source} | {d['clusters']} | "
                f"{d['der_reason'] or _tr(d['der_pct'])} | {d['speakers_found']} | "
                f"{_tr(d['rtf'], 3)}"
            )
        if not shape["diarization"]:
            lines.append(f"{SEGMENTER_ID} | — | — | — | ölçülmedi | — | —")
        lines += [
            "",
            f"Dinleme (deponun DIŞINDA): `{shape['listen_dir']}` - `eklenti.wav` + "
            "`eklenti-zaman.txt`, `konusma.wav` + `konusma-zaman.txt` (varsa).",
            "",
        ]
    lines += [
        "## Notlar",
        "",
        f"- {REAL_CPX32_NOT_RUN}",
        "- 'eklenti' konuşma doğal değildir: sahip ve konuk kayıtlarından 2-8 s'lik dönüşler, "
        "0,3-1,0 s sessizlikle sabit tohumla eklendi; doğruluk yapı gereği bilinir, örtüşme yok.",
        "- 'küme 2' = konuşmacı sayısı verildi (ana satır); 'esik' = sayı verilmedi, eşik 0,5.",
        "- EER tek bir konuğa karşıdır: konuğun sesi tek bir kişidir, bir nüfus değildir.",
        "- Yayımlanmış EER'ler PyTorch özgünlerine aittir; bu satırlar ONNX dosyalarınındır.",
        *[f"- Plan: {line}" for line in report["plan_stops"]],
        "- Silindi: vektörler yalnız konteynerin belleğindeydi ve koşu bitince silindi; hiçbir "
        "profile yazılmadı; depoda ses yok; indirilen kayıtlar ve geçici klasörler silindi. "
        "Dinleme dosyaları yalnız yukarıdaki klasörde, sahip dinledikten sonra siler.",
        "",
        "Benimseme ve bağlantı (mikrofon → bölütleyici → STT → kayıt) ayrı sahip kararlarıdır.",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------- prepare


class InputError(Exception):
    """A recording or folder the measurement refuses (exit 2)."""


def wav_seconds(path: Path) -> float:
    """Seconds of a 16 kHz mono PCM16 WAV; anything else is refused, never converted."""
    try:
        with wave.open(str(path), "rb") as handle:
            shape = (handle.getnchannels(), handle.getsampwidth(), handle.getframerate())
            frames = handle.getnframes()
    except (wave.Error, EOFError) as error:
        raise InputError(f"{path.name}: not a readable WAV ({error})") from error
    if shape != (1, 2, SAMPLE_RATE):
        raise InputError(
            f"{path.name}: needs 16 kHz mono 16-bit PCM, got {shape[2]} Hz, "
            f"{shape[0]} channel(s), {8 * shape[1]}-bit"
        )
    return frames / SAMPLE_RATE


def _jsonl(rows: Iterable[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def prepare(staged: Path, work: Path, models: Sequence[str], *, seed: int = SPLICE_SEED) -> dict:
    """Write ``jobs-<model>.jsonl`` (container paths) and, when the owner and the guest are both
    there, ``splice.json`` beside the staged files. Returns what was found."""
    sentences = sorted(p for p in staged.glob("sahip-[0-9][0-9].wav"))
    long_file = staged / f"{LONG_LABEL}.wav"
    guest_file = staged / f"{GUEST_LABEL}.wav"
    talk_file = staged / "konusma.wav"
    sentence_s = [wav_seconds(p) for p in sentences]
    inputs: dict[str, Any] = {
        "sahip_cumle": len(sentences),
        "sahip_uzun_s": round(wav_seconds(long_file), 2) if long_file.is_file() else None,
        "konuk_s": round(wav_seconds(guest_file), 2) if guest_file.is_file() else None,
        "konusma_s": round(wav_seconds(talk_file), 2) if talk_file.is_file() else None,
    }
    if not sentences and inputs["sahip_uzun_s"] is None:
        raise InputError("no owner recording: neither sentences nor owner.wav")
    owner_set = SENTENCE_GLOB if sentences else f"{LONG_LABEL}#*"
    splice = None
    if inputs["konuk_s"] is not None:
        owner_sources = (
            [f"/in/{LONG_LABEL}.wav"]
            if long_file.is_file()
            else [f"/in/{p.name}" for p in sentences]
        )
        owner_s = inputs["sahip_uzun_s"] if long_file.is_file() else sum(sentence_s)
        plan = splice_plan(owner_s, inputs["konuk_s"], seed=seed)
        splice = {
            "sample_rate": SAMPLE_RATE,
            "seed": seed,
            "sources": {"sahip": owner_sources, GUEST_LABEL: [f"/in/{GUEST_LABEL}.wav"]},
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
        (staged / "splice.json").write_text(json.dumps(splice, indent=1), encoding="utf-8")
    for model in models:
        jobs: list[dict[str, Any]] = [
            {"kind": "embed", "model": model, "path": f"/in/{p.name}", "label": p.stem}
            for p in sentences
        ]
        if long_file.is_file():
            jobs.append(
                {
                    "kind": "embed",
                    "model": model,
                    "path": f"/in/{long_file.name}",
                    "label": LONG_LABEL,
                    "chunk_s": CHUNK_S,
                }
            )
        if guest_file.is_file():
            jobs.append(
                {
                    "kind": "embed",
                    "model": model,
                    "path": f"/in/{guest_file.name}",
                    "label": GUEST_LABEL,
                    "chunk_s": CHUNK_S,
                }
            )
        jobs.append(
            {"kind": "score", "model": model, "name": "same", "pairs": [[owner_set, owner_set]]}
        )
        if sentences and long_file.is_file():
            jobs.append(
                {
                    "kind": "score",
                    "model": model,
                    "name": "same_long",
                    "pairs": [[f"{LONG_LABEL}#*", SENTENCE_GLOB]],
                }
            )
        if guest_file.is_file():
            jobs.append(
                {
                    "kind": "score",
                    "model": model,
                    "name": "diff",
                    "pairs": [[owner_set, f"{GUEST_LABEL}#*"]],
                }
            )
        for source, path in (
            ("eklenti", "/splice/eklenti.wav" if splice else None),
            ("konusma", "/in/konusma.wav" if talk_file.is_file() else None),
        ):
            if path is None:
                continue
            for speakers in (2, None):
                jobs.append(
                    {
                        "kind": "diarize",
                        "model": model,
                        "segmenter": SEGMENTER_ID,
                        "path": path,
                        "label": source,
                        "num_speakers": speakers,
                    }
                )
        (work / f"jobs-{model}.jsonl").write_text(_jsonl(jobs), encoding="utf-8")
    summary = {"inputs": inputs, "splice": splice is not None, "models": list(models)}
    (work / "inputs.json").write_text(json.dumps(summary), encoding="utf-8")
    return summary


# ------------------------------------------------------------------ merge


@dataclass
class ContainerRun:
    load_ms: float | None
    peak_rss_mb: float | None
    cpu: str
    embeds: list[tuple[float, float]]
    scores: dict[str, list[float]]
    diarize: list[dict[str, Any]]
    errors: int


def parse_container_output(lines: Iterable[str]) -> ContainerRun:
    run = ContainerRun(None, None, "", [], {}, [], 0)
    peaks: list[float] = []
    for raw in lines:
        line = raw.strip()
        if not line.startswith("{"):
            continue
        row = json.loads(line)
        if isinstance(row.get("peak_rss_mb"), int | float):
            peaks.append(float(row["peak_rss_mb"]))
        kind = row.get("kind")
        if row.get("error"):
            run.errors += 1
            continue
        if kind == "load":
            run.load_ms = float(sum(row.get("load_ms", {}).values()))
            run.cpu = str(row.get("cpu") or "")
        elif kind == "embed":
            run.embeds.append((float(row["wall_ms"]), float(row["audio_ms"])))
        elif kind == "score":
            run.scores.setdefault(str(row["name"]), []).extend(float(s) for s in row["scores"])
        elif kind == "diarize":
            run.diarize.append(row)
    run.peak_rss_mb = max(peaks) if peaks else None
    return run


def _timeline(title: str, segments: Iterable[tuple[float, float, str]]) -> list[str]:
    out = [title]
    out += [f"  {_tr(start)} - {_tr(end)}  konuşmacı {who}" for start, end, who in segments]
    return out + [""]


def merge(args: argparse.Namespace) -> int:
    work: Path = args.work
    listen = Path(args.listen_dir)
    for root in {REPO_ROOT, args.repo_root}:
        if _inside(listen, root):
            print(f"listening folder is inside the repository: {listen}", file=sys.stderr)
            return 2
    summary = json.loads((work / "inputs.json").read_text(encoding="utf-8"))
    consent = args.consent == "yes"
    selected = [m for m in args.models.split(",") if m]
    splice_file = work / "in" / "splice.json"
    reference_plan: list[Segment] = []
    if splice_file.is_file():
        splice = json.loads(splice_file.read_text(encoding="utf-8"))
        reference_plan = reference_from_plan(
            [
                SpliceSegment(
                    s["speaker"],
                    s["start_s"],
                    s["duration_s"],
                    s["gap_after_s"],
                    s["source_start_s"],
                )
                for s in splice["segments"]
            ]
        )
    reference_file = work / "in" / "referans.json"
    talk_reference: list[Segment] | None = None
    if reference_file.is_file():
        talk_reference = [
            Segment(float(r["start_s"]), float(r["end_s"]), str(r["speaker"]))
            for r in json.loads(reference_file.read_text(encoding="utf-8"))
        ]
    guest_reason = REASON_NO_GUEST if consent else REASON_NO_CONSENT
    models: list[dict[str, Any]] = []
    diarization: list[dict[str, Any]] = []
    timelines: dict[str, list[str]] = {"eklenti": [], "konusma": []}
    if reference_plan:
        timelines["eklenti"] += _timeline(
            "doğru (yapı gereği):", ((s.start_s, s.end_s, s.speaker) for s in reference_plan)
        )
    cpu = ""
    for model in MODEL_IDS:
        failure = work / f"fail-{model}.json"
        output = work / f"out-{model}.jsonl"
        if model not in selected:
            reason = REASON_NOT_SELECTED
        elif failure.is_file():
            facts = json.loads(failure.read_text(encoding="utf-8-sig"))
            reason = (
                "çalışmadı: süre aşıldı"
                if facts.get("timed_out")
                else f"çalışmadı: çıkış {facts.get('exit')}"
            )
        elif not output.is_file():
            reason = "kurulu değil"
        else:
            reason = ""
        if reason:
            models.append(left_out_row(model, reason))
            diarization.append(diar_row(model, "eklenti", "2", reason=reason))
            continue
        run = parse_container_output(output.read_text(encoding="utf-8").splitlines())
        cpu = cpu or run.cpu
        same = run.scores.get("same", []) + run.scores.get("same_long", [])
        diff = run.scores.get("diff") if "diff" in run.scores else None
        models.append(
            model_row(
                model,
                same=same,
                diff=diff,
                embed_runs=run.embeds,
                load_ms=run.load_ms,
                peak_rss_mb=run.peak_rss_mb,
                guest_reason=guest_reason,
                job_errors=run.errors,
            )
        )
        if not consent:
            diarization.append(diar_row(model, "eklenti", "2", reason=REASON_NO_CONSENT))
            diarization.append(diar_row(model, "konusma", "2", reason=REASON_NO_CONSENT))
        for row in run.diarize:
            source = str(row["label"])
            clusters = "2" if row.get("num_speakers") == 2 else "esik"
            hypothesis = [
                Segment(float(s["start_s"]), float(s["end_s"]), str(s["speaker"]))
                for s in row["segments"]
            ]
            reference = reference_plan if source == "eklenti" else talk_reference
            measured = der(reference, hypothesis) if reference else None
            diarization.append(
                diar_row(
                    model,
                    source,
                    clusters,
                    der_pct=measured.der_pct if measured else None,
                    der_reason=None if measured else REASON_NO_REFERENCE,
                    speakers_found=len({s.speaker for s in hypothesis}),
                    segments_n=len(hypothesis),
                    rate=rtf(float(row["wall_ms"]), float(row["audio_ms"])),
                    input_s=round(float(row["audio_ms"]) / 1000.0, 2),
                )
            )
            timelines.setdefault(source, []).extend(
                _timeline(
                    f"{model}, küme {clusters}:",
                    ((s.start_s, s.end_s, s.speaker) for s in hypothesis),
                )
            )
    shape = build_shape(
        label=args.label,
        threads=args.threads,
        cpus_limit=args.cpus_limit,
        cpu=cpu,
        image=args.image,
        consent=consent,
        synthetic=args.synthetic == "yes",
        inputs=summary["inputs"],
        models=models,
        diarization=diarization,
        listen_dir=str(listen),
    )
    evidence_dir: Path = args.evidence_dir
    json_path = evidence_dir / "speaker-measure.json"
    existing = json.loads(json_path.read_text(encoding="utf-8")) if json_path.is_file() else None
    report = merge_report(existing, shape)
    markdown = render_tr(report, args.repo_root)  # refuses before anything is written
    listen.mkdir(parents=True, exist_ok=True)
    for source, lines in timelines.items():
        if lines:
            (listen / f"{source}-zaman.txt").write_text("\n".join(lines), encoding="utf-8")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (evidence_dir / "speaker-measure.md").write_text(markdown, encoding="utf-8")
    print(report["verdict_tr"])
    return 0


# ------------------------------------------------------------- command line


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="speaker_measure")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("prepare")
    make.add_argument("--in", dest="staged", required=True, type=Path)
    make.add_argument("--work", required=True, type=Path)
    make.add_argument("--models", default=",".join(MODEL_IDS))
    make.add_argument("--seed", type=int, default=SPLICE_SEED)
    done = commands.add_parser("merge")
    done.add_argument("--work", required=True, type=Path)
    done.add_argument("--label", required=True)
    done.add_argument("--threads", required=True, type=int)
    done.add_argument("--cpus-limit", default="")
    done.add_argument("--evidence-dir", required=True, type=Path)
    done.add_argument("--listen-dir", required=True)
    done.add_argument("--repo-root", required=True, type=Path)
    done.add_argument("--image", default="")
    done.add_argument("--consent", choices=("yes", "no"), required=True)
    done.add_argument("--synthetic", choices=("yes", "no"), default="no")
    done.add_argument("--models", default=",".join(MODEL_IDS))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "prepare":
        models = [m for m in args.models.split(",") if m]
        unknown = [m for m in models if m not in MODEL_IDS]
        if unknown:
            print(f"unknown model id(s): {unknown}; known: {list(MODEL_IDS)}", file=sys.stderr)
            return 2
        try:
            summary = prepare(args.staged, args.work, models, seed=args.seed)
        except InputError as error:
            print(f"refused: {error}", file=sys.stderr)
            return 2
        print(json.dumps(summary))
        return 0
    return merge(args)


if __name__ == "__main__":
    raise SystemExit(main())
