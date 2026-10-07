"""Card memory-lexical-turkish-measure: the memory's word leg, ``like`` against ``trgm``.

Loads the fixed Turkish set (``services/api/tests/fixtures/memory_lexical/names_tr.json``:
invented names, places, companies, plates and promises, each with one right memory and
look-alikes) into a SCRATCH PostgreSQL database, runs ``retrieval.hybrid_search`` once per
question in each ``PAGENTOS_MEMORY_LEXICAL`` mode, and writes for each mode the share of
questions whose right memory is first / in the top 3 / in the top 10, the MRR and the query
time p50/p95/max. Each seed's order is loaded ``--draws`` times with fresh ids (equal scores
break on the id, so one load is one draw) and the draws are pooled. ``decide_default`` then
reads the card's rule on the pool:

    trgm becomes the default when its top-3 share is HIGHER than like's, every question
    that like had in its top 3 and trgm did not, in ANY one load, is listed with its rate
    and accepted with a reason (``--accept-lost id:reason``, or by class:
    ``--accept-lost-class id_tie:reason``), and trgm's p95 is at most 50 ms slower
    (``RULE_TEXT``).

The database address comes from the environment (``Settings().database_url``); the script
creates ``pagentos_bench_lexical_<8hex>`` on that server, migrates it to head, and drops it
in ``finally`` (``--keep-db`` keeps it). The shared dev database is never written. No
password is printed or written. The decision is read from the LOCAL embedder (the one
production serves, ADR-0200); the deterministic n-gram embedder imitates a word match by
itself, so its rows are reported but never decide.

Run from the repository root (Windows, the project venv), through the test queue:

    services\\api\\.venv\\Scripts\\python.exe scripts\\core\\bench-memory-lexical.py \\
        --out docs/evidence/memory-lexical-turkish-measure.json

The only network call is the local model's first download from huggingface.co, into
``--cache-dir`` (default ``%LOCALAPPDATA%/PagentOS/fastembed-cache``; never the repository
or %TEMP%).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import random
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.memory import lexical, service  # noqa: E402
from app.memory.embedding import DeterministicEmbedder, Embedder  # noqa: E402
from app.memory.models import Memory  # noqa: E402
from app.memory.retrieval import RetrievalFilters, hybrid_search  # noqa: E402
from app.memory.service import MemoryLinks  # noqa: E402
from app.memory.types import Actor, MemoryClass  # noqa: E402

DEFAULT_SET = ROOT / "services" / "api" / "tests" / "fixtures" / "memory_lexical" / "names_tr.json"
MODES: tuple[str, str] = ("like", "trgm")
MIN_CASES = 30
MIN_SHAPE = 3
HARD_TAGS = ("dotted_i", "suffix", "typo")
KNOWN_TAGS = frozenset({*HARD_TAGS, "verb", "plate", "company", "place", "person"})
#: Names from the owner's real life and machines; the set is invented, always.
BANNED_WORDS = ("aktivra", "akbaba", "kadir", "melissa", "gmkadirakbaba", "alp")
#: The card's ceiling on what trgm may add to the p95, and the band reported as borderline.
P95_BUDGET_MS = 50.0
BORDERLINE_MS = (40.0, 60.0)
SCRATCH_PREFIX = "pagentos_bench_lexical_"
#: A fixed clock for ``hybrid_search``'s recency term, so a run does not age its own corpus.
FIXED_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


class BenchError(RuntimeError):
    """The corpus did not load as the set says (a merged memory, a wrong count)."""


# --------------------------------------------------------------------------- the set


def load_cases(path: Path = DEFAULT_SET) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_cases(data: dict[str, Any]) -> list[str]:
    """Everything wrong with the set, one line each; [] when it is fit to measure with."""
    errors: list[str] = []
    cases = data.get("cases") or []
    noise = data.get("noise") or []
    if len(cases) < MIN_CASES:
        errors.append(f"{len(cases)} questions; the card needs at least {MIN_CASES}")
    for what, values in (
        ("id", [c.get("id") for c in cases]),
        ("query", [c.get("query") for c in cases]),
        (
            "memory text",
            [t for c in cases for t in [c.get("answer"), *c.get("distractors", [])]] + noise,
        ),
    ):
        seen: set[Any] = set()
        for value in values:
            if value in seen:
                errors.append(f"duplicate {what}: {value!r}")
            seen.add(value)
    for case in cases:
        cid = case.get("id")
        if not str(case.get("answer") or "").strip():
            errors.append(f"{cid}: no answer")
        if case.get("answer") in case.get("distractors", []):
            errors.append(f"{cid}: the answer is also a distractor")
        if len(case.get("distractors", [])) < 3:
            errors.append(f"{cid}: fewer than 3 distractors")
        unknown = set(case.get("tags", [])) - KNOWN_TAGS
        if unknown:
            errors.append(f"{cid}: unknown tags {sorted(unknown)}")
        errors.extend(_tag_lies(case))
    for tag in HARD_TAGS:
        count = sum(1 for c in cases if tag in c.get("tags", []))
        if count < MIN_SHAPE:
            errors.append(f"{count} '{tag}' questions; the card needs at least {MIN_SHAPE}")
    texts = [t for c in cases for t in [c.get("query"), c.get("answer"), *c.get("distractors", [])]]
    for text in [*texts, *noise]:
        folded = lexical.fold(str(text or ""))
        for word in BANNED_WORDS:
            if re.search(rf"\b{word}\b", folded):
                errors.append(f"banned real name {word!r} in {text!r}")
    return errors


def _tag_lies(case: dict[str, Any]) -> list[str]:
    """A tag the question's own text does not bear out."""
    cid, query, tags = case.get("id"), str(case.get("query") or ""), case.get("tags", [])
    out: list[str] = []
    if "dotted_i" in tags and not re.search("[İI]", query):
        out.append(f"{cid}: tagged dotted_i but the question has no İ/I")
    if "suffix" in tags and not re.search(r"\w'\w", query):
        out.append(f"{cid}: tagged suffix but the question has no apostrophe suffix")
    if "typo" in tags:
        wrong, right = str(case.get("typo") or ""), str(case.get("typo_of") or "")
        if not wrong or not right:
            out.append(f"{cid}: tagged typo without typo/typo_of")
        elif not (
            lexical.fold(wrong) in lexical.fold(query)
            and lexical.fold(right) not in lexical.fold(query)
            and lexical.fold(right) in lexical.fold(str(case.get("answer") or ""))
        ):
            out.append(f"{cid}: typo {wrong!r} -> {right!r} is not what the texts say")
    return out


# --------------------------------------------------------------------------- arithmetic


def rank_of(result_ids: Sequence[Any], answer_id: Any) -> int | None:
    """1-based place of ``answer_id`` in ``result_ids``; None when it is not there."""
    for place, result_id in enumerate(result_ids, start=1):
        if result_id == answer_id:
            return place
    return None


def top_k_rate(ranks: Iterable[int | None], k: int) -> float:
    values = list(ranks)
    if not values:
        return 0.0
    return sum(1 for r in values if r is not None and r <= k) / len(values)


def mrr(ranks: Iterable[int | None]) -> float:
    values = list(ranks)
    if not values:
        return 0.0
    return sum(1.0 / r for r in values if r is not None) / len(values)


def percentile(values: Iterable[float], p: float) -> float:
    """numpy's ``percentile(values, p)`` with the default linear method, without numpy."""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise ValueError("percentile of no values")
    position = (len(ordered) - 1) * (float(p) / 100.0)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(mode: str, ranks: dict[str, int | None], timings_ms: Sequence[float]) -> dict:
    values = list(ranks.values())
    return {
        "mode": mode,
        "questions": len(values),
        "top1": top_k_rate(values, 1),
        "top3": top_k_rate(values, 3),
        "top10": top_k_rate(values, 10),
        "mrr": mrr(values),
        "p50_ms": percentile(timings_ms, 50),
        "p95_ms": percentile(timings_ms, 95),
        "max_ms": max(float(t) for t in timings_ms),
        "timed_queries": len(timings_ms),
        "ranks": dict(ranks),
    }


def pool_draws(mode: str, draws: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One mode over several draws (fresh loads, fresh ids) of the same corpus. Rates are
    the mean of the draws' rates (with their min/max), the timings are pooled, and
    ``top3_share`` is the share of draws that had each question in the top 3."""
    if not draws:
        raise ValueError("pool of no draws")
    ranks = [dict(d["ranks"]) for d in draws]
    timings = [float(t) for d in draws for t in d["timings_ms"]]
    per_draw_top3 = [top_k_rate(r.values(), 3) for r in ranks]
    questions = list(ranks[0])

    def mean(values: Sequence[float]) -> float:
        return sum(values) / len(values)

    return {
        "mode": mode,
        "draws": len(draws),
        "questions": len(questions),
        "top1": mean([top_k_rate(r.values(), 1) for r in ranks]),
        "top3": mean(per_draw_top3),
        "top10": mean([top_k_rate(r.values(), 10) for r in ranks]),
        "mrr": mean([mrr(r.values()) for r in ranks]),
        "top3_min": min(per_draw_top3),
        "top3_max": max(per_draw_top3),
        "top3_per_draw": per_draw_top3,
        "top3_share": {
            cid: sum(1 for r in ranks if _in_top3(r.get(cid))) / len(ranks) for cid in questions
        },
        "ranks_per_draw": {cid: [r.get(cid) for r in ranks] for cid in questions},
        "p50_ms": percentile(timings, 50),
        "p95_ms": percentile(timings, 95),
        "max_ms": max(timings),
        "timed_queries": len(timings),
        "timings_ms": [round(t, 3) for t in timings],
    }


def _in_top3(rank: int | None) -> bool:
    return rank is not None and rank <= 3


#: The card's rule, word for word what ``decide_default`` checks; written into the decision.
RULE_TEXT = (
    "trgm varsayılan olur, ancak: (1) trgm'in ilk-3 oranı (yüklemelerin ortalaması) like'tan "
    "yüksekse; (2) herhangi bir yüklemede like'ın ilk-3'ünde olup aynı yüklemede trgm'in "
    "ilk-3'ünden düşen her soru, düştüğü yükleme oranıyla lost_in_trgm'e yazılır ve her biri "
    "için kabul gerekçesi (soru kimliğiyle ya da sınıfıyla) varsa; (3) trgm'in p95'i like'tan "
    "en çok 50 ms fazlaysa. Biri tutmazsa like kalır."
)
#: A drop whose question was in trgm's top 3 in another load of the same seed: same texts,
#: same order, only the ids differ, so the drop is an equal score broken on the id.
ID_TIE = "id_tie"
#: Dropped in every load of a seed where it dropped at all: not explained by the ids.
CONSISTENT = "consistent"


def _ranks_per_draw(summary: dict[str, Any]) -> dict[str, list[int | None]]:
    if "ranks_per_draw" in summary:
        return {cid: list(ranks) for cid, ranks in summary["ranks_per_draw"].items()}
    return {cid: [rank] for cid, rank in summary["ranks"].items()}


def _drops(like: dict[str, Any], trgm: dict[str, Any]) -> dict[str, list[bool]]:
    """Per question, per paired draw (one load, both modes): like top 3, trgm not."""
    like_ranks, trgm_ranks = _ranks_per_draw(like), _ranks_per_draw(trgm)
    return {
        cid: [
            _in_top3(lr) and not _in_top3(tr)
            for lr, tr in zip(ranks, trgm_ranks.get(cid, [None] * len(ranks)), strict=True)
        ]
        for cid, ranks in like_ranks.items()
    }


def _top3_any(summary: dict[str, Any]) -> set[str]:
    return {cid for cid, ranks in _ranks_per_draw(summary).items() if any(map(_in_top3, ranks))}


def decide_default(
    like: dict[str, Any],
    trgm: dict[str, Any],
    *,
    accepted_lost: dict[str, str] | None = None,
    accepted_classes: dict[str, str] | None = None,
    classes: dict[str, str] | None = None,
    budget_ms: float = P95_BUDGET_MS,
) -> dict[str, Any]:
    """``RULE_TEXT`` on two ``summarize`` (one draw) or ``pool_draws`` results (the same
    loads, in the same order). ``outcome``: ``trgm`` / ``trgm_with_accepted_losses``
    (switch), ``not_better`` / ``lost`` / ``slow`` (stay on like, and why). A drop is
    accepted by its id (``accepted_lost``) or by its class (``classes`` -> ``accepted_classes``)."""
    by_id = dict(accepted_lost or {})
    by_class = dict(accepted_classes or {})
    known = dict(classes or {})
    lost: list[dict[str, Any]] = []
    accepted: dict[str, str] = {}
    for cid, drops in sorted(_drops(like, trgm).items()):
        if not any(drops):
            continue
        cls = known.get(cid, "unclassified")
        lost.append(
            {
                "id": cid,
                "lost_draws": sum(drops),
                "draws": len(drops),
                "rate": sum(drops) / len(drops),
                "class": cls,
            }
        )
        if cid in by_id:
            accepted[cid] = by_id[cid]
        elif cls in by_class:
            accepted[cid] = f"{cls}: {by_class[cls]}"
    unaccepted = [e["id"] for e in lost if e["id"] not in accepted]
    gained = sorted(_top3_any(trgm) - _top3_any(like))
    delta = float(trgm["p95_ms"]) - float(like["p95_ms"])
    shares = f"ilk-3: like {like['top3']:.3f}, trgm {trgm['top3']:.3f}"
    if not trgm["top3"] > like["top3"]:
        outcome, default = "not_better", "like"
        reason = f"trgm ilk-3 oranında like'tan iyi değil ({shares}); like kalır."
    elif unaccepted:
        outcome, default = "lost", "like"
        reason = (
            f"like'ta ilk-3'te olup trgm'de düşen ve kabul gerekçesi olmayan soru var "
            f"({', '.join(unaccepted)}); like kalır."
        )
    elif delta > budget_ms:
        outcome, default = "slow", "like"
        reason = f"trgm p95 {delta:+.1f} ms ek süre, sınır {budget_ms:g} ms; like kalır."
    else:
        outcome = "trgm_with_accepted_losses" if lost else "trgm"
        default = "trgm"
        reason = (
            f"trgm ilk-3'te daha iyi ({shares}), düşen soru "
            f"{'yok' if not lost else f'{len(lost)}, hepsi kabul gerekçeli'}, p95 ek süre "
            f"{delta:+.1f} ms (sınır {budget_ms:g} ms): varsayılan trgm."
        )
    verdict = {
        "trgm": "trgm",
        "trgm_with_accepted_losses": "trgm, kabul edilen düşüşlerle",
    }.get(outcome, "like")
    return {
        "default": default,
        "outcome": outcome,
        "verdict": verdict,
        "rule": RULE_TEXT,
        "reasons": [reason],
        "lost_in_trgm": lost,
        "gained_in_trgm": gained,
        "accepted_lost": accepted,
        "accepted_classes": {c: why for c, why in by_class.items() if c in set(known.values())},
        "p95_delta_ms": delta,
        "borderline": BORDERLINE_MS[0] <= delta <= BORDERLINE_MS[1],
    }


# --------------------------------------------------------------------------- corpus and runs


@dataclass(slots=True)
class Corpus:
    conversation_id: uuid.UUID
    answers: dict[str, uuid.UUID] = field(default_factory=dict)
    ids: list[uuid.UUID] = field(default_factory=list)
    loaded: int = 0
    seed: int = 0

    @property
    def filters(self) -> RetrievalFilters:
        return RetrievalFilters(conversation_id=self.conversation_id)


def retire_corpus(session: Any, corpus: Corpus) -> None:
    """Forget every memory of ``corpus``. The write path's dedup reads every ACTIVE memory
    of the class, not one conversation's, so the next seed's copy of a text would be merged
    into this one ('corroborated') - the first PostgreSQL run stopped on exactly that."""
    for memory_id in corpus.ids:
        service.forget_memory(session, memory_id, actor=Actor.OWNER, reason="bench_retire")
    corpus.ids.clear()


def load_corpus(session: Any, embedder: Embedder, data: dict[str, Any], *, seed: int) -> Corpus:
    """Every memory of the set under ONE new conversation, in a seeded shuffled order (the
    like leg is newest-first, so order matters and is reported). The right memory's id is
    what ``remember_explicit`` returned - never looked up by its text. A memory merged
    into another (dedup) stops the run: the set would no longer be what it says."""
    rows: list[tuple[str | None, str]] = []
    for case in data["cases"]:
        rows.append((case["id"], case["answer"]))
        rows.extend((None, text) for text in case["distractors"])
    rows.extend((None, text) for text in data.get("noise", []))
    random.Random(seed).shuffle(rows)
    corpus = Corpus(conversation_id=uuid.uuid4(), seed=seed)
    links = MemoryLinks(conversation_id=corpus.conversation_id)
    for case_id, text in rows:
        result = service.remember_explicit(
            session, embedder, text=text, memory_class=MemoryClass.SEMANTIC, links=links
        )
        if result.action != "created" or result.memory_id is None:
            raise BenchError(f"not created ({result.action}): {text!r}")
        if case_id is not None:
            corpus.answers[case_id] = result.memory_id
        corpus.ids.append(result.memory_id)
        corpus.loaded += 1
    session.commit()
    from sqlalchemy import func, select

    stored = session.execute(
        select(func.count())
        .select_from(Memory)
        .where(Memory.conversation_id == corpus.conversation_id)
    ).scalar_one()
    if stored != len(rows):
        raise BenchError(f"{stored} memories stored, the set has {len(rows)}")
    return corpus


@contextlib.contextmanager
def _mode_env(mode: str) -> Iterator[None]:
    before = os.environ.get(lexical.MODE_ENV)
    os.environ[lexical.MODE_ENV] = mode
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(lexical.MODE_ENV, None)
        else:
            os.environ[lexical.MODE_ENV] = before


def run_mode(
    session: Any,
    embedder: Embedder,
    cases: Sequence[dict[str, Any]],
    corpus: Corpus,
    mode: str,
    *,
    k: int = 10,
) -> dict[str, Any]:
    """Each question once through ``hybrid_search`` in ``mode``: its right memory's rank and
    the call's wall time in ms (``perf_counter_ns``)."""
    ranks: dict[str, int | None] = {}
    timings: list[float] = []
    with _mode_env(mode):
        for case in cases:
            started = time.perf_counter_ns()
            results = hybrid_search(
                session, embedder, case["query"], corpus.filters, k=k, now=FIXED_NOW
            )
            timings.append((time.perf_counter_ns() - started) / 1e6)
            ranks[case["id"]] = rank_of([r.memory.id for r in results], corpus.answers[case["id"]])
    return {"mode": mode, "ranks": ranks, "timings_ms": timings}


def measure(
    session: Any,
    embedder: Embedder,
    cases: Sequence[dict[str, Any]],
    corpus: Corpus,
    *,
    rounds: int,
    warmup: int,
) -> dict[str, Any]:
    """``warmup`` + ``rounds`` rounds, the two modes in turn (like-trgm, then trgm-like), so
    neither mode always runs on a warmer cache. Within one load the ranks are fixed; a
    round whose ranks differ from the first is reported, never hidden. Across loads they
    are not (equal scores break on the uuid4 id): that is ``measure_draws``'s job."""
    timings: dict[str, list[float]] = {m: [] for m in MODES}
    first: dict[str, dict[str, int | None]] = {}
    unstable: dict[str, list[str]] = {m: [] for m in MODES}
    for index in range(warmup + rounds):
        order = MODES if index % 2 == 0 else tuple(reversed(MODES))
        for mode in order:
            out = run_mode(session, embedder, cases, corpus, mode)
            if mode not in first:
                first[mode] = out["ranks"]
            elif out["ranks"] != first[mode]:
                unstable[mode].append(f"round {index}")
            if index >= warmup:
                timings[mode].extend(out["timings_ms"])
    summaries = {m: summarize(m, first[m], timings[m]) for m in MODES}
    for mode in MODES:
        summaries[mode]["unstable_rounds"] = unstable[mode]
        summaries[mode]["timings_ms"] = [round(t, 3) for t in timings[mode]]
    return summaries


def measure_draws(
    session: Any,
    embedder: Embedder,
    data: dict[str, Any],
    *,
    seed: int,
    draws: int,
    rounds: int,
    warmup: int,
) -> dict[str, Any]:
    """``draws`` fresh loads of the corpus in ``seed``'s order, each measured and retired.
    hybrid_search breaks equal scores on ``str(memory.id)`` and every load draws new uuid4
    ids, so one load is one draw of those ties (the inspector, f7845e62: the same seed
    loaded twice moved trgm's rank on 11 of 33 questions). Every draw is kept; each mode
    is also pooled over them (``pool_draws``)."""
    kept: list[dict[str, Any]] = []
    for _ in range(draws):
        started = time.perf_counter()
        corpus = load_corpus(session, embedder, data, seed=seed)
        load_s = round(time.perf_counter() - started, 2)
        out = measure(session, embedder, data["cases"], corpus, rounds=rounds, warmup=warmup)
        out["load_s"] = load_s
        out["memories"] = corpus.loaded
        out["answer_ids"] = {cid: str(mid) for cid, mid in corpus.answers.items()}
        retire_corpus(session, corpus)
        session.commit()
        kept.append(out)
    return {
        "draws": kept,
        **{mode: pool_draws(mode, [d[mode] for d in kept]) for mode in MODES},
    }


# --------------------------------------------------------------------------- the machine


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            stdin=subprocess.DEVNULL,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def machine_facts() -> dict[str, Any]:
    facts: dict[str, Any] = {
        "node": platform.node(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "ram_mb": _total_ram_mb(),
    }
    sha = _git("rev-parse", "HEAD")
    facts["git_sha"] = sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None
    facts["git_dirty"] = bool(_git("status", "--porcelain"))
    return facts


def _total_ram_mb() -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullTotalPhys // (1024 * 1024))


def peak_rss_mb() -> float | None:
    """This process's peak working set (Windows, ctypes; psutil is not a dependency)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
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
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return None
    return round(counters.PeakWorkingSetSize / (1024 * 1024), 1)


# --------------------------------------------------------------------------- the database


def _safe_address(url: Any) -> str:
    """host:port/database - never the user or the password."""
    return f"{url.host}:{url.port}/{url.database}"


@contextlib.contextmanager
def scratch_database(base_url: str, *, keep: bool) -> Iterator[tuple[Any, dict[str, Any]]]:
    """A new ``pagentos_bench_lexical_<8hex>`` on the server of ``base_url``, at head."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    url = make_url(base_url)
    name = SCRATCH_PREFIX + uuid.uuid4().hex[:8]
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    facts: dict[str, Any] = {"database": name, "server": f"{url.host}:{url.port}"}
    try:
        with admin.connect() as conn:
            facts["leftovers"] = [
                row[0]
                for row in conn.execute(
                    text("SELECT datname FROM pg_database WHERE datname LIKE :p"),
                    {"p": SCRATCH_PREFIX + "%"},
                )
            ]
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        scratch = url.set(database=name)
        try:
            _migrate(scratch)
            yield scratch, facts
        finally:
            if keep:
                facts["kept"] = True
            else:
                with admin.connect() as conn:
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                facts["dropped"] = True
    finally:
        admin.dispose()


def _migrate(url: Any) -> None:
    """``alembic upgrade head`` on ``url``; alembic's env.py reads the address from Settings."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from app.config import get_settings

    api_root = ROOT / "services" / "api"
    before = os.environ.get("PAGENTOS_DATABASE_URL")
    os.environ["PAGENTOS_DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        cfg = AlembicConfig(str(api_root / "alembic.ini"))
        cfg.set_main_option("script_location", str(api_root / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        if before is None:
            os.environ.pop("PAGENTOS_DATABASE_URL", None)
        else:
            os.environ["PAGENTOS_DATABASE_URL"] = before
        get_settings.cache_clear()


def server_facts(session: Any) -> dict[str, Any]:
    from sqlalchemy import text

    def one(sql: str) -> Any:
        return session.execute(text(sql)).scalar()

    return {
        "postgres_version": one("SELECT version()"),
        "pg_trgm_version": one("SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm'"),
        "vector_version": one("SELECT extversion FROM pg_extension WHERE extname = 'vector'"),
        "database_bytes": one("SELECT pg_database_size(current_database())"),
        "memories_table_bytes": one("SELECT pg_total_relation_size('memories')"),
        "alembic_revision": one("SELECT version_num FROM alembic_version"),
    }


# --------------------------------------------------------------------------- embedders


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "PagentOS" / "fastembed-cache"


def cache_refusal(cache_dir: Path) -> str | None:
    resolved = cache_dir.resolve()
    for forbidden, why in (
        (ROOT.resolve(), "the repository"),
        (Path(tempfile.gettempdir()).resolve(), "%TEMP%"),
    ):
        if resolved == forbidden or forbidden in resolved.parents:
            return f"--cache-dir is under {why}; use a folder outside it"
    return None


def build(name: str, cache_dir: Path) -> tuple[Embedder | None, dict[str, Any]]:
    if name == "deterministic":
        embedder = DeterministicEmbedder()
        return embedder, {"name": name, "model_id": embedder.model_id, "fallback": None}
    from app.memory.providers import DEFAULT_LOCAL_MODEL, EmbeddingProviderError, LocalEmbedder

    started = time.perf_counter()
    try:
        local = LocalEmbedder(model_name=DEFAULT_LOCAL_MODEL, cache_dir=str(cache_dir))
    except EmbeddingProviderError as exc:
        return None, {"name": name, "model_id": None, "fallback": str(exc)[:300]}
    return local, {
        "name": name,
        "model_name": DEFAULT_LOCAL_MODEL,
        "model_id": local.model_id,
        "load_s": round(time.perf_counter() - started, 2),
        "fallback": None,
    }


# --------------------------------------------------------------------------- the run


def run_bench(args: argparse.Namespace) -> dict[str, Any]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import Settings

    data = load_cases(args.set)
    errors = validate_cases(data)
    if errors:
        raise BenchError("the set is not fit: " + "; ".join(errors[:5]))
    report: dict[str, Any] = {
        "kind": "memory-lexical-turkish-measure",
        "status": "MEASURED_LOCAL",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": machine_facts(),
        "set": {
            "path": Path(args.set).resolve().relative_to(ROOT).as_posix()
            if ROOT in Path(args.set).resolve().parents
            else str(args.set),
            "questions": len(data["cases"]),
            "memories_per_corpus": sum(1 + len(c["distractors"]) for c in data["cases"])
            + len(data.get("noise", [])),
            "noise": len(data.get("noise", [])),
            "tags": {
                tag: sum(1 for c in data["cases"] if tag in c["tags"]) for tag in sorted(KNOWN_TAGS)
            },
        },
        "seeds": args.seeds,
        "draws": args.draws,
        "rounds": args.repeat,
        "warmup": args.warmup,
        "p95_budget_ms": P95_BUDGET_MS,
        "embedders": {},
        "results": {},
    }
    base_url = Settings().database_url
    with scratch_database(base_url, keep=args.keep_db) as (scratch, db_facts):
        report["database"] = db_facts
        print(f"geçici veritabanı: {_safe_address(scratch)}")
        engine = create_engine(scratch)
        session = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            for name in args.embedder:
                embedder, info = build(name, args.cache_dir)
                report["embedders"][name] = info
                if embedder is None:
                    print(f"gömücü {name}: yüklenemedi ({info['fallback']})")
                    continue
                per_seed: dict[str, Any] = {}
                for seed in args.seeds:
                    out = measure_draws(
                        session,
                        embedder,
                        data,
                        seed=seed,
                        draws=args.draws,
                        rounds=args.repeat,
                        warmup=args.warmup,
                    )
                    per_seed[str(seed)] = out
                    print(
                        f"{name} tohum {seed}, {args.draws} yükleme: "
                        + " | ".join(
                            f"{m} ilk-1 {out[m]['top1']:.3f} ilk-3 {out[m]['top3']:.3f} "
                            f"({out[m]['top3_min']:.3f}-{out[m]['top3_max']:.3f}) "
                            f"p95 {out[m]['p95_ms']:.1f} ms"
                            for m in MODES
                        )
                    )
                report["results"][name] = per_seed
            report["database"].update(server_facts(session))
        finally:
            session.close()
            engine.dispose()
    report["peak_rss_mb"] = peak_rss_mb()
    report["decision"] = decide(
        report, dict(args.accept_lost), accepted_classes=dict(args.accept_lost_class)
    )
    report["seed_sensitivity"] = seed_sensitivity(report)
    return report


def classify_drops(seed_draws: dict[str, Sequence[dict[str, Any]]]) -> dict[str, str]:
    """``ID_TIE`` for a question that, in every seed where it dropped, was in trgm's top 3
    in another load of that seed; ``CONSISTENT`` otherwise. Questions never dropped are not
    in the result."""
    classes: dict[str, str] = {}
    for draws in seed_draws.values():
        for cid, drops in _drops(
            pool_draws("like", [d["like"] for d in draws]),
            pool_draws("trgm", [d["trgm"] for d in draws]),
        ).items():
            if not any(drops):
                continue
            moved = any(_in_top3(d["trgm"]["ranks"].get(cid)) for d in draws)
            if not moved or classes.get(cid) == CONSISTENT:
                classes[cid] = CONSISTENT
            else:
                classes[cid] = ID_TIE
    return classes


def decide(
    report: dict[str, Any],
    accepted_lost: dict[str, str],
    *,
    accepted_classes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The decision from the local embedder, every seed and every draw pooled; the
    deterministic embedder never decides. Each draw's own verdict is counted too
    (``per_draw``): more than one verdict means one draw alone could not have decided."""
    local = report["results"].get("local")
    if not local:
        fallback = report["embedders"].get("local", {}).get("fallback", "not measured")
        return {
            "default": "like",
            "outcome": "no_local",
            "verdict": "like",
            "rule": RULE_TEXT,
            "reasons": [
                "Yerel gömücü ölçülemedi (" + str(fallback) + "): like kalır, ölçüm kesin değil."
            ],
            "embedder_fallback": fallback,
        }
    seeds = [str(s) for s in report["seeds"] if str(s) in local]
    draws = [d for s in seeds for d in local[s]["draws"]]
    classes = classify_drops({s: local[s]["draws"] for s in seeds})
    accept = {
        "accepted_lost": accepted_lost,
        "accepted_classes": accepted_classes,
        "classes": classes,
    }
    verdicts: Counter[str] = Counter()
    for d in draws:
        one = decide_default(
            pool_draws("like", [d["like"]]), pool_draws("trgm", [d["trgm"]]), **accept
        )
        verdicts[one["outcome"]] += 1
    out = decide_default(
        pool_draws("like", [d["like"] for d in draws]),
        pool_draws("trgm", [d["trgm"] for d in draws]),
        **accept,
    )
    out["read_from"] = f"local, tohumlar {', '.join(seeds)}, toplam {len(draws)} yükleme"
    out["draws"] = len(draws)
    out["per_draw"] = dict(sorted(verdicts.items()))
    out["draw_sensitive"] = len(verdicts) > 1
    return out


def seed_sensitivity(report: dict[str, Any]) -> dict[str, Any]:
    """Top-3 hit counts per seed and draw; more than one question apart anywhere is 'order
    sensitive' (the load order or the ids' tie draw moves the result)."""
    out: dict[str, Any] = {}
    for name, per_seed in report["results"].items():
        for mode in MODES:
            hits = {
                seed: [
                    sum(1 for r in d[mode]["ranks"].values() if _in_top3(r)) for d in res["draws"]
                ]
                for seed, res in per_seed.items()
            }
            flat = [h for values in hits.values() for h in values]
            spread = max(flat) - min(flat) if flat else 0
            out[f"{name}/{mode}"] = {"top3_hits": hits, "order_sensitive": spread > 1}
    return out


# --------------------------------------------------------------------------- the report


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_md(report: dict[str, Any]) -> str:
    machine = report["machine"]
    db = report.get("database", {})
    decision = report["decision"]
    lines = [
        "# Hafıza kelime araması ölçümü: like ile trgm (memory-lexical-turkish-measure)",
        "",
        f"Durum: **{report['status']}** — gerçek PostgreSQL'de, geçici veritabanında ölçüldü.",
        "",
        f"- Makine: `{machine.get('node')}`, {machine.get('platform')}, "
        f"{machine.get('cpu_count')} çekirdek, {machine.get('ram_mb')} MB RAM",
        f"- Git: `{machine.get('git_sha')}`"
        + (" (kirli ağaç)" if machine.get("git_dirty") else ""),
        f"- PostgreSQL: {db.get('postgres_version')}; pg_trgm {db.get('pg_trgm_version')}, "
        f"vector {db.get('vector_version')}, göç `{db.get('alembic_revision')}`",
        f"- Geçici DB `{db.get('database')}`: {db.get('database_bytes')} bayt "
        f"(memories {db.get('memories_table_bytes')} bayt), sonunda "
        + ("silindi" if db.get("dropped") else "SAKLANDI"),
        f"- Set: {report['set']['questions']} soru, korpus başına "
        f"{report['set']['memories_per_corpus']} anı ({report['set']['noise']} gürültü); etiketler "
        + ", ".join(f"{k} {v}" for k, v in report["set"]["tags"].items()),
        f"- Tohumlar {report['seeds']}, tohum başına {report['draws']} yükleme (her biri yeni "
        f"kimliklerle), {report['warmup']} ısınma + {report['rounds']} tur, "
        "modlar her turda sıra değiştirerek; yeniden sıralayıcı kapalı (üretim gibi)",
        f"- Süreç bellek tepesi: {report.get('peak_rss_mb')} MB",
        "",
        "| gömücü | tohum | mod | ilk-1 | ilk-3 | ilk-10 | MRR | p50 ms | p95 ms | max ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, per_seed in report["results"].items():
        for seed, res in per_seed.items():
            for mode in MODES:
                r = res[mode]
                lines.append(
                    f"| {name} | {seed} | {mode} | {_pct(r['top1'])} | {_pct(r['top3'])} | "
                    f"{_pct(r['top10'])} | {r['mrr']:.3f} | {r['p50_ms']:.1f} | "
                    f"{r['p95_ms']:.1f} | {r['max_ms']:.1f} |"
                )
    for name, info in report["embedders"].items():
        if info.get("fallback"):
            lines.append(f"\nGömücü `{name}` yüklenemedi: {info['fallback']}")
    lines += [
        "",
        "Oranlar her tohumun yüklemelerinin ortalaması; ilk-3 aralığı (en az-en çok yükleme):",
        "",
    ]
    for name, per_seed in report["results"].items():
        for seed, res in per_seed.items():
            lines.append(
                f"- {name} tohum {seed}: "
                + ", ".join(
                    f"{m} {_pct(res[m]['top3_min'])}-{_pct(res[m]['top3_max'])}" for m in MODES
                )
            )
    lines += [
        "",
        "## Soru başına sıra (doğru anının yeri her yüklemede; - = ilk 10'da yok)",
        "",
    ]

    def ranks(values: Sequence[int | None]) -> str:
        return " ".join(str(v) if v is not None else "-" for v in values)

    for name, per_seed in report["results"].items():
        for seed, res in per_seed.items():
            lines += [
                f"### {name}, tohum {seed}",
                "",
                "| soru | like | trgm | trgm ilk-3 payı |",
                "|---|---|---|---|",
            ]
            for cid, like_ranks in res["like"]["ranks_per_draw"].items():
                trgm_ranks = res["trgm"]["ranks_per_draw"].get(cid, [])
                share = res["trgm"]["top3_share"].get(cid, 0.0)
                lines.append(
                    f"| {cid} | {ranks(like_ranks)} | {ranks(trgm_ranks)} | {_pct(share)} |"
                )
            lines.append("")
    lines += ["## Tohuma ve yüklemeye duyarlılık (ilk-3 isabet, yükleme başına)", ""]
    for key, value in report["seed_sensitivity"].items():
        note = " — sıraya/kimlik çekilişine duyarlı" if value["order_sensitive"] else ""
        lines.append(f"- {key}: {value['top3_hits']}{note}")
    lines += [
        "",
        "## Karar",
        "",
        f"**Varsayılan: `{decision['default']}`** — {decision.get('verdict', '')} "
        f"({decision.get('outcome')})",
        "",
        f"Kural: {decision['rule']}",
        "",
    ]
    lines += [f"- {reason}" for reason in decision["reasons"]]
    if decision.get("per_draw") is not None:
        lines.append(f"- Okunan: {decision['read_from']}")
        lines.append(
            f"- Tek tek yüklemelerin kararı: {decision['per_draw']}"
            + (" — tek yükleme kararı veremezdi" if decision.get("draw_sensitive") else "")
        )
    if decision.get("lost_in_trgm") is not None:
        lost = decision["lost_in_trgm"]
        lines.append(
            "- trgm'de ilk-3'ten düşen (herhangi bir yüklemede): " + ("yok" if not lost else "")
        )
        for entry in lost:
            why = decision.get("accepted_lost", {}).get(entry["id"], "KABUL GEREKÇESİ YOK")
            lines.append(
                f"  - {entry['id']}: {entry['lost_draws']}/{entry['draws']} yüklemede "
                f"({_pct(entry['rate'])}), sınıf {entry['class']} — {why}"
            )
        lines.append(f"- trgm'de ilk-3'e giren: {decision.get('gained_in_trgm') or 'yok'}")
        lines.append(f"- p95 farkı: {decision['p95_delta_ms']:+.1f} ms (sınır 50 ms)")
        if decision.get("borderline"):
            lines.append("- p95 farkı 40-60 ms bandında: SINIRDA")
    lines.append("")
    return "\n".join(lines)


def _accept(value: str) -> tuple[str, str]:
    cid, sep, why = value.partition(":")
    if not sep or not cid.strip() or not why.strip():
        raise argparse.ArgumentTypeError(
            "--accept-lost id:reason / --accept-lost-class class:reason"
        )
    return cid.strip(), why.strip()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument(
        "--embedder",
        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        default=["deterministic", "local"],
    )
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument(
        "--seeds", type=lambda s: [int(x) for x in s.split(",") if x.strip()], default=[7, 11]
    )
    parser.add_argument("--draws", type=int, default=5, help="fresh loads (fresh ids) per seed")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--keep-db", action="store_true")
    parser.add_argument("--accept-lost", type=_accept, action="append", default=[])
    parser.add_argument(
        "--accept-lost-class",
        type=_accept,
        action="append",
        default=[],
        help=f"class:reason, e.g. {ID_TIE}:... accepts every drop of that class",
    )
    parser.add_argument("--out", type=Path, default=None, help="write the report as JSON (+ .md)")
    args = parser.parse_args(argv[1:])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import logging

    from app.logging import configure_logging

    # ~1300 memory_audit lines per run say nothing the report does not.
    configure_logging(json_output=False, level=logging.WARNING)
    args.cache_dir = args.cache_dir or default_cache_dir()
    refusal = cache_refusal(args.cache_dir)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    unknown = set(args.embedder) - {"deterministic", "local"}
    if unknown:
        print(f"unknown --embedder {sorted(unknown)}", file=sys.stderr)
        return 2
    report = run_bench(args)
    print(f"Karar: {report['decision']['default']} — {report['decision']['reasons'][0]}")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(
            (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        args.out.with_suffix(".md").write_bytes(render_md(report).encode("utf-8"))
        print(f"kanıt: {args.out.as_posix()} (+ .md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
