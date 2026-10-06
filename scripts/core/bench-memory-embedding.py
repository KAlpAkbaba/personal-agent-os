"""ADR-0200 — the owner's Turkish benchmark for the local memory embedder.

Compares what production used until ADR-0200 (the n-gram hash, `DeterministicEmbedder`)
with one or more local models on Turkish pairs the memory actually holds: paraphrases
that must score HIGH and unrelated sentences that must score LOW. The number to read is
the SEPARATION (mean paraphrase cosine minus mean unrelated cosine): the wider, the better
the model tells "the same thing said differently" from "another topic". Nothing here
touches the database; it needs `services/api`'s environment (fastembed is a dependency
since ADR-0200) and network access to huggingface.co for the first download of each model.

Run from the repository root (Windows, the project venv):

    services\\api\\.venv\\Scripts\\python.exe scripts\\core\\bench-memory-embedding.py
    services\\api\\.venv\\Scripts\\python.exe scripts\\core\\bench-memory-embedding.py \\
        ibm-granite/granite-embedding-311m-multilingual-r2 --threads 4 \\
        --out bench.json --cost cost-potion.json --cost cost-granite.json

Prints one table per model. A model that is not natively 256 wide and not on the
Matryoshka allowlist is reported as refused, exactly as the API would refuse it.

memory-embedding-granite-measure: the default model is ALWAYS measured first as the
baseline row; ``--out`` writes every pair's cosine per model as JSON (and the Turkish
report beside it as ``.md``), ``--cost`` folds in ``measure-embedder-cost.py --out`` files,
and ``render_tr`` writes the verdict the numbers give - (a) / (b) / (c) - which ADOPTS
NOTHING: changing the default is a separate idea for the owner. ``--cache-dir`` defaults to
``%LOCALAPPDATA%/PagentOS/fastembed-cache``; the repository and %TEMP% are refused.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.memory.embedding import DeterministicEmbedder, cosine_similarity  # noqa: E402
from app.memory.providers import (  # noqa: E402
    CUSTOM_ONNX_MODELS,
    DEFAULT_LOCAL_MODEL,
    EmbeddingProviderError,
    LocalEmbedder,
    _fastembed_factory,
)

# (a, b, ANLAMDAŞ | ALAKASIZ) — the shapes owner memory takes: preferences, people,
# projects, routines, device facts. Extend freely; keep pairs balanced.
PAIRS: list[tuple[str, str, str]] = [
    ("Sahip sabahları kahve içmeyi sever", "Kadir her sabah bir fincan kahve içer", "ANLAMDAŞ"),
    ("Sahip sabahları kahve içmeyi sever", "Sunucuda disk alanı azaldı", "ALAKASIZ"),
    ("Araştırma raporlarını Türkçe okusun", "Yabancı kaynaklar Türkçeye çevrilsin", "ANLAMDAŞ"),
    ("Araştırma raporlarını Türkçe okusun", "Alarm saat yedide çalsın", "ALAKASIZ"),
    ("Aktivra benim kurduğum şirket", "Şirketimin adı Aktivra", "ANLAMDAŞ"),
    ("Aktivra benim kurduğum şirket", "Bugün hava çok sıcak", "ALAKASIZ"),
    ("Nişanlımın adı Melissa", "Melissa ile nişanlıyım", "ANLAMDAŞ"),
    ("Nişanlımın adı Melissa", "YouTube'da müzik aç", "ALAKASIZ"),
    (
        "Ekranlar 15 dakika sonra kapansın",
        "Monitörler çeyrek saat boşta kalınca sönsün",
        "ANLAMDAŞ",
    ),
    ("Ekranlar 15 dakika sonra kapansın", "Postgres yedeği gece yarısı alınır", "ALAKASIZ"),
    (
        "Yapay zeka haberlerini düzenli araştırıyorum",
        "AI gelişmelerini sık sık takip ederim",
        "ANLAMDAŞ",
    ),
    ("Yapay zeka haberlerini düzenli araştırıyorum", "Mutfaktaki lambayı kapat", "ALAKASIZ"),
    ("İş bilgisayarımın adı MAIL", "MAIL benim ofis makinem", "ANLAMDAŞ"),
    ("İş bilgisayarımın adı MAIL", "Kripto piyasası bugün düştü", "ALAKASIZ"),
]

#: The two paraphrases ADR-0200 found hardest (no shared words): read by name per model.
HARD_PAIRS: tuple[tuple[str, str], ...] = (
    ("Ekranlar 15 dakika sonra kapansın", "Monitörler çeyrek saat boşta kalınca sönsün"),
    ("Sahip sabahları kahve içmeyi sever", "Kadir her sabah bir fincan kahve içer"),
)

#: ADR-0200 'Kanıt' (the owner's PC, 2026-09-27): the earlier run, quoted, never re-derived.
EARLIER_RUN: dict[str, Any] = {
    "date": "2026-09-27",
    "source": "ADR-0200 Kanıt",
    "ayrim": {"minishlab/potion-multilingual-128M": 0.457, "Qwen/Qwen3-Embedding-0.6B-Q": 0.354},
}

#: The CPX32 budget the verdict reads: a 200-row retention batch (``memory_index_fill_batch``
#: x the median embedding) must finish within this many seconds in the 4-thread shape. Qwen3
#: was rejected at 2.3 minutes (ADR-0200); this is a stated proxy budget, not a CPX32 run.
CPX32_RETENTION_BUDGET_S = 30.0

SHAPE_HOME = "ev-pc"
SHAPE_CPX32 = "cpx32-bicimi"
DEFAULT_CACHE_LABEL = "%LOCALAPPDATA%/PagentOS/fastembed-cache"
NO_ADOPTION = "Bu kart benimsemez: varsayılan model potion kalır, yeniden indeksleme yok."


def shape_for(threads: int | None) -> str:
    if threads is None:
        return SHAPE_HOME
    return SHAPE_CPX32 if threads == 4 else f"threads-{threads}"


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "PagentOS" / "fastembed-cache"


def cache_refusal(cache_dir: Path) -> str | None:
    """The cache is never the repository and never %TEMP% (millions of leaked temp folders
    once slowed every test on this machine)."""
    resolved = cache_dir.resolve()
    for forbidden, why in (
        (ROOT.resolve(), "the repository"),
        (Path(tempfile.gettempdir()).resolve(), "%TEMP%"),
    ):
        if resolved == forbidden or forbidden in resolved.parents:
            return f"--cache-dir is under {why}; use a folder outside it"
    return None


def cache_label(cache_dir: Path) -> str:
    return (
        DEFAULT_CACHE_LABEL
        if cache_dir == default_cache_dir()
        else "--cache-dir (outside the repository and %TEMP%)"
    )


def _factory(threads: int | None):
    def factory(model_name: str, cache_dir: str | None):
        return _fastembed_factory(model_name, cache_dir, threads=threads)

    return factory


def _measure_pairs(embed) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    fresh_ms: list[float] = []
    seen: set[str] = set()
    for a, b, label in PAIRS:
        vectors = []
        started_pair = time.perf_counter()
        for text in (a, b):
            started = time.perf_counter()
            vectors.append(embed(text))
            if text not in seen:  # a cached text is not an embedding's cost
                seen.add(text)
                fresh_ms.append((time.perf_counter() - started) * 1000)
        ms = (time.perf_counter() - started_pair) * 1000
        rows.append(
            {
                "a": a,
                "b": b,
                "label": label,
                "cosine": round(cosine_similarity(*vectors), 4),
                "ms": round(ms, 2),
            }
        )
    same = [r["cosine"] for r in rows if r["label"] == "ANLAMDAŞ"]
    other = [r["cosine"] for r in rows if r["label"] == "ALAKASIZ"]
    fresh_ms.sort()
    return {
        "pairs": rows,
        "same_mean": round(sum(same) / len(same), 4),
        "other_mean": round(sum(other) / len(other), 4),
        "ayrim": round(sum(same) / len(same) - sum(other) / len(other), 4),
        "embed_median_ms": round(fresh_ms[len(fresh_ms) // 2], 3),
    }


def measure_model(model_name: str, *, threads: int | None, cache_dir: str | None) -> dict[str, Any]:
    factory = _factory(threads)
    started = time.perf_counter()
    try:
        local = LocalEmbedder(model_name=model_name, cache_dir=cache_dir, model_factory=factory)
    except EmbeddingProviderError as exc:
        return {"model_name": model_name, "refused": str(exc)}
    load_first = time.perf_counter() - started
    started = time.perf_counter()
    try:
        LocalEmbedder(model_name=model_name, cache_dir=cache_dir, model_factory=factory)
        load_warm: float | None = round(time.perf_counter() - started, 2)
    except EmbeddingProviderError:
        load_warm = None
    spec = CUSTOM_ONNX_MODELS.get(model_name)
    row: dict[str, Any] = {
        "model_name": model_name,
        "refused": None,
        "model_id": local.model_id,
        "native_dim": local.native_dim,
        "dim": local.dim,
        "truncated": local.truncated,
        "load_path": "onnxruntime-yedek"
        if type(local._model).__name__ == "_OnnxRuntimeModel"
        else "fastembed",
        "load_s_first": round(load_first, 2),
        "load_s_warm": load_warm,
        # Loaded means every pinned file passed its size and sha256 check.
        "pinned_files": [
            {"path": f.path, "size": f.size, "sha256": f.sha256, "verified": True}
            for f in spec.files
        ]
        if spec is not None
        else [],
        "revision": spec.revision if spec is not None else None,
    }
    row.update(_measure_pairs(local.embed))
    return row


def run_bench(models: list[str], *, threads: int | None, cache_dir: str | None) -> dict[str, Any]:
    from app.config import Settings

    ordered = [DEFAULT_LOCAL_MODEL] + [m for m in models if m != DEFAULT_LOCAL_MODEL]
    det = DeterministicEmbedder()
    deterministic = {"model_name": "deterministic n-gram (ADR-0200 öncesi üretim)", "refused": None}
    deterministic.update(_measure_pairs(det.embed))
    return {
        "kind": "memory-embedding-granite-measure",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "threads": threads,
        "shape": shape_for(threads),
        "baseline_model": DEFAULT_LOCAL_MODEL,
        "retention_batch_rows": Settings().memory_index_fill_batch,
        "cpx32_retention_budget_s": CPX32_RETENTION_BUDGET_S,
        "earlier_run": EARLIER_RUN,
        "deterministic": deterministic,
        "models": [measure_model(m, threads=threads, cache_dir=cache_dir) for m in ordered],
        "cost": [],
    }


# ---------------------------------------------------------------------- the Turkish report


def _costs(report: dict[str, Any], model_name: str) -> list[dict[str, Any]]:
    return [c for c in report.get("cost", []) if c.get("model_name") == model_name]


def _retention(report: dict[str, Any], row: dict[str, Any]) -> tuple[float | None, str]:
    """The retention-batch seconds the verdict reads: the cpx32-bicimi cost run first, then
    any cost run, then the bench's own median - with where it came from."""
    costs = _costs(report, row["model_name"])
    for cost in sorted(costs, key=lambda c: c.get("shape") != SHAPE_CPX32):
        if cost.get("retention_batch_s") is not None:
            return float(cost["retention_batch_s"]), f"maliyet ölçümü, {cost.get('shape')}"
    median = row.get("embed_median_ms")
    if median is None:
        return None, "ölçülmedi"
    rows = report.get("retention_batch_rows", 200)
    return round(median * rows / 1000, 2), f"bench ortancası, {report.get('shape')}"


def verdict_tr(report: dict[str, Any]) -> dict[str, Any]:
    """(a) / (b) / (c) from the numbers only. The best loaded candidate decides."""
    rows = [r for r in report.get("models", []) if r.get("refused") is None]
    baseline = next((r for r in rows if r["model_name"] == report.get("baseline_model")), None)
    candidates = [r for r in rows if baseline is None or r["model_name"] != baseline["model_name"]]
    if baseline is None or not candidates:
        return {
            "letter": None,
            "per_model": {},
            "text": "Karar yok: taban (potion) ya da aday ölçülemedi. " + NO_ADOPTION,
        }
    budget = float(report.get("cpx32_retention_budget_s", CPX32_RETENTION_BUDGET_S))
    per_model: dict[str, str] = {}
    for row in candidates:
        seconds, _source = _retention(report, row)
        if row["ayrim"] <= baseline["ayrim"]:
            per_model[row["model_name"]] = "c"
        elif seconds is not None and seconds <= budget:
            per_model[row["model_name"]] = "a"
        else:
            per_model[row["model_name"]] = "b"
    letter = min(per_model.values())  # a before b before c
    texts = {
        "a": "AYRIM potion'dan geniş ve 200 satırlık tutma partisi CPX32 bütçesinin "
        f"({budget:g} s) içinde: benimseme ÖNERİSİ ayrı fikir olarak gelir, sahibin onayına.",
        "b": "AYRIM potion'dan geniş ama 200 satırlık tutma partisi CPX32 bütçesini "
        f"({budget:g} s) aşıyor: Qwen3 gibi 'sahibin PC'sine taşınınca' notu.",
        "c": "AYRIM potion'dan geniş değil: ölçüldü, alınmadı.",
    }
    return {
        "letter": letter,
        "per_model": per_model,
        "text": f"({letter}) {texts[letter]} {NO_ADOPTION}",
    }


def _fmt(value: Any, unit: str = "", digits: int = 2) -> str:
    if value is None:
        return "ölçülmedi"
    return f"{value:.{digits}f}{unit}" if isinstance(value, float | int) else str(value)


def _cost_cell(report: dict[str, Any], name: str, key: str, unit: str) -> str:
    cells = [
        f"{c.get('shape')} {_fmt(c.get(key), unit)}"
        for c in _costs(report, name)
        if c.get(key) is not None
    ]
    return " · ".join(cells) if cells else "ölçülmedi"


def _row_md(report: dict[str, Any], row: dict[str, Any]) -> str:
    name = row["model_name"]
    if row.get("refused") is not None:
        return f"| `{name}` | REDDEDİLDİ: {row['refused']} | — | — | — | — | — |"
    width = f" ({row['native_dim']}→{row['dim']}, MRL)" if row.get("truncated") else ""
    load = (
        f"ilk {_fmt(row.get('load_s_first'), ' s')} / ısınmış {_fmt(row.get('load_s_warm'), ' s')}"
    )
    embed = (
        f"{_fmt(row.get('embed_median_ms'), ' ms', 3)} (bench) · "
        f"{_cost_cell(report, name, 'embed_median_ms', ' ms')}"
    )
    rss = _cost_cell(report, name, "rss_peak_mb", " MB tepe")
    return f"| `{name}`{width} | {_means(row)} | {load} | {embed} | {rss} |"


def _means(row: dict[str, Any]) -> str:
    return f"{row['ayrim']:+.3f} | {row['same_mean']:.3f} | {row['other_mean']:.3f}"


def render_tr(report: dict[str, Any]) -> str:
    verdict = verdict_tr(report)
    lines = [
        "# Hafıza gömme ölçümü: IBM Granite Embedding Multilingual R2, potion'ın yanında",
        "",
        "Yalnız ÖLÇÜM (memory-embedding-granite-measure). Üretimde hiçbir şey değişmedi: "
        f"varsayılan `{report.get('baseline_model')}`, şema, indeks ve bağımlılıklar aynı. "
        "Cümleler sabit Türkçe bench cümleleri, sahibin gerçek hafıza metni değil; model "
        "yerelde koştu.",
        "",
        f"- Biçim: `{report.get('shape')}` (threads {report.get('threads') or 'hepsi'})"
        + (
            " — CPX32 VEKİLİ (PROXY), gerçek CPX32 koşusu: NOT_RUN"
            if report.get("shape") == SHAPE_CPX32
            else ""
        ),
        f"- Önbellek: {report.get('cache', DEFAULT_CACHE_LABEL)} (depo ve %TEMP% dışında)",
        f"- Ölçüm zamanı: {report.get('measured_at', 'bilinmiyor')}",
        "",
        "| model | AYRIM | anlamdaş ort. | alakasız ort. | yükleme | embed ortanca | RSS |",
        "|---|---|---|---|---|---|---|",
    ]
    det = report.get("deterministic")
    if det:
        lines.append(f"| {det['model_name']} | {_means(det)} | — | — | — |")
    lines.extend(_row_md(report, row) for row in report.get("models", []))
    earlier = report.get("earlier_run", EARLIER_RUN)
    quoted = ", ".join(f"`{name}` {value:+.3f}" for name, value in earlier["ayrim"].items())
    lines += [
        "",
        f"Önceki koşu ({earlier['source']}, {earlier['date']}, aynı 14 çift): {quoted}.",
        "",
        "## Zor iki çift (ortak kelimesi yok)",
        "",
        "| model | " + " | ".join(f"{a} ~ {b}" for a, b in HARD_PAIRS) + " |",
        "|---|" + "---|" * len(HARD_PAIRS),
    ]
    for row in report.get("models", []):
        if row.get("refused") is not None:
            lines.append(
                f"| `{row['model_name']}` | " + " | ".join("REDDEDİLDİ" for _ in HARD_PAIRS) + " |"
            )
            continue
        scores = {(p["a"], p["b"]): p["cosine"] for p in row.get("pairs", [])}
        cells = [_fmt(scores.get(pair), "", 3) for pair in HARD_PAIRS]
        lines.append(f"| `{row['model_name']}` | " + " | ".join(cells) + " |")
    lines += ["", "## 200 satırlık tutma partisi", ""]
    for row in report.get("models", []):
        if row.get("refused") is None:
            seconds, source = _retention(report, row)
            lines.append(f"- `{row['model_name']}`: {_fmt(seconds, ' s')} ({source})")
    pinned = [
        r for r in report.get("models", []) if r.get("refused") is None and r.get("pinned_files")
    ]
    if pinned:
        lines += ["", "## Sabitlenmiş dosyalar (boyut ve sha256 ilk gömmeden önce doğrulandı)", ""]
        for row in pinned:
            lines.append(
                f"- `{row['model_name']}` @ `{row.get('revision')}`, "
                f"yükleme yolu: {row.get('load_path')}"
            )
            for f in row["pinned_files"]:
                lines.append(
                    f"  - `{f['path']}` {f['size']} bayt sha256 `{f['sha256']}`: doğrulandı"
                )
    lines += [
        "",
        "## Hüküm",
        "",
        f"**({verdict['letter']})** {verdict['text'].split(') ', 1)[-1]}"
        if verdict["letter"]
        else verdict["text"],
        "",
        "Model başına: "
        + (", ".join(f"`{k}` ({v})" for k, v in verdict["per_model"].items()) or "yok"),
        "",
    ]
    return "\n".join(lines)


def _print_table(row: dict[str, Any]) -> None:
    if row.get("refused") is not None:
        print(f"\n== {row['model_name']}: REDDEDİLDİ — {row['refused']}")
        return
    note = f" [kırpıldı {row['native_dim']}→{row['dim']}]" if row.get("truncated") else ""
    head = f"{row['model_name']}{note}" + (
        f" → model_id {row['model_id']}" if row.get("model_id") else ""
    )
    load = row.get("load_s_first")
    print(f"\n== {head}" + (f"  (yükleme {load:.1f} s)" if load is not None else ""))
    print(f"{'':10} {'cosine':>7}   {'ms':>5}   çift")
    for p in row["pairs"]:
        pair = f"{p['a'][:34]!r} ~ {p['b'][:34]!r}"
        print(f"{p['label']:10} {p['cosine']:7.3f}   {p['ms']:5.0f}   {pair}")
    print(
        f"AYRIM (anlamdaş ort. − alakasız ort.): {row['ayrim']:+.3f}   "
        f"[anlamdaş {row['same_mean']:.3f} | alakasız {row['other_mean']:.3f}]"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="*")
    parser.add_argument("--out", type=Path, default=None, help="write the report as JSON (+ .md)")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument(
        "--cost", type=Path, action="append", default=[], help="a measure-embedder-cost --out file"
    )
    args = parser.parse_args(argv[1:])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cache_dir = args.cache_dir or default_cache_dir()
    refusal = cache_refusal(cache_dir)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    report = run_bench(args.models, threads=args.threads, cache_dir=str(cache_dir))
    report["cache"] = cache_label(cache_dir)
    report["cost"] = [json.loads(path.read_text(encoding="utf-8")) for path in args.cost]
    report["verdict"] = verdict_tr(report)
    _print_table(report["deterministic"])
    for row in report["models"]:
        _print_table(row)
    print(f"\nHüküm: {report['verdict']['text']}")
    print("Okuma: AYRIM ne kadar büyükse hafıza 'aynı şeyi başka sözle' o kadar iyi bulur.")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        args.out.with_suffix(".md").write_text(render_tr(report), encoding="utf-8")
        print(f"kanıt: {args.out.as_posix()} (+ .md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
