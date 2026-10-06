"""scripts/core/bench-memory-embedding.py: the Granite measurement's report and its verdict.

The script is loaded by FILE PATH (never by the working directory: pytest across drives needs
its rootdir). Every model is a fake; nothing is downloaded. What is pinned:

* ``--out`` writes every pair's cosine per model (14 rows, the two hard pairs by sentence),
  and ``--threads`` reaches the model factory;
* ``render_tr`` turns the numbers into ONE verdict of three - (a) wider and inside the CPX32
  budget, (b) wider but over it, (c) narrower - each saying that this card adopts nothing;
* a refused model is a row with its reason, never zeros; the markdown names no cache path
  under the repository and holds no vector.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "core" / "bench-memory-embedding.py"
POTION = "minishlab/potion-multilingual-128M"
GRANITE = "ibm-granite/granite-embedding-311m-multilingual-r2"
GRANITE_INT8 = "ibm-granite/granite-embedding-311m-multilingual-r2-int8"
HARD = (
    ("Ekranlar 15 dakika sonra kapansın", "Monitörler çeyrek saat boşta kalınca sönsün"),
    ("Sahip sabahları kahve içmeyi sever", "Kadir her sabah bir fincan kahve içer"),
)


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("bench_memory_embedding", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bench() -> Any:
    return _load()


def _outside_cache() -> str:
    """A cache path outside the repository and %TEMP% that the fakes never create."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return str(Path(base) / "PagentOS" / "bench-test-never-created")


class _Fake:
    def __init__(self, width: int) -> None:
        self.width = width

    def embed(self, documents, **_kwargs):
        for text in documents:
            seed = sum(ord(ch) for ch in text) or 1
            yield [float((seed * (i + 1)) % 11 + 1) for i in range(self.width)]


def test_out_writes_14_pair_rows_per_model_and_threads_reach_the_factory(
    bench, tmp_path, monkeypatch, capsys
):
    calls: list[tuple[str, str | None, int | None]] = []

    def fake_factory(model_name: str, cache_dir: str | None, *, threads: int | None = None):
        calls.append((model_name, cache_dir, threads))
        return _Fake(768 if model_name == GRANITE else 256)

    monkeypatch.setattr(bench, "_fastembed_factory", fake_factory)
    out = tmp_path / "bench.json"
    code = bench.main(
        ["bench", GRANITE, "--out", str(out), "--threads", "4", "--cache-dir", _outside_cache()]
    )
    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    names = [m["model_name"] for m in report["models"]]
    assert names == [POTION, GRANITE]  # potion is always measured, first, as the baseline
    assert report["threads"] == 4
    assert report["shape"] == "cpx32-bicimi"
    for row in report["models"]:
        assert row["refused"] is None
        assert len(row["pairs"]) == 14
        sentences = {(p["a"], p["b"]) for p in row["pairs"]}
        for pair in HARD:
            assert pair in sentences
        assert {"ayrim", "same_mean", "other_mean", "embed_median_ms", "load_s_first"} <= set(row)
    granite = report["models"][1]
    assert granite["model_id"] == f"local-{GRANITE}@256"
    assert granite["native_dim"] == 768 and granite["dim"] == 256 and granite["truncated"]
    assert {c[2] for c in calls} == {4}
    assert {c[1] for c in calls} == {_outside_cache()}
    md = out.with_suffix(".md").read_text(encoding="utf-8")
    assert "Kadir her sabah bir fincan kahve içer" in md
    assert str(REPO) not in md and REPO.as_posix() not in md
    assert _outside_cache() not in md  # the cache is named by its kind, not its path


def test_a_cache_inside_the_repository_is_refused(bench, tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "_fastembed_factory", lambda *a, **k: pytest.fail("loaded"))
    code = bench.main(["bench", "--cache-dir", str(REPO / "var" / "cache")])
    assert code == 2


# ------------------------------------------------------------------ render_tr / verdict


def _pairs(hard_scores: tuple[float, float]) -> list[dict[str, Any]]:
    rows = [
        {"a": HARD[0][0], "b": HARD[0][1], "label": "ANLAMDAŞ", "cosine": hard_scores[0]},
        {"a": HARD[1][0], "b": HARD[1][1], "label": "ANLAMDAŞ", "cosine": hard_scores[1]},
    ]
    return rows


def _model(name: str, ayrim: float, *, hard=(0.1, 0.2), median_ms=1.0) -> dict[str, Any]:
    return {
        "model_name": name,
        "model_id": f"local-{name}",
        "refused": None,
        "native_dim": 256,
        "dim": 256,
        "truncated": False,
        "load_path": "fastembed",
        "load_s_first": 3.0,
        "load_s_warm": 1.0,
        "pairs": _pairs(hard),
        "ayrim": ayrim,
        "same_mean": 0.5,
        "other_mean": 0.5 - ayrim,
        "embed_median_ms": median_ms,
        "pinned_files": [],
    }


def _cost(name: str, retention_s: float, shape: str = "cpx32-bicimi") -> dict[str, Any]:
    return {
        "model_name": name,
        "shape": shape,
        "threads": 4 if shape == "cpx32-bicimi" else None,
        "warm_load_s": 2.0,
        "embed_median_ms": retention_s * 5,
        "retention_batch_rows": 200,
        "retention_batch_s": retention_s,
        "rss_peak_mb": 1500.0,
    }


def _report(candidate_ayrim: float, candidate_retention_s: float) -> dict[str, Any]:
    return {
        "shape": "ev-pc",
        "threads": None,
        "cache": "%LOCALAPPDATA%/PagentOS/fastembed-cache",
        "baseline_model": POTION,
        "retention_batch_rows": 200,
        "models": [_model(POTION, 0.457), _model(GRANITE, candidate_ayrim, hard=(0.71, 0.63))],
        "cost": [_cost(POTION, 0.04), _cost(GRANITE, candidate_retention_s)],
    }


def _assert_no_adoption(text: str) -> None:
    assert "bu kart benimsemez" in text.lower()
    for claim in ("daha iyi", "daha kaliteli", "üstün"):
        assert claim not in text.lower()


@pytest.mark.parametrize(
    ("ayrim", "retention_s", "letter", "marker"),
    [
        (0.61, 5.0, "a", "benimseme ÖNERİSİ ayrı fikir olarak gelir"),
        (0.61, 400.0, "b", "sahibin PC'sine taşınınca"),
        (0.30, 5.0, "c", "ölçüldü, alınmadı"),
    ],
)
def test_render_tr_gives_the_verdict_the_numbers_give(bench, ayrim, retention_s, letter, marker):
    report = _report(ayrim, retention_s)
    verdict = bench.verdict_tr(report)
    assert verdict["letter"] == letter
    assert marker in verdict["text"]
    _assert_no_adoption(verdict["text"])
    md = bench.render_tr(report)
    assert f"**({letter})**" in md
    assert marker in md
    _assert_no_adoption(md)
    # The ADR-0200 table's shape, and the two hard pairs by name per model.
    assert "| model | AYRIM | anlamdaş ort. | alakasız ort. | yükleme | embed ortanca | RSS |" in md
    assert "0.710" in md and "0.630" in md
    assert "+0.457" in md and "+0.354" in md and "2026-09-27" in md  # the earlier run, dated


def test_a_refused_model_renders_with_its_reason_never_zeros(bench):
    report = _report(0.61, 5.0)
    refused = {
        "model_name": GRANITE_INT8,
        "refused": "local embedding model: tokenizer.json sha256 does not match the pin",
    }
    report["models"].append(refused)
    md = bench.render_tr(report)
    row = next(line for line in md.splitlines() if line.startswith(f"| `{GRANITE_INT8}`"))
    assert "REDDEDİLDİ" in row and "tokenizer.json sha256" in row
    assert "0.000" not in row and "| |" not in row
    # A refused candidate is not a verdict input; the loaded one still decides.
    assert bench.verdict_tr(report)["letter"] == "a"


@pytest.mark.parametrize(
    ("int8_shift", "chosen", "diff"),
    [
        (0.005, GRANITE_INT8, "0.0050"),  # every pair within 0.01 of FP32: INT8
        (0.0152, GRANITE, "0.0152"),  # one pair 0.0152 away (the real run's gap): FP32
    ],
)
def test_render_tr_names_the_granite_name_for_the_corpus_and_why(bench, int8_shift, chosen, diff):
    report = _report(0.30, 5.0)
    int8 = _model(GRANITE_INT8, 0.29, hard=(0.71, 0.63 + int8_shift))
    report["models"].append(int8)
    choice = bench.corpus_choice_tr(report)
    assert choice["chosen"] == chosen
    assert choice["threshold"] == 0.01
    assert f"{choice['max_diff']:.4f}" == diff
    md = bench.render_tr(report)
    section = md.split("## Korpus için seçilen Granite adı", 1)[1].split("\n## ", 1)[0]
    assert f"`{chosen}`" in section
    assert diff in section and "0.01" in section
    assert HARD[1][0] in section  # the pair that drove the gap, by its sentence


def test_corpus_choice_with_int8_refused_is_fp32_and_says_so(bench):
    report = _report(0.30, 5.0)
    report["models"].append({"model_name": GRANITE_INT8, "refused": "allowlist"})
    choice = bench.corpus_choice_tr(report)
    assert choice["chosen"] == GRANITE and choice["max_diff"] is None
    assert "REDDEDİLDİ" in choice["text"]


def test_the_markdown_holds_no_vector(bench):
    report = _report(0.61, 5.0)
    report["models"][1]["vector_probe"] = [0.123456789] * 8  # a stray field must not render
    md = bench.render_tr(report)
    assert "0.123456789" not in md
    assert "[" not in md.replace("[kırpıldı", "")
