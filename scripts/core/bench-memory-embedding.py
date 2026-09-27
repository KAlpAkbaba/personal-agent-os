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
        Qwen/Qwen3-Embedding-0.6B-Q

Prints one table per model. A model that is not natively 256 wide and not on the
Matryoshka allowlist is reported as refused, exactly as the API would refuse it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.memory.embedding import DeterministicEmbedder, cosine_similarity  # noqa: E402
from app.memory.providers import (  # noqa: E402
    DEFAULT_LOCAL_MODEL,
    EmbeddingProviderError,
    LocalEmbedder,
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


def _table(name: str, embed, load_s: float | None) -> None:
    print(f"\n== {name}" + (f"  (yükleme {load_s:.1f} s)" if load_s is not None else ""))
    print(f"{'':10} {'cosine':>7}   {'ms':>5}   çift")
    same: list[float] = []
    other: list[float] = []
    for a, b, label in PAIRS:
        t0 = time.perf_counter()
        score = cosine_similarity(embed(a), embed(b))
        ms = (time.perf_counter() - t0) * 1000
        (same if label == "ANLAMDAŞ" else other).append(score)
        print(f"{label:10} {score:7.3f}   {ms:5.0f}   {a[:34]!r} ~ {b[:34]!r}")
    sep = sum(same) / len(same) - sum(other) / len(other)
    print(
        f"AYRIM (anlamdaş ort. − alakasız ort.): {sep:+.3f}   "
        f"[anlamdaş {sum(same) / len(same):.3f} | alakasız {sum(other) / len(other):.3f}]"
    )


def main(argv: list[str]) -> int:
    models = argv[1:] or [DEFAULT_LOCAL_MODEL]
    det = DeterministicEmbedder()
    _table("deterministic n-gram (ADR-0200 öncesi üretim)", det.embed, None)
    for model in models:
        t0 = time.perf_counter()
        try:
            local = LocalEmbedder(model_name=model)
        except EmbeddingProviderError as exc:
            print(f"\n== {model}: REDDEDİLDİ — {exc}")
            continue
        load_s = time.perf_counter() - t0
        note = f" [kırpıldı {local.native_dim}→{local.dim}]" if local.truncated else ""
        _table(f"{model}{note} → model_id {local.model_id}", local.embed, load_s)
    print("\nOkuma: AYRIM ne kadar büyükse hafıza 'aynı şeyi başka sözle' o kadar iyi bulur.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
