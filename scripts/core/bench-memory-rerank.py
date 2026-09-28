"""ADR-0206 - the owner's Turkish benchmark for a local memory reranker.

`bench-memory-embedding.py` asked whether an embedding tells "the same thing said
differently" from "another topic". A reranker is asked a harder question: of five
memories that are ALL about the topic, which one answers this query? So every query here
comes with one right memory and four that share its words, its person or its project -
the shapes the owner's memory actually holds, and the mistakes a vector search makes.

Three things are measured per model, because all three decide whether it may serve:

* **quality** - the rank of the right memory (1 is best), as top-1 accuracy and MRR,
  beside the same ranking made by the embedder that serves today (the baseline a
  reranker has to beat to be worth its memory);
* **time** - load once per process, and milliseconds per (query, memory) pair: a voice
  turn pays `memory_rerank_top_k` of them;
* **memory** - this process's working set before the model and after it has scored, and
  the peak. The model lives inside the API process, one per colour, two through a
  blue-green drain, on an 8 GB host.

Each model is measured in its OWN child process, so one model's memory is never counted
into the next one's.

Run from the repository root, in the API's environment:

    services/api/.venv/Scripts/python.exe scripts/core/bench-memory-rerank.py \
        --cache-dir E:/AI/.model-cache --out docs/evidence/adr-0206-rerank-bench.json

Needs network access to huggingface.co for the first download of each model.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "api"))

#: (query, the right memory, four memories that are near it and wrong).
CASES: list[tuple[str, str, list[str]]] = [
    (
        "Araştırma raporlarını hangi dilde okumamı istiyor?",
        "Sahip araştırma raporlarının her zaman Türkçe okunmasını istiyor",
        [
            "Sahip 'yapay zeka haberleri' konusunu düzenli olarak araştırıyor (4 kez sordu).",
            "Araştırma tamamlandı: Türkiye'de elektrikli araç satışları",
            "Sahip raporların uzun halinin kendisine zorla okunmasını istemiyor",
            "Yabancı kaynaklı haberler özetlenirken kaynak adı söylensin",
        ],
    ),
    (
        "Nişanlımın adı neydi?",
        "Nişanlımın adı Melissa",
        [
            "Melissa'nın doğum günü 14 Mart",
            "Kardeşimin adı Emre",
            "Melissa ile cumartesi akşamı yemeğe çıkılacak",
            "Annemin adı Ayşe",
        ],
    ),
    (
        "Ekranlar ne zaman kapanıyor?",
        "Ekranlar 15 dakika boşta kalınca kapansın",
        [
            "Ekran parlaklığı akşam dokuzdan sonra yüzde kırka insin",
            "Bilgisayar gece ikide uyku moduna geçsin",
            "Sahip ikinci monitörü dikey kullanıyor",
            "Alarm çaldığında ekranlar açılsın",
        ],
    ),
    (
        "Şirketimin adı ne?",
        "Aktivra benim kurduğum şirket",
        [
            "Aktivra'nın muhasebesini Deniz Hanım tutuyor",
            "Sahip şirket bilgisayarında operator yetkisi istemiyor",
            "Aktivra için yeni bir web sitesi taslağı hazırlandı",
            "İş bilgisayarımın adı GMKADIRAKBABA",
        ],
    ),
    (
        "Ofisteki bilgisayarın adı ne?",
        "İş bilgisayarımın adı GMKADIRAKBABA",
        [
            "Ev bilgisayarımın adı MAIL",
            "Ofiste çalışırken bildirimler sessize alınsın",
            "Ofis bilgisayarında tarayıcı işçisi kurulu değil",
            "Laptop şarja takılıyken uyumasın",
        ],
    ),
    (
        "Sabahları ne içerim?",
        "Sahip sabahları kahve içmeyi sever",
        [
            "Sahip akşamları çay içiyor",
            "Sabah alarmı yedide çalsın",
            "Sahip sabah haberlerini kahvaltıda dinliyor",
            "Kahve makinesi salı günü servise gidecek",
        ],
    ),
    (
        "Hangi müziği sık açtırıyorum?",
        "Sahip 'lofi çalışma müziği' medyasını sık sık açtırıyor (5 kez istedi).",
        [
            "Sahip müzik açıkken ses seviyesinin yüzde otuz olmasını istiyor",
            "YouTube videoları medya oynatıcıda değil Chrome'da açılsın",
            "Sahip 'yapay zeka haberleri' konusunu düzenli olarak araştırıyor (4 kez sordu).",
            "Alarm sesi olarak piyano melodisi seçildi",
        ],
    ),
    (
        "Bildirimler kaçtan sonra gelmesin?",
        "Akşam dokuzdan sonra bildirim gelmesin",
        [
            "Görev bitince kısa bir bildirim yeter, uzun sonucu bekletsin",
            "Akşam dokuzda günün özeti okunsun",
            "Ofiste çalışırken bildirimler sessize alınsın",
            "Sabah yediden önce alarm dışında ses çıkmasın",
        ],
    ),
    (
        "Yedek ne zaman alınıyor?",
        "Postgres yedeği gece yarısından sonra alınır",
        [
            "Geri yükleme tatbikatı pazar günleri yapılır",
            "Yedeğin sunucu dışında bir kopyası yok",
            "Sunucuda disk alanı azaldığında haber verilsin",
            "Gece yarısından sonra güncelleme kurulmasın",
        ],
    ),
    (
        "Mail gönderirken onay soruyor mu?",
        "Mail gönderiminden önce sesli onay istenir, başka hiçbir işte ikinci onay sorulmaz",
        [
            "Gmail ve Outlook postaları Core'da yönetilir",
            "Sahip uzun mailleri özet olarak dinlemek istiyor",
            "Takvim davetleri onay beklemeden kabul edilmesin",
            "Mail imzasında şirket adı geçsin",
        ],
    ),
]

#: Models fastembed does not list are registered from their ONNX export. `file` is the
#: ONNX inside the repository; `extra` are files that must come with it.
CANDIDATES: dict[str, dict[str, object]] = {
    "bge-reranker-v2-m3 (fp32)": {
        "name": "onnx-community/bge-reranker-v2-m3-ONNX",
        "hf": "onnx-community/bge-reranker-v2-m3-ONNX",
        "file": "onnx/model.onnx",
        "extra": ["onnx/model.onnx_data"],
        "licence": "apache-2.0 (BAAI/bge-reranker-v2-m3)",
        "size_gb": 2.27,
    },
    "bge-reranker-v2-m3 (int8)": {
        "name": "onnx-community/bge-reranker-v2-m3-ONNX#int8",
        "hf": "onnx-community/bge-reranker-v2-m3-ONNX",
        "file": "onnx/model_int8.onnx",
        "extra": [],
        "licence": "apache-2.0 (BAAI/bge-reranker-v2-m3)",
        "size_gb": 0.57,
    },
    "jina-reranker-v2-base-multilingual (fp32)": {
        "name": "jinaai/jina-reranker-v2-base-multilingual",
        "hf": None,  # listed by fastembed
        "licence": "cc-by-nc-4.0 (NON-COMMERCIAL)",
        "size_gb": 1.11,
    },
    "jina-reranker-v2-base-multilingual (int8)": {
        "name": "jinaai/jina-reranker-v2-base-multilingual#int8",
        "hf": "jinaai/jina-reranker-v2-base-multilingual",
        "file": "onnx/model_int8.onnx",
        "extra": [],
        "licence": "cc-by-nc-4.0 (NON-COMMERCIAL)",
        "size_gb": 0.28,
    },
}


def _rss() -> tuple[float, float]:
    """(working set, peak working set) of this process in MB."""
    import ctypes
    import ctypes.wintypes as wt

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wt.DWORD),
            ("PageFaultCount", wt.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    function = ctypes.windll.kernel32.K32GetProcessMemoryInfo
    function.argtypes = [wt.HANDLE, ctypes.POINTER(Counters), wt.DWORD]
    function.restype = wt.BOOL
    if not function(
        ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise OSError("K32GetProcessMemoryInfo failed")
    mb = 1024 * 1024
    return round(counters.WorkingSetSize / mb, 1), round(
        counters.PeakWorkingSetSize / mb, 1
    )


def _rank_of_right(scores: list[float]) -> int:
    """1-based rank of candidate 0 (the right memory); a tie counts against it."""
    right = scores[0]
    return 1 + sum(1 for other in scores[1:] if other >= right)


def _summary(ranks: list[int]) -> dict[str, float]:
    return {
        "top1": round(sum(1 for r in ranks if r == 1) / len(ranks), 3),
        "mrr": round(sum(1.0 / r for r in ranks) / len(ranks), 3),
        "worst_rank": max(ranks),
    }


def measure_baseline() -> dict[str, object]:
    """The ranking the serving embedder makes of the same five memories."""
    from app.memory.embedding import cosine_similarity
    from app.memory.providers import DEFAULT_LOCAL_MODEL, LocalEmbedder

    embedder = LocalEmbedder(model_name=DEFAULT_LOCAL_MODEL)
    ranks = []
    for query, right, wrong in CASES:
        q = embedder.embed(query)
        ranks.append(
            _rank_of_right(
                [cosine_similarity(q, embedder.embed(t)) for t in [right, *wrong]]
            )
        )
    return {
        "label": f"embedder only ({embedder.model_id})",
        "ranks": ranks,
        **_summary(ranks),
    }


def measure_model(label: str, cache_dir: str | None) -> dict[str, object]:
    from app.memory.providers import DEFAULT_LOCAL_MODEL, LocalEmbedder
    from fastembed.common.model_description import ModelSource
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    spec = CANDIDATES[label]
    # The API process this model would live in already holds the embedder (ADR-0200) and
    # the ONNX runtime. Both are loaded BEFORE the first reading, so the increase below is
    # what the reranker ADDS to a colour, not what a bare Python would cost.
    embedder = LocalEmbedder(model_name=DEFAULT_LOCAL_MODEL)
    embedder.embed("ısınma")
    before, _ = _rss()
    if spec.get("hf"):
        TextCrossEncoder.add_custom_model(
            model=str(spec["name"]),
            sources=ModelSource(hf=str(spec["hf"])),
            model_file=str(spec["file"]),
            additional_files=list(spec.get("extra") or []),  # type: ignore[arg-type]
            license=str(spec["licence"]),
            size_in_gb=float(spec["size_gb"]),  # type: ignore[arg-type]
        )
    started = time.perf_counter()
    model = TextCrossEncoder(model_name=str(spec["name"]), cache_dir=cache_dir)
    list(model.rerank("ısınma", ["ilk çift"]))
    load_s = time.perf_counter() - started

    ranks: list[int] = []
    pair_ms: list[float] = []
    for query, right, wrong in CASES:
        documents = [right, *wrong]
        t0 = time.perf_counter()
        scores = [float(s) for s in model.rerank(query, documents)]
        pair_ms.append((time.perf_counter() - t0) * 1000 / len(documents))
        ranks.append(_rank_of_right(scores))

    # What a voice turn pays: one query against the default top-K.
    twenty = [text for _, right, wrong in CASES[:4] for text in [right, *wrong]]
    t0 = time.perf_counter()
    list(model.rerank(CASES[0][0], twenty))
    top20_ms = (time.perf_counter() - t0) * 1000

    after, peak = _rss()
    pair_ms.sort()
    return {
        "label": label,
        "model": spec["name"],
        "licence": spec["licence"],
        "download_gb": spec["size_gb"],
        "load_s": round(load_s, 2),
        "pair_ms_median": round(pair_ms[len(pair_ms) // 2], 1),
        "pair_ms_slowest": round(pair_ms[-1], 1),
        "top20_ms": round(top20_ms, 0),
        "rss_before_mb": before,
        "rss_after_mb": after,
        "rss_increase_mb": round(after - before, 1),
        "rss_peak_mb": peak,
        "ranks": ranks,
        **_summary(ranks),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Turkish memory rerank benchmark (ADR-0206)"
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--only", default=None, help="measure ONE label and print JSON (child)"
    )
    args = parser.parse_args(argv[1:])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.only is not None:
        result = (
            measure_baseline()
            if args.only == "baseline"
            else measure_model(args.only, args.cache_dir)
        )
        print("RESULT " + json.dumps(result, ensure_ascii=False))
        return 0

    results: list[dict[str, object]] = []
    for label in ["baseline", *CANDIDATES]:
        command = [sys.executable, str(Path(__file__).resolve()), "--only", label]
        if args.cache_dir:
            command += ["--cache-dir", args.cache_dir]
        child = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", check=False
        )
        line = next(
            (ln for ln in child.stdout.splitlines() if ln.startswith("RESULT ")), None
        )
        if child.returncode != 0 or line is None:
            reason = (child.stderr.strip().splitlines() or ["no output"])[-1][:200]
            results.append({"label": label, "error": reason})
            print(f"{label}: ÖLÇÜLEMEDİ - {reason}")
            continue
        results.append(json.loads(line[len("RESULT ") :]))

    print(f"\n{len(CASES)} sorgu, her biri 1 doğru + 4 yakın-yanlış hafıza\n")
    header = f"{'model':46} {'top-1':>6} {'MRR':>6} {'en kötü':>8} {'yükleme':>9} {'çift':>9} {'20 aday':>9} {'RSS artışı':>11} {'tepe':>8}"
    print(header)
    for r in results:
        if "error" in r:
            continue
        if "load_s" not in r:
            print(
                f"{str(r['label'])[:46]:46} {r['top1']:>6} {r['mrr']:>6} {r['worst_rank']:>8}"
            )
            continue
        print(
            f"{str(r['label'])[:46]:46} {r['top1']:>6} {r['mrr']:>6} {r['worst_rank']:>8} "
            f"{r['load_s']:>7} s {r['pair_ms_median']:>6} ms {r['top20_ms']:>6} ms "
            f"{r['rss_increase_mb']:>8} MB {r['rss_peak_mb']:>5} MB"
        )
    if args.out is not None:
        document = {
            "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "host": {
                "platform": platform.platform(),
                "python": platform.python_version(),
            },
            "cases": len(CASES),
            "candidates_per_case": 5,
            "results": results,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", "utf-8"
        )
        print(f"\nkanıt: {args.out.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
