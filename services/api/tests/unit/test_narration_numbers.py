"""Unit tests: Turkish cardinal/ordinal number-to-words engine edge cases."""

import pytest

from app.narration import numbers


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, "sıfır"),
        (1, "bir"),
        (9, "dokuz"),
        (10, "on"),
        (11, "on bir"),
        (20, "yirmi"),
        (26, "yirmi altı"),
        (99, "doksan dokuz"),
        (100, "yüz"),  # "yüz", never "bir yüz"
        (101, "yüz bir"),
        (200, "iki yüz"),
        (250, "iki yüz elli"),
        (1000, "bin"),  # "bin", never "bir bin"
        (2000, "iki bin"),
        (2026, "iki bin yirmi altı"),
        (12345, "on iki bin üç yüz kırk beş"),
        (100000, "yüz bin"),
        (1000000, "bir milyon"),  # milyon KEEPS the "bir"
        (1250000, "bir milyon iki yüz elli bin"),
        (1000000000, "bir milyar"),
        (-5, "eksi beş"),
    ],
)
def test_cardinal(n: int, expected: str) -> None:
    assert numbers.cardinal(n) == expected


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (1, "birinci"),
        (2, "ikinci"),
        (3, "üçüncü"),
        (4, "dördüncü"),  # consonant softening t -> d
        (5, "beşinci"),
        (6, "altıncı"),
        (10, "onuncu"),
        (20, "yirminci"),
        (21, "yirmi birinci"),
        (100, "yüzüncü"),
        (1000, "bininci"),
    ],
)
def test_ordinal(n: int, expected: str) -> None:
    assert numbers.ordinal(n) == expected


@pytest.mark.parametrize(
    ("int_part", "frac_part", "expected"),
    [
        ("17", "2", "on yedi virgül iki"),
        ("3", "42", "üç virgül kırk iki"),
        ("0", "5", "sıfır virgül beş"),
        ("3", "05", "üç virgül sıfır beş"),  # leading-zero fraction read digit-wise
        ("49", "95", "kırk dokuz virgül doksan beş"),
    ],
)
def test_decimal(int_part: str, frac_part: str, expected: str) -> None:
    assert numbers.decimal(int_part, frac_part) == expected


def test_digit_by_digit() -> None:
    assert numbers.digit_by_digit("05") == "sıfır beş"
    assert numbers.digit_by_digit("2024") == "iki sıfır iki dört"
