"""Bozuk Türkçe kapıda: the rule that recognises UTF-8 read as cp1252 ('YÃ¶neticisi')."""

from __future__ import annotations

from app.team.mojibake import (
    MOJIBAKE_PAIRS,
    MojibakeHit,
    find_mojibake,
    mojibake_detail,
    scan_payload,
)

# The correct side, written here and NOT read from the module: each pair is generated from it.
TURKISH_AND_PUNCTUATION = "öüçÖÜÇıİğĞşŞ—–“’"


def _as_cp1252(letter: str) -> str:
    """What a BOM-less PowerShell 5.1 script makes of ``letter``: its UTF-8 bytes read one by one
    as cp1252, falling back to latin-1 for the five bytes cp1252 leaves undefined."""
    out = []
    for byte in letter.encode("utf-8"):
        try:
            out.append(bytes([byte]).decode("cp1252"))
        except UnicodeDecodeError:
            out.append(bytes([byte]).decode("latin-1"))
    return "".join(out)


def test_case1_proje_yoneticisi_is_hit_at_offset_7() -> None:
    hit = find_mojibake("Proje YÃ¶neticisi")
    assert hit == MojibakeHit(pair="Ã¶", correct="ö", offset=7)


def test_case2_correct_turkish_is_clean() -> None:
    assert find_mojibake("Proje Yöneticisi") is None
    assert find_mojibake("Işık, çiçek, ağaç, şüphe, İzmir, Ğ, Ö, Ü, Ç — tamam") is None


def test_case3_lone_lead_byte_letters_are_never_hits() -> None:
    assert find_mojibake("São Paulo") is None
    assert find_mojibake("ÃO") is None
    assert find_mojibake("Ãlvaro") is None
    assert find_mojibake("Ä Å Ã â") is None
    assert find_mojibake("Ã") is None


def test_case4_every_pair_is_generated_from_its_letter_and_found() -> None:
    for letter in TURKISH_AND_PUNCTUATION:
        pair = _as_cp1252(letter)
        assert MOJIBAKE_PAIRS.get(pair) == letter, (letter, pair)
        hit = find_mojibake(f"xx {pair} yy")
        assert hit == MojibakeHit(pair=pair, correct=letter, offset=3), (letter, pair)
    # and nothing in the module disagrees with the generator
    for pair, letter in MOJIBAKE_PAIRS.items():
        assert _as_cp1252(letter) == pair, (pair, letter)


def test_case5_earliest_of_two_hits_is_returned() -> None:
    hit = find_mojibake("aÄ±b ÅŸimdi Ã¶")
    assert hit == MojibakeHit(pair="Ä±", correct="ı", offset=1)
    hit = find_mojibake("önce doğru, sonra Ã§ ve Ä±")
    assert hit is not None and hit.pair == "Ã§" and hit.offset == 18


def test_case6_em_dash() -> None:
    hit = find_mojibake("bitti â€” devam")
    assert hit == MojibakeHit(pair="â€”", correct="—", offset=6)


def test_case7_scan_payload_paths() -> None:
    found = scan_payload({"title": "ok", "sections": [{"text": "ÅŸimdi"}]})
    assert found == ("sections.0.text", MojibakeHit(pair="ÅŸ", correct="ş", offset=0))


def test_case7_scan_payload_finds_mojibake_in_a_dict_key() -> None:
    found = scan_payload({"title": "ok", "meta": {"ok": 1, "YÃ¶n": "temiz"}})
    assert found is not None
    path, hit = found
    assert hit.pair == "Ã¶"
    assert path.startswith("meta.")
    assert "Y" not in path.removeprefix("meta.")  # the key's text is not echoed in the path


def test_case7_scan_payload_ignores_non_strings() -> None:
    assert scan_payload({"a": 1, "b": None, "c": True, "d": 2.5, "e": [False, 0]}) is None
    assert scan_payload(42) is None
    assert scan_payload(None) is None
    assert scan_payload(("ok", ["Ä±"])) == ("1.0", MojibakeHit(pair="Ä±", correct="ı", offset=0))


def test_case7_scan_payload_self_referencing_list_terminates() -> None:
    loop: list[object] = ["ok"]
    loop.append(loop)
    assert scan_payload(loop) is None
    loop.append("ÄŸ")
    assert scan_payload(loop) == ("2", MojibakeHit(pair="ÄŸ", correct="ğ", offset=0))


def test_case7_scan_payload_ten_thousand_deep_terminates() -> None:
    deep: object = "Ã¶"
    for _ in range(10_000):
        deep = [deep]
    result = scan_payload(deep)  # None or a hit, never RecursionError
    assert result is None or result[1].pair == "Ã¶"
    deep_dict: object = {"t": "Ã¶"}
    for _ in range(10_000):
        deep_dict = {"k": deep_dict}
    scan_payload(deep_dict)


def test_case8_detail_names_pair_and_letter_never_the_text() -> None:
    text = "Zebra Quokka Jüpiter Ã¶ Wombat"
    path, hit = scan_payload({"title": text}) or ("", None)
    assert hit is not None
    detail = mojibake_detail(path, hit)
    assert detail == {
        "code": "mojibake",
        "field": "title",
        "pair": "Ã¶",
        "correct": "ö",
        "reason": "metin bozuk kodlanmış görünüyor: 'Ã¶' → 'ö' olmalı; betiği BOM'lu kaydet",
    }
    for word in ("Zebra", "Quokka", "Jüpiter", "Wombat", "Zeb", "okk", "Wom"):
        assert word not in str(detail)
