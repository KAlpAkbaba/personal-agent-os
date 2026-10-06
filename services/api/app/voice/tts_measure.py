"""FreyaTTS measurement, the pure half (tts-freya-measure; team/plans/tts-freya-measure-adr.md).

MEASUREMENT ONLY. Nothing in the application imports this module; it holds no torch and no
FastAPI. The model runs in its own container (``tools/tts-measure``); this module

* hands that container the twenty sentences (``OWNER_SENTENCES``, imported, never retyped)
  as JSON lines ``{index, text}``;
* parses the container's JSON lines - a malformed line or a missing index is a FAILED
  sentence with its reason, never a silent drop - and turns them into the per-machine numbers:
  RTF per sentence (synth_ms / audio_ms), pooled RTF (sum synth / sum audio), p50 / p95 of
  RTF and of first_audio_ms (nearest rank), peak memory, model load time (once, never inside
  a sentence), sentences ok / failed;
* merges one machine into ``docs/evidence/tts-<engine>-measure.json`` (a re-run of a label
  replaces only that label) and renders the Turkish ``.md``;
* renders ``docs/evidence/tts-measure-compare.md``: every engine (FreyaTTS, and Antalia 1 from
  tts-antalia-measure) in one table; an engine with no evidence is 'ölçülmedi', never a zero.

The verdict says from the numbers only whether real time is reached (pooled RTF < 1). Sound
quality is NOT judged here; the owner's ear does that.

Run: ``python -m app.voice.tts_measure input --out <file>`` and
``python -m app.voice.tts_measure merge --output <container stdout> --label ... --evidence-dir ...``
(``scripts/voice/tts-measure.ps1`` calls both).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.voice.stt_compare import OWNER_SENTENCES, percentile

SCHEMA_VERSION = "1.0"
CODE_COMMIT = "146d36c1cb6660646be57d31339db4eed9315de3"
WEIGHT_REVISIONS: dict[str, str] = {
    "freyavoice/Freya-TTS": "d124e07493615208f58bdd21d432736849ee4230",
    "openbmb/VoxCPM2": "32279effe8c19989596f05d353d1447f51d9e915",
}
EVIDENCE_NAME = "tts-freya-measure"
#: The measured engines, in table order (tts-antalia-measure adds the second candidate).
#: A report written before the ``engine`` field existed is a FreyaTTS report.
ENGINES: dict[str, dict[str, Any]] = {
    "freya": {
        "name": "FreyaTTS-small",
        "card": "tts-freya-measure",
        "code_commit": CODE_COMMIT,
        "weight_revisions": WEIGHT_REVISIONS,
    },
    "antalia": {
        "name": "Antalia 1",
        "card": "tts-antalia-measure",
        "code_commit": "20f9bfeaaefefb3ef723c292fe2bb0306e08823d",
        "weight_revisions": {
            "cloud0day3/antalia-1": "eaec2aad2da8c0db5fc359734470874dae82c603",
            "nvidia/bigvgan_v2_24khz_100band_256x": "c329ede9e9bbc100ddf5c91e2330a61921262370",
        },
        "vocoder_code_commit": "7d2b454564a6c7d014227f635b7423881f14bdac",
    },
}
DEFAULT_ENGINE = "freya"
COMPARE_NAME = "tts-measure-compare"
#: The labels every engine is expected to have; one missing is an 'ölçülmedi' row.
EXPECTED_LABELS = ("ev-pc", "cpx32-bicimi")
NOT_MEASURED = "ölçülmedi"


def evidence_name(engine: str) -> str:
    """The evidence base name of ``engine`` (``tts-<engine>-measure``)."""
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}")
    return f"tts-{engine}-measure"


def report_engine(report: dict[str, Any]) -> str:
    """The engine a report is for; one without the field predates it and is FreyaTTS."""
    return str((report.get("model") or {}).get("engine") or DEFAULT_ENGINE)


#: The sentences whose WAVs the .md offers the owner to listen to.
SAMPLE_INDICES = (1, 5, 10, 15, 20)
#: Labels that stand in for another machine: the .md and the JSON say so.
PROXY_LABELS: dict[str, str] = {"cpx32-bicimi": "CPX32"}
REAL_CPX32_LABEL = "cpx32-gercek"
_NUMBER_FIELDS = ("audio_ms", "synth_ms")


def sentences() -> tuple[str, ...]:
    """The measured sentences: ``OWNER_SENTENCES`` itself (identity, not a copy)."""
    return OWNER_SENTENCES


def input_lines() -> list[str]:
    """One JSON line ``{index, text}`` per sentence, index from 1."""
    return [
        json.dumps({"index": index, "text": text}, ensure_ascii=False)
        for index, text in enumerate(sentences(), start=1)
    ]


@dataclass
class SentenceResult:
    index: int
    ok: bool
    reason: str = ""
    chars: int = 0
    audio_ms: float = 0.0
    synth_ms: float = 0.0
    first_audio_ms: float = 0.0
    streamed: bool = False
    peak_rss_mb: float | None = None
    retries: int = 0
    voiced_check_ms: float = 0.0

    @property
    def rtf(self) -> float:
        return self.synth_ms / self.audio_ms


@dataclass
class ContainerRun:
    load: dict[str, Any] | None
    results: list[SentenceResult]
    malformed: list[str] = field(default_factory=list)


def _number(row: dict[str, Any], name: str) -> float:
    value = row.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"bozuk satır: {name} sayı değil ({value!r})")
    return float(value)


def _sentence(row: dict[str, Any]) -> SentenceResult:
    index = int(row["index"])
    if row.get("error"):
        return SentenceResult(index, ok=False, reason=str(row["error"]))
    try:
        audio_ms = _number(row, "audio_ms")
        synth_ms = _number(row, "synth_ms")
        if audio_ms <= 0:
            raise ValueError("bozuk satır: audio_ms sıfır ya da negatif")
        streamed = row.get("streamed") is True
        # A model that does not stream gives its first audio when the whole synthesis ends.
        first = _number(row, "first_audio_ms") if streamed else synth_ms
        peak = row.get("peak_rss_mb")
        return SentenceResult(
            index,
            ok=True,
            chars=int(row.get("chars") or 0),
            audio_ms=audio_ms,
            synth_ms=synth_ms,
            first_audio_ms=first,
            streamed=streamed,
            peak_rss_mb=float(peak) if isinstance(peak, int | float) else None,
            retries=int(row.get("retries") or 0),
            voiced_check_ms=_number(row, "voiced_check_ms") if "voiced_check_ms" in row else 0.0,
        )
    except ValueError as error:
        return SentenceResult(index, ok=False, reason=str(error))


def parse_container_output(lines: Iterable[str], indices: Iterable[int]) -> ContainerRun:
    """Parse the container's stdout. Every expected index gets exactly one result."""
    load: dict[str, Any] | None = None
    found: dict[int, SentenceResult] = {}
    malformed: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("not an object")
        except ValueError:
            malformed.append(line[:200])
            continue
        if row.get("event") == "load":
            load = row
            continue
        if "index" not in row:
            malformed.append(line[:200])
            continue
        try:
            result = _sentence(row)
        except (TypeError, ValueError):
            malformed.append(line[:200])
            continue
        if result.index in found:
            found[result.index] = SentenceResult(
                result.index, ok=False, reason="aynı index iki kez geldi"
            )
        else:
            found[result.index] = result
    results: list[SentenceResult] = []
    for index in indices:
        if index in found:
            results.append(found[index])
            continue
        reason = "çıktıda satırı yok"
        if malformed:
            reason += f"; bozuk satır var ({len(malformed)})"
        results.append(SentenceResult(index, ok=False, reason=reason))
    return ContainerRun(load, results, malformed)


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def summarize_machine(
    run: ContainerRun,
    *,
    label: str,
    threads: int,
    cpus_limit: float | None,
    wav_dir: str,
    image: str = "",
    seed: int = 9,
    steps: int = 32,
) -> dict[str, Any]:
    ok = [r for r in run.results if r.ok]
    failed = [r for r in run.results if not r.ok]
    rtfs = [r.rtf for r in ok]
    firsts = [r.first_audio_ms for r in ok]
    sum_audio = sum(r.audio_ms for r in ok)
    sum_synth = sum(r.synth_ms for r in ok)
    peaks = [r.peak_rss_mb for r in ok if r.peak_rss_mb is not None]
    load = run.load or {}
    if isinstance(load.get("peak_rss_mb"), int | float):
        peaks.append(float(load["peak_rss_mb"]))
    machine: dict[str, Any] = {
        "label": label,
        "cpu": str(load.get("cpu") or ""),
        "threads": threads,
        "cpus_limit": cpus_limit,
        "proxy_for": PROXY_LABELS.get(label),
        "image": image,
        "synthesis_network": "none",
        "seed": seed,
        "steps": steps,
        "streamed": any(r.streamed for r in ok),
        "load_ms": _round(
            load.get("load_ms") if isinstance(load.get("load_ms"), int | float) else None, 1
        ),
        "sentences_ok": len(ok),
        "sentences_failed": len(failed),
        "failures": [{"index": r.index, "reason": r.reason} for r in failed],
        "rtf_pooled": _round(sum(r.synth_ms for r in ok) / sum_audio) if sum_audio > 0 else None,
        "rtf_p50": _round(percentile(rtfs, 50)),
        "rtf_p95": _round(percentile(rtfs, 95)),
        "first_audio_ms_p50": _round(percentile(firsts, 50), 1),
        "first_audio_ms_p95": _round(percentile(firsts, 95), 1),
        "peak_rss_mb": _round(max(peaks), 1) if peaks else None,
        "retries_total": sum(r.retries for r in ok),
        # the share of synthesis time spent in upstream's per-clause pyin check (inside synth_ms)
        "voiced_check_share": _round(sum(r.voiced_check_ms for r in ok) / sum_synth)
        if sum_synth > 0
        else None,
        "malformed_lines": len(run.malformed),
        "wav_dir": wav_dir,
        "sentences": [
            {
                "index": r.index,
                "chars": r.chars,
                "audio_ms": _round(r.audio_ms, 1),
                "synth_ms": _round(r.synth_ms, 1),
                "first_audio_ms": _round(r.first_audio_ms, 1),
                "rtf": _round(r.rtf),
                "retries": r.retries,
                "voiced_check_ms": _round(r.voiced_check_ms, 1),
            }
            for r in ok
        ],
    }
    return machine


def _tr(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}".replace(".", ",")


def verdict_tr(machines: Sequence[dict[str, Any]]) -> str:
    """Real time or not, per machine, from pooled RTF only. No word on quality."""
    parts: list[str] = []
    for machine in machines:
        label = machine["label"]
        pooled = machine.get("rtf_pooled")
        if pooled is None:
            parts.append(f"{label}: ölçülemedi (başarılı cümle yok).")
        elif pooled < 1:
            parts.append(f"{label}: gerçek zamana ulaşıldı (havuzlanmış RTF {_tr(pooled)} < 1).")
        else:
            parts.append(f"{label}: gerçek zamana ulaşılamadı (havuzlanmış RTF {_tr(pooled)} ≥ 1).")
    parts.append("Ses kalitesi burada değerlendirilmez; ona sahibin kulağı karar verir.")
    return " ".join(parts)


def merge_report(
    existing: dict[str, Any] | None, machine: dict[str, Any], engine: str = DEFAULT_ENGINE
) -> dict[str, Any]:
    """Put ``machine`` into ``engine``'s report: a re-run of its label replaces it in place."""
    if existing is not None and report_engine(existing) != engine:
        raise ValueError(f"the existing report is {report_engine(existing)!r}, not {engine!r}")
    spec = ENGINES[engine]
    machines = list((existing or {}).get("machines") or [])
    for position, current in enumerate(machines):
        if current.get("label") == machine["label"]:
            machines[position] = machine
            break
    else:
        machines.append(machine)
    return {
        "schema_version": SCHEMA_VERSION,
        "model": {
            "engine": engine,
            "name": spec["name"],
            "code_commit": spec["code_commit"],
            "weight_revisions": dict(spec["weight_revisions"]),
            **(
                {"vocoder_code_commit": spec["vocoder_code_commit"]}
                if "vocoder_code_commit" in spec
                else {}
            ),
        },
        "machines": machines,
        "verdict_tr": verdict_tr(machines),
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def render_markdown(report: dict[str, Any], repo_root: Path) -> str:
    """The Turkish evidence page. A WAV folder inside the repository is refused."""
    for machine in report["machines"]:
        if _inside(Path(machine["wav_dir"]), repo_root):
            raise ValueError(
                f"WAV folder of {machine['label']} is inside the repository: {machine['wav_dir']}"
            )
    model = report["model"]
    engine = report_engine(report)
    spec = ENGINES[engine]
    lines = [
        f"# {spec['name']} ölçümü ({spec['card']})",
        "",
        "YALNIZ ÖLÇÜM: sağlayıcı bağlanmadı, ayar yok, API'ye bağımlılık eklenmedi.",
        f"Kod `{model['code_commit']}`; ağırlıklar "
        + ", ".join(f"`{repo}@{rev}`" for repo, rev in model["weight_revisions"].items())
        + ".",
        "Sentez `--network none` ile, imaj `pip install --require-hashes` ile kuruldu. "
        "Cümleler: `OWNER_SENTENCES` (yazılı, sentetik; sahibin sesi kullanılmadı).",
        "",
        "## Karar (yalnız sayılardan)",
        "",
        report["verdict_tr"],
        "",
        "## Makineler",
        "",
        "Etiket | tür | CPU | iş parçacığı | --cpus | yükleme ms | RTF havuz | RTF p50/p95 | "
        "ilk ses ms p50/p95 | tepe bellek MB | ok/hata | yeniden deneme",
        "---|---|---|---|---|---|---|---|---|---|---|---",
    ]
    for m in report["machines"]:
        kind = f"VEKİL ({m['proxy_for']} biçimi, ev PC'sinde)" if m.get("proxy_for") else "gerçek"
        lines.append(
            f"{m['label']} | {kind} | {m.get('cpu') or '-'} | {m['threads']} | "
            f"{m['cpus_limit'] if m.get('cpus_limit') is not None else 'tümü'} | "
            f"{_tr(m.get('load_ms'), 0)} | {_tr(m.get('rtf_pooled'), 3)} | "
            f"{_tr(m.get('rtf_p50'), 3)} / {_tr(m.get('rtf_p95'), 3)} | "
            f"{_tr(m.get('first_audio_ms_p50'), 0)} / {_tr(m.get('first_audio_ms_p95'), 0)} | "
            f"{_tr(m.get('peak_rss_mb'), 0)} | {m['sentences_ok']}/{m['sentences_failed']} | "
            f"{m.get('retries_total', 0)}"
        )
    lines.append("")
    if not any(m["label"] == REAL_CPX32_LABEL for m in report["machines"]):
        lines.append("- cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)")
    if any(m.get("proxy_for") for m in report["machines"]):
        lines.append(
            "- VEKİL satırı sunucunun kendisi değildir: ev PC'sinde `--cpus 4` ile sınırlanmış "
            "aynı konteyner. Sunucu CPU'su farklıdır."
        )
    if not any(m.get("streamed") for m in report["machines"]):
        lines.append(
            "- Model akış yapmıyor (streamed: false): ilk ses gecikmesi = tüm sentez süresi."
        )
    lines.append("- Yükleme süresi ayrı ölçülür; hiçbir cümlenin süresine girmez.")
    if engine == "antalia":
        lines.append(
            "- Antalia: tarif v2 (32 Euler adımı, tohum 20260803, konuşmacı "
            "voicedata-candidate-b), tek tohum, best-of-8 yok. Gürültü zarfı sabitleme "
            "(pin_noise_envelope): açık kodda yok, uygulanmadı. Model geliştirmesi durdurulmuş "
            "(düzeltme gelmeyecek). Ağırlıklar Antalia Open RAIL-M: 'Antalia 1', Sezgin Saygili, "
            "Emre Kaplaner, Oncel Ozgul, Fikri San Koktas (Patientdesk.ai), "
            "https://huggingface.co/cloud0day3/antalia-1 ."
        )
    for m in report["machines"]:
        if engine == DEFAULT_ENGINE and m.get("voiced_check_share") is not None:
            lines.append(
                f"- {m['label']}: sentez süresinin {_tr(100 * m['voiced_check_share'], 1)} %'i "
                "üst kaynağın cümle parçası başına sesli-kontrolünde (librosa pyin) geçti "
                "(sentez süresinin içinde, ona eklenmedi)."
            )
    lines.append(
        "- Tepe bellek: konteyner sürecinin o ana kadarki en yüksek RSS'i (yükleme dahil)."
    )
    for m in report["machines"]:
        if m["failures"]:
            lines.append("")
            lines.append(f"### {m['label']}: başarısız cümleler")
            lines.extend(f"- {f['index']}: {f['reason']}" for f in m["failures"])
    lines += ["", "## Dinleme örnekleri (sahip: 'kullanılır' / 'kullanılmaz')", ""]
    for m in report["machines"]:
        lines.append(f"{m['label']}:")
        lines.extend(
            f"- {index}: `{Path(m['wav_dir']) / f'{index:02d}.wav'}`" for index in SAMPLE_INDICES
        )
    lines += ["", "Ses kalitesi burada değerlendirilmez; karar sahibin kulağınındır.", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ the engines side by side


def load_reports(evidence_dir: Path) -> dict[str, dict[str, Any] | None]:
    """Each engine's evidence report, or ``None`` when its file does not exist yet."""
    reports: dict[str, dict[str, Any] | None] = {}
    for engine in ENGINES:
        path = evidence_dir / f"{evidence_name(engine)}.json"
        reports[engine] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    return reports


def _compare_machines(
    report: dict[str, Any] | None,
) -> list[tuple[str, dict[str, Any] | None]]:
    """(label, machine or None): the measured labels, then each expected label not measured."""
    machines = list((report or {}).get("machines") or [])
    pairs: list[tuple[str, dict[str, Any] | None]] = [(m["label"], m) for m in machines]
    measured = {label for label, _ in pairs}
    pairs += [(label, None) for label in EXPECTED_LABELS if label not in measured]
    return pairs


def compare_rows(reports: dict[str, dict[str, Any] | None]) -> list[str]:
    """One table row per engine x label. Not measured is 'ölçülmedi' in every cell, never 0."""
    rows: list[str] = []
    for engine in ENGINES:
        for label, m in _compare_machines(reports.get(engine)):
            if m is None:
                cells = [engine, label, "-", *[NOT_MEASURED] * 6]
            else:
                kind = f"VEKİL ({m['proxy_for']})" if m.get("proxy_for") else "gerçek"
                cells = [
                    engine,
                    label,
                    kind,
                    _tr(m.get("rtf_pooled"), 3),
                    _tr(m.get("rtf_p95"), 3),
                    _tr(m.get("first_audio_ms_p95"), 0),
                    _tr(m.get("peak_rss_mb"), 0),
                    _tr(m.get("load_ms"), 0),
                    f"{m['sentences_ok']}/{m['sentences_failed']}",
                ]
            rows.append("| " + " | ".join(cells) + " |")
    return rows


def compare_verdict_tr(reports: dict[str, dict[str, Any] | None]) -> str:
    """From the numbers only: real time per row, and which engine fits a smaller memory."""
    parts: list[str] = []
    peaks: dict[str, float] = {}
    for engine in ENGINES:
        report = reports.get(engine)
        if report is None:
            parts.append(f"{engine}: {NOT_MEASURED} (kanıt dosyası yok).")
            continue
        for m in report.get("machines") or []:
            pooled = m.get("rtf_pooled")
            name = f"{engine} / {m['label']}"
            if pooled is None:
                parts.append(f"{name}: ölçülemedi (başarılı cümle yok).")
            elif pooled < 1:
                parts.append(f"{name}: gerçek zamana ulaşıldı (havuzlanmış RTF {_tr(pooled)} < 1).")
            else:
                parts.append(
                    f"{name}: gerçek zamana ulaşılamadı (havuzlanmış RTF {_tr(pooled)} ≥ 1)."
                )
            if isinstance(m.get("peak_rss_mb"), int | float):
                peaks[engine] = max(peaks.get(engine, 0.0), float(m["peak_rss_mb"]))
    if len(peaks) >= 2:
        ordered = sorted(peaks.items(), key=lambda item: item[1])
        smallest, peak = ordered[0]
        others = "; ".join(f"{engine} {_tr(value, 0)} MB" for engine, value in ordered[1:])
        parts.append(f"Daha küçük bellekte sığan: {smallest} (tepe {_tr(peak, 0)} MB; {others}).")
    else:
        parts.append("Bellek karşılaştırması: iki motor birden ölçülmedi.")
    parts.append("Ses kalitesi burada değerlendirilmez; ona sahibin kulağı karar verir.")
    return " ".join(parts)


def render_compare(reports: dict[str, dict[str, Any] | None], repo_root: Path) -> str:
    """``docs/evidence/tts-measure-compare.md``: one table for every engine. A WAV folder inside
    the repository is refused."""
    for report in reports.values():
        for m in (report or {}).get("machines") or []:
            if _inside(Path(m["wav_dir"]), repo_root):
                raise ValueError(
                    f"WAV folder of {m['label']} is inside the repository: {m['wav_dir']}"
                )
    lines = [
        "# Yerel Türkçe ses adayları yan yana (FreyaTTS, Antalia 1)",
        "",
        "YALNIZ ÖLÇÜM: hiçbir motor bağlanmadı. Aynı yirmi cümle (`OWNER_SENTENCES`), aynı "
        "konteyner kuralları (`--network none`, salt okunur, uid 10001, 8 GB tavan, takas yok).",
        "",
        "| motor | etiket | tür | RTF havuz | RTF p95 | ilk ses p95 ms | tepe bellek MB | "
        "yükleme ms | ok/hata |",
        "|---|---|---|---|---|---|---|---|---|",
        *compare_rows(reports),
        "",
        "## Karar (yalnız sayılardan)",
        "",
        compare_verdict_tr(reports),
        "",
        "- cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)",
        "- CER: ölçülmedi (WAV'lar üzerinde bir STT geçişi gerekir; sonraki kart).",
        "- VEKİL satırı sunucunun kendisi değildir: ev PC'sinde `--cpus 4` ile sınırlanmış aynı "
        "konteyner.",
        "- İki model de akış yapmıyor: ilk ses gecikmesi = tüm sentez süresi.",
        "",
        "## Dinleme örnekleri (sahip: motor başına 'kullanılır' / 'kullanılmaz')",
        "",
    ]
    for engine in ENGINES:
        machines = list((reports.get(engine) or {}).get("machines") or [])
        if not machines:
            lines.append(f"{engine}: {NOT_MEASURED}")
            continue
        first = next((m for m in machines if m["label"] == EXPECTED_LABELS[0]), machines[0])
        lines.append(f"{engine} ({first['label']}):")
        lines.extend(
            f"- {index}: `{Path(first['wav_dir']) / f'{index:02d}.wav'}`"
            for index in SAMPLE_INDICES
        )
    lines += ["", "Ses kalitesi burada değerlendirilmez; karar sahibin kulağınındır.", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ command line


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tts_measure")
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("input", help="write the sentences as JSON lines")
    make.add_argument("--out", required=True, type=Path)
    merge = sub.add_parser("merge", help="merge one machine's container output into the evidence")
    merge.add_argument("--output", required=True, type=Path)
    merge.add_argument("--label", required=True)
    merge.add_argument("--threads", required=True, type=int)
    merge.add_argument("--cpus-limit", default="")
    merge.add_argument("--wav-dir", required=True)
    merge.add_argument("--evidence-dir", required=True, type=Path)
    merge.add_argument("--repo-root", required=True, type=Path)
    merge.add_argument("--image", default="")
    merge.add_argument("--seed", type=int, default=9)
    merge.add_argument("--steps", type=int, default=32)
    merge.add_argument("--engine", choices=sorted(ENGINES), default=DEFAULT_ENGINE)
    compare = sub.add_parser("compare", help="render tts-measure-compare.md from the evidence")
    compare.add_argument("--evidence-dir", required=True, type=Path)
    compare.add_argument("--repo-root", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "input":
        args.out.write_text("\n".join(input_lines()) + "\n", encoding="utf-8")
        return 0
    if args.command == "compare":
        markdown = render_compare(load_reports(args.evidence_dir), args.repo_root)
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        (args.evidence_dir / f"{COMPARE_NAME}.md").write_text(markdown, encoding="utf-8")
        return 0
    text = args.output.read_text(encoding="utf-8")
    run = parse_container_output(text.splitlines(), range(1, len(sentences()) + 1))
    if run.load is None:
        print("tts_measure: the container output has no load line; no evidence written")
        return 2
    machine = summarize_machine(
        run,
        label=args.label,
        threads=args.threads,
        cpus_limit=float(args.cpus_limit) if args.cpus_limit else None,
        wav_dir=args.wav_dir,
        image=args.image,
        seed=args.seed,
        steps=args.steps,
    )
    name = evidence_name(args.engine)
    json_path = args.evidence_dir / f"{name}.json"
    existing = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else None
    report = merge_report(existing, machine, args.engine)
    markdown = render_markdown(report, args.repo_root)  # refuses before anything is written
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.evidence_dir / f"{name}.md").write_text(markdown, encoding="utf-8")
    sys.stdout.buffer.write((report["verdict_tr"] + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
