"""ADR-0200 — what the local memory embedder costs the owner: time and memory.

`bench-memory-embedding.py` answers "does it find the same thing said differently".
This answers the other half of the same decision, the half a table of cosines cannot
show: how long a retrieval waits for an embedding, and how much of the host the model
holds while it waits. Both are release facts. The model lives INSIDE the API process
(`MemoryRuntime` builds the embedder at construction, one uvicorn worker per container),
so the number to read is this process's working set before and after the model is built,
and a blue-green release runs two of them at once.

What is measured, in the order production meets it:

* **warm load** — the model is already in the fastembed cache, which is what production
  sees: the release script prefetches it into the shared volume before the idle colour
  starts. A cold first download is a release-script concern (exit 85), not a turn's cost.
* **one embedding** — first, then the median and slowest of a batch. A voice turn pays
  one; the `memory_index` retention sweep pays `memory_index_fill_batch` of them.
* **working set** — before the model, after a first embedding, and the process peak,
  read through the Win32 API (no psutil in this venv).

Run from the repository root, in the API's environment:

    services/api/.venv/Scripts/python.exe scripts/core/measure-embedder-cost.py
    services/api/.venv/Scripts/python.exe scripts/core/measure-embedder-cost.py \
        Qwen/Qwen3-Embedding-0.6B-Q --out docs/evidence/adr-0200-qwen.json

Prints a short table and, with `--out`, writes the same numbers as an evidence artifact
so a QUALIFICATION row can point at something instead of asserting something.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "api"))

#: Sentences of the shape owner memory actually holds - a preference, a fact, a routine.
#: The point is the cost of embedding Turkish prose, so they are not one repeated string.
SENTENCES: tuple[str, ...] = (
    "Sahip sabahları kahve içmeyi sever",
    "Araştırma raporlarını her zaman Türkçe oku",
    "Nişanlımın adı Melissa",
    "Ekranlar 15 dakika sonra kapansın",
    "İş bilgisayarımın adı MAIL",
    "Yapay zeka haberlerini düzenli araştırıyorum",
    "Aktivra benim kurduğum şirket",
    "Akşam dokuzdan sonra bildirim gelmesin",
)


class _Counters(ctypes.Structure):
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


def _read_counters() -> _Counters:
    """kernel32's ``K32GetProcessMemoryInfo`` first: psapi.dll's export is a forwarder
    ctypes cannot always bind on current Windows, and the failed call silently reported
    0 MB - a measurement that lies is worse than one that refuses."""
    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    for dll, name in (
        (ctypes.windll.kernel32, "K32GetProcessMemoryInfo"),
        (ctypes.windll.psapi, "GetProcessMemoryInfo"),
    ):
        function = getattr(dll, name, None)
        if function is None:
            continue
        function.argtypes = [wt.HANDLE, ctypes.POINTER(_Counters), wt.DWORD]
        function.restype = wt.BOOL
        if function(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            return counters
    raise OSError("GetProcessMemoryInfo failed through both kernel32 and psapi")


def _mb(value: int) -> float:
    return round(value / (1024 * 1024), 1)


def measure(model_name: str) -> dict[str, object]:
    from app.config import Settings
    from app.memory.providers import LocalEmbedder

    before = _read_counters()
    started = time.perf_counter()
    embedder = LocalEmbedder(model_name=model_name)
    load_s = time.perf_counter() - started

    first = time.perf_counter()
    vector = embedder.embed(SENTENCES[0])
    first_ms = (time.perf_counter() - first) * 1000

    samples: list[float] = []
    for sentence in SENTENCES:
        started_one = time.perf_counter()
        embedder.embed(sentence)
        samples.append((time.perf_counter() - started_one) * 1000)
    samples.sort()
    median_ms = samples[len(samples) // 2]

    after = _read_counters()
    batch = Settings().memory_index_fill_batch
    return {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "model_name": model_name,
        "model_id": embedder.model_id,
        "dim": embedder.dim,
        "native_dim": embedder.native_dim,
        "truncated": embedder.truncated,
        "semantic": True,
        "vector_len": len(vector),
        "warm_load_s": round(load_s, 2),
        "first_embed_ms": round(first_ms, 1),
        "embed_median_ms": round(median_ms, 2),
        "embed_slowest_ms": round(samples[-1], 2),
        "embed_samples": len(samples),
        "retention_batch_rows": batch,
        "retention_batch_s": round(median_ms * batch / 1000, 2),
        "rss_before_mb": _mb(before.WorkingSetSize),
        "rss_after_mb": _mb(after.WorkingSetSize),
        "rss_peak_mb": _mb(after.PeakWorkingSetSize),
    }


def main(argv: list[str]) -> int:
    from app.memory.providers import DEFAULT_LOCAL_MODEL

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", default=DEFAULT_LOCAL_MODEL)
    parser.add_argument("--out", type=Path, default=None, help="write the numbers as JSON")
    args = parser.parse_args(argv[1:])

    # A console that cannot print "ısınmış" must not be able to fail a measurement that
    # already ran: Windows hands a piped stdout cp1252 by default.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    result = measure(args.model)
    print(
        f"{result['model_id']}  (boyut {result['dim']}"
        f"{', native ' + str(result['native_dim']) if result['truncated'] else ''})\n"
        f"  ısınmış yükleme : {result['warm_load_s']:>8} s\n"
        f"  ilk embed       : {result['first_embed_ms']:>8} ms\n"
        f"  embed ortanca   : {result['embed_median_ms']:>8} ms"
        f"   (en yavaş {result['embed_slowest_ms']} ms, {result['embed_samples']} cümle)\n"
        f"  {result['retention_batch_rows']} satırlık sağaltım kümesi"
        f" : {result['retention_batch_s']} s\n"
        f"  süreç belleği   : {result['rss_before_mb']} MB -> {result['rss_after_mb']} MB"
        f"   (tepe {result['rss_peak_mb']} MB)"
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", "utf-8")
        print(f"  kanıt           : {args.out.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
