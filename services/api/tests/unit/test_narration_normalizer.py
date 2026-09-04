"""Unit tests: the deterministic tr-TR normalizer, driven by the machine-readable
eval dataset (evals/voice/turkish_narration.jsonl) as the ground truth.

This is the acceptance evidence for ACCEPTANCE_TESTS.md M4:
"Turkish normalizer tests cover date, money, percentage, IP, abbreviation, table".
Every §4 category is asserted to have at least one passing case.
"""

import json
from pathlib import Path

import pytest

from app.narration.normalizer import normalize
from app.narration.tables import narrate_table

EVAL_PATH = Path(__file__).resolve().parents[4] / "evals" / "voice" / "turkish_narration.jsonl"

# Categories the acceptance criteria explicitly require, plus the rest of §4.
REQUIRED_CATEGORIES = {
    "date",
    "clock",
    "decimal",
    "percentage",
    "lira",
    "currency",
    "thousands",
    "ordinal",
    "phone",
    "ip",
    "cidr",
    "version",
    "email",
    "url",
    "path",
    "abbreviation",
    "mixed_tr_en",
    "table",
}


def _load_cases() -> list[dict]:
    assert EVAL_PATH.exists(), f"missing eval dataset: {EVAL_PATH}"
    cases = []
    for line in EVAL_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


CASES = _load_cases()
TEXT_CASES = [c for c in CASES if c["category"] != "table"]
TABLE_CASES = [c for c in CASES if c["category"] == "table"]


def test_dataset_is_large_and_covers_all_categories() -> None:
    assert len(CASES) >= 60, f"dataset too small: {len(CASES)}"
    present = {c["category"] for c in CASES}
    missing = REQUIRED_CATEGORIES - present
    assert not missing, f"eval dataset missing required categories: {missing}"


@pytest.mark.parametrize("case", TEXT_CASES, ids=[c["id"] for c in TEXT_CASES])
def test_normalizer_matches_expected(case: dict) -> None:
    got = normalize(
        case["input"],
        mode=case.get("mode", "narration"),
        pronunciation=case.get("pronunciation"),
    )
    assert got == case["expected"], f"{case['id']}: {case['input']!r}"


@pytest.mark.parametrize("case", TABLE_CASES, ids=[c["id"] for c in TABLE_CASES])
def test_table_semantic_narration(case: dict) -> None:
    out = narrate_table(case["input"], mode=case.get("mode", "narration")).text
    for needle in case.get("expected_contains", []):
        assert needle in out, f"{case['id']}: expected {needle!r} in {out!r}"
    for absent in case.get("expected_absent", []):
        assert absent not in out, f"{case['id']}: {absent!r} should be absent in {out!r}"


def test_every_required_category_has_a_passing_case() -> None:
    """Belt-and-braces: assert each required category has >=1 case that the
    normalizer/table narrator actually resolves as expected."""
    by_cat: dict[str, list[dict]] = {}
    for c in CASES:
        by_cat.setdefault(c["category"], []).append(c)
    for cat in REQUIRED_CATEGORIES:
        cases = by_cat.get(cat, [])
        assert cases, f"no cases for required category {cat}"
        ok = 0
        for c in cases:
            if cat == "table":
                out = narrate_table(c["input"], mode=c.get("mode", "narration")).text
                if all(n in out for n in c.get("expected_contains", [])):
                    ok += 1
            else:
                got = normalize(
                    c["input"],
                    mode=c.get("mode", "narration"),
                    pronunciation=c.get("pronunciation"),
                )
                if got == c["expected"]:
                    ok += 1
        assert ok >= 1, f"category {cat} has no passing case"


# ---- targeted spot checks mirroring the VOICE_SPEC §4 canonical examples ----


def test_spec_examples() -> None:
    assert normalize("31.08.2026") == "otuz bir Ağustos iki bin yirmi altı"
    assert normalize("%17,2") == "yüzde on yedi virgül iki"
    assert normalize("₺1.250.000") == "bir milyon iki yüz elli bin Türk lirası"
    assert (
        normalize("192.168.1.20", mode="technical")
        == "yüz doksan iki nokta yüz altmış sekiz nokta bir nokta yirmi"
    )


def test_pronunciation_dict_wins_over_default() -> None:
    out = normalize("API çağrısı", pronunciation={"API": "ey pi ay"})
    assert out == "ey pi ay çağrısı"


def test_technical_mode_keeps_dotted_octets_not_magnitude() -> None:
    # In technical mode a 4-group dotted run must be read as octets, never as a
    # collapsed magnitude.
    out = normalize("192.168.100.200", mode="technical")
    assert "nokta" in out and "milyon" not in out


def test_owner_pronunciation_wins_over_number_pipeline() -> None:
    """Owner authority: an explicit spoken form must survive even when the
    token contains digits or punctuation the numeric pipeline would rewrite.

    Regression: the dictionary used to be applied AFTER the pipeline, so any
    owner token with digits was mangled before it could ever match.
    """
    pron = {
        "CUDA12": "kuda on iki",
        "SQL2019": "es kü el iki bin on dokuz",
        "192.168.1.1": "yerel ağ geçidi",
    }
    assert "kuda on iki" in normalize("CUDA12 sürümünü doğrula.", pronunciation=pron)
    assert "es kü el iki bin on dokuz" in normalize("SQL2019 kuruldu.", pronunciation=pron)
    assert "yerel ağ geçidi" in normalize(
        "Sunucu 192.168.1.1 adresinde.", pronunciation=pron, mode="technical"
    )


def test_pronunciation_does_not_break_untouched_numerics() -> None:
    """The reordering must not regress ordinary numeric normalization."""
    out = normalize("Toplam 1.250.000 lira ve %17,2 artış.", pronunciation={"API": "ey pi ay"})
    assert "bir milyon iki yüz elli bin" in out
    assert "yüzde on yedi virgül iki" in out


def test_iso_datetime_stamps_with_t_zone_and_fraction_are_spoken_not_crashed() -> None:
    """Found on the real dev database (M16): an ISO stamp inside a briefing
    ("2026-09-04T18:28:21Z") reached the normaliser and "T18" was handed to int()."""
    assert normalize("2026-09-04T18:28:21Z") == (
        "dört Eylül iki bin yirmi altı saat on sekiz yirmi sekiz yirmi bir"
    )
    assert normalize("2026-09-04T18:28:21.125418Z").startswith("dört Eylül iki bin yirmi altı saat")
    assert normalize("2026-09-04T18:28:21+03:00").endswith("saat on sekiz yirmi sekiz yirmi bir")
    assert normalize("2026-09-04 18:05").endswith("saat on sekiz sıfır beş")
