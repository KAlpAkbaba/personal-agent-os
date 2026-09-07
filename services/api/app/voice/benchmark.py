"""Voice provider benchmark harness (VOICE_SPEC §7 TTS, §9 STT).

Runs a set of candidate providers against the Turkish eval sets and emits a
structured report (JSON) plus a short Markdown summary. It MUST compare >= 2
providers so the "benchmark report exists / compares >= 2 providers" acceptance
evidence is produced deterministically and offline (the fakes do the work in
tests; real-key adapters plug into the exact same harness once an owner wires
keys).

Metrics:
- STT (per case): WER and CER vs the label, plus latency. Aggregated to mean WER
  / mean CER per provider, which ranks providers.
- TTS (per case): latency, output format, produced audio length, and stability /
  coverage capability metadata. (Perceptual quality is an OWNER-GATED blind A/B,
  VOICE_SPEC §8 — noted in the report, not scored here.)

Everything is pure/deterministic. Word- and char-error rates use a stdlib
Levenshtein (no external dependency).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.voice.datasets import STT_CASES, TTS_CASES, STTCase, TTSCase
from app.voice.providers import (
    STTProvider,
    TTSProvider,
    synthesize_wav,
    wav_duration_ms,
)

REPORT_SCHEMA_VERSION = "1.0"


# --------------------------------------------------------------- error rates


def _levenshtein(a: list[str] | str, b: list[str] | str) -> int:
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def levenshtein(a: list[str] | str, b: list[str] | str) -> int:
    """Edit distance over tokens (lists) or characters (strings); stdlib only."""
    return _levenshtein(a, b)


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref = reference.split()
    if not ref:
        return 0.0 if not hypothesis.split() else 1.0
    return _levenshtein(ref, hypothesis.split()) / len(ref)


def char_error_rate(reference: str, hypothesis: str) -> float:
    ref = reference.replace(" ", "")
    if not ref:
        return 0.0 if not hypothesis.replace(" ", "") else 1.0
    return _levenshtein(ref, hypothesis.replace(" ", "")) / len(ref)


# --------------------------------------------------------------------- reports


@dataclass(slots=True)
class BenchmarkReport:
    kind: str  # "stt" | "tts"
    schema_version: str
    generated_at: str
    providers: list[str]
    cases: list[dict[str, Any]]
    per_provider: dict[str, dict[str, Any]]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "providers": self.providers,
            "compares_provider_count": len(self.providers),
            "cases": self.cases,
            "per_provider": self.per_provider,
            "notes": self.notes,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _require_two(providers: list[Any], kind: str) -> None:
    names = [p.name for p in providers]
    if len(set(names)) < 2:
        raise ValueError(
            f"{kind} benchmark must compare >= 2 distinct providers, got {names}"
        )


# ------------------------------------------------------------------ STT bench


def run_stt_benchmark(
    providers: list[STTProvider], *, cases: tuple[STTCase, ...] = STT_CASES,
    language: str = "tr-TR",
) -> BenchmarkReport:
    _require_two(providers, "STT")
    case_rows: list[dict[str, Any]] = []
    agg: dict[str, dict[str, float]] = {p.name: {"wer": 0.0, "cer": 0.0, "latency_ms": 0.0}
                                        for p in providers}

    for case in cases:
        audio = synthesize_wav(case.reference)  # deterministic offline "recording"
        row: dict[str, Any] = {"case_id": case.case_id, "category": case.category,
                               "reference": case.reference, "results": {}}
        for provider in providers:
            res = provider.transcribe(audio, language=language)
            wer = word_error_rate(case.reference, res.text)
            cer = char_error_rate(case.reference, res.text)
            row["results"][provider.name] = {
                "hypothesis": res.text, "wer": round(wer, 4), "cer": round(cer, 4),
                "confidence": res.confidence, "latency_ms": res.latency_ms,
            }
            agg[provider.name]["wer"] += wer
            agg[provider.name]["cer"] += cer
            agg[provider.name]["latency_ms"] += res.latency_ms
        case_rows.append(row)

    n = len(cases) or 1
    per_provider = {
        name: {
            "mean_wer": round(v["wer"] / n, 4),
            "mean_cer": round(v["cer"] / n, 4),
            "mean_latency_ms": round(v["latency_ms"] / n, 3),
            "capabilities": next(p.capabilities().to_dict() for p in providers if p.name == name),
        }
        for name, v in agg.items()
    }
    ranked = sorted(per_provider, key=lambda k: per_provider[k]["mean_wer"])
    return BenchmarkReport(
        kind="stt", schema_version=REPORT_SCHEMA_VERSION, generated_at=_now(),
        providers=[p.name for p in providers], cases=case_rows, per_provider=per_provider,
        notes=[
            f"lowest mean WER: {ranked[0]} ({per_provider[ranked[0]]['mean_wer']})",
            "Audio is synthesized deterministically from labels (offline); real-mic "
            "WER/CER across desktop/laptop/headset/phone/noise (VOICE_SPEC §9) is "
            "OWNER-GATED and requires real recordings + provider keys.",
        ],
    )


# ------------------------------------------------------------------ TTS bench


def run_tts_benchmark(
    providers: list[TTSProvider], *, cases: tuple[TTSCase, ...] = TTS_CASES,
    voice: str = "default", fmt: str = "wav",
) -> BenchmarkReport:
    _require_two(providers, "TTS")
    case_rows: list[dict[str, Any]] = []
    agg: dict[str, dict[str, float]] = {
        p.name: {"latency_ms": 0.0, "audio_ms": 0.0, "ok": 0.0} for p in providers
    }

    for case in cases:
        row: dict[str, Any] = {"case_id": case.case_id, "category": case.category,
                               "char_count": len(case.text), "results": {}}
        for provider in providers:
            use_fmt = fmt if fmt in provider.capabilities().output_formats else \
                provider.capabilities().output_formats[0]
            res = provider.synthesize(case.text, voice=voice, fmt=use_fmt)
            audio_ms = res.duration_ms or wav_duration_ms(res.audio)
            row["results"][provider.name] = {
                "latency_ms": res.latency_ms, "output_format": res.audio_format,
                "audio_bytes": len(res.audio), "audio_ms": audio_ms,
                "valid_audio": res.audio[:4] == b"RIFF" if res.audio_format in ("wav", "pcm16")
                else bool(res.audio),
            }
            agg[provider.name]["latency_ms"] += res.latency_ms
            agg[provider.name]["audio_ms"] += audio_ms
            agg[provider.name]["ok"] += 1.0
        case_rows.append(row)

    n = len(cases) or 1
    per_provider = {
        name: {
            "cases": int(v["ok"]),
            "mean_latency_ms": round(v["latency_ms"] / n, 3),
            "mean_audio_ms": round(v["audio_ms"] / n, 1),
            "capabilities": next(p.capabilities().to_dict() for p in providers if p.name == name),
        }
        for name, v in agg.items()
    }
    return BenchmarkReport(
        kind="tts", schema_version=REPORT_SCHEMA_VERSION, generated_at=_now(),
        providers=[p.name for p in providers], cases=case_rows, per_provider=per_provider,
        notes=[
            "Capability/coverage + latency + audio length are measured objectively.",
            "Perceptual quality (naturalness, long-form stability, pronunciation of "
            "numbers/dates/mixed TR-EN) is an OWNER-GATED blind A/B (VOICE_SPEC §8) "
            "requiring real provider keys and the owner listening; not scored here.",
        ],
    )


# ------------------------------------------------------------- markdown summary


def report_to_markdown(report: BenchmarkReport) -> str:
    lines = [
        f"# Voice {report.kind.upper()} Benchmark",
        "",
        f"- Generated: {report.generated_at}",
        f"- Schema: {report.schema_version}",
        f"- Providers compared: {len(report.providers)} "
        f"({', '.join(report.providers)})",
        "",
    ]
    if report.kind == "stt":
        lines += ["| Provider | Mean WER | Mean CER | Mean latency (ms) |",
                  "|---|---|---|---|"]
        for name, m in sorted(report.per_provider.items(), key=lambda kv: kv[1]["mean_wer"]):
            lines.append(
                f"| {name} | {m['mean_wer']} | {m['mean_cer']} | {m['mean_latency_ms']} |"
            )
    else:
        lines += ["| Provider | Cases | Mean latency (ms) | Mean audio (ms) | Stability |",
                  "|---|---|---|---|---|"]
        for name, m in report.per_provider.items():
            stab = m["capabilities"]["long_form_stability"]
            lines.append(
                f"| {name} | {m['cases']} | {m['mean_latency_ms']} | "
                f"{m['mean_audio_ms']} | {stab} |"
            )
    if report.notes:
        lines += ["", "## Notes", *[f"- {n}" for n in report.notes]]
    return "\n".join(lines) + "\n"


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "BenchmarkReport",
    "char_error_rate",
    "report_to_markdown",
    "run_stt_benchmark",
    "run_tts_benchmark",
    "word_error_rate",
]
