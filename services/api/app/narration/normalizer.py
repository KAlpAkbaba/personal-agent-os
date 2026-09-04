"""Deterministic tr-TR narration normalizer (VOICE_SPEC §4/§5).

Converts *written* Turkish text into a *spoken* form suitable for TTS. It is a
pure function: same input + same pronunciation dict -> same output, no
randomness, no network, no clock.

Design: a fixed, ordered pipeline of regex substitutions, most-specific first.
Every substitution emits final spoken words (letters/spaces only), so a later,
more general numeric rule can never re-match text an earlier rule already
resolved. The ordering is the contract; see ``_PIPELINE``.

Two modes:

- ``"narration"`` (default): reader-friendly. Grouped thousands ("1.250.000")
  collapse to a spoken magnitude ("bir milyon iki yüz elli bin").
- ``"technical"``: literal structure. Dotted numeric runs (IP octets, versions)
  are always read separated by "nokta"; grouped-thousands collapsing is off so a
  value like "192.168.100.200" is never misread as a magnitude.

The pronunciation dictionary (owner-editable, VOICE_SPEC §5) is applied to
whole-token matches and always wins over the built-in fallbacks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.narration import numbers

Mode = str  # "narration" | "technical"

MONTHS_TR = {
    1: "Ocak",
    2: "Şubat",
    3: "Mart",
    4: "Nisan",
    5: "Mayıs",
    6: "Haziran",
    7: "Temmuz",
    8: "Ağustos",
    9: "Eylül",
    10: "Ekim",
    11: "Kasım",
    12: "Aralık",
}

# Turkish letter names for spelling out acronyms as a *mechanical* fallback
# (not a stored pronunciation guess): "IP" -> "i pe". Owner dict overrides.
LETTER_NAMES_TR = {
    "A": "a",
    "B": "be",
    "C": "ce",
    "Ç": "çe",
    "D": "de",
    "E": "e",
    "F": "fe",
    "G": "ge",
    "Ğ": "yumuşak ge",
    "H": "he",
    "I": "ı",
    "İ": "i",
    "J": "je",
    "K": "ka",
    "L": "le",
    "M": "me",
    "N": "ne",
    "O": "o",
    "Ö": "ö",
    "P": "pe",
    "Q": "ku",
    "R": "re",
    "S": "se",
    "Ş": "şe",
    "T": "te",
    "U": "u",
    "Ü": "ü",
    "V": "ve",
    "W": "çift ve",
    "X": "iks",
    "Y": "ye",
    "Z": "ze",
}

_TR_UPPER = "A-ZÇĞİÖŞÜ"


def _spell_acronym(token: str) -> str:
    letters = [LETTER_NAMES_TR.get(ch, ch) for ch in token if ch != "."]
    return " ".join(letters)


# --------------------------------------------------------------------- helpers


def _digits_with_nokta(digit_run: str) -> str:
    """'192.168.1.20' -> 'yüz doksan iki nokta yüz altmış sekiz nokta ...'."""
    parts = [numbers.cardinal(int(p)) for p in digit_run.split(".")]
    return " nokta ".join(parts)


def _grouped_int(token: str) -> int:
    return int(token.replace(".", ""))


# ---------------------------------------------------------------- substitutions
# Each stage is (name, compiled_regex, replacement_callable). ``ctx`` carries the
# mode and merged pronunciation dict so callables stay pure w.r.t. their inputs.


class _Ctx:
    __slots__ = ("mode", "pron")

    def __init__(self, mode: Mode, pron: Mapping[str, str]) -> None:
        self.mode = mode
        self.pron = pron


def _sub_email(m: re.Match[str], ctx: _Ctx) -> str:
    local, domain = m.group("local"), m.group("domain")
    local = local.replace(".", " nokta ").replace("_", " alt çizgi ").replace("-", " tire ")
    domain = domain.replace(".", " nokta ").replace("-", " tire ")
    return f"{local} et {domain}"


def _sub_url(m: re.Match[str], ctx: _Ctx) -> str:
    rest = m.group("host") + (m.group("path") or "")
    rest = rest.rstrip("/.")
    rest = rest.replace("://", " ").replace("/", " bölü ").replace(".", " nokta ")
    rest = rest.replace("-", " tire ").replace("_", " alt çizgi ")
    return re.sub(r"\s+", " ", rest).strip()


def _sub_winpath(m: re.Match[str], ctx: _Ctx) -> str:
    drive = m.group("drive")
    tail = m.group("tail")
    tail = tail.replace("\\", " ters bölü ").replace(".", " nokta ")
    return f"{drive}{tail}"


def _sub_cidr(m: re.Match[str], ctx: _Ctx) -> str:
    return f"{_digits_with_nokta(m.group('ip'))} bölü {numbers.cardinal(int(m.group('mask')))}"


def _sub_mask(m: re.Match[str], ctx: _Ctx) -> str:
    # A bare "/24" network mask.
    return f"bölü {numbers.cardinal(int(m.group('mask')))}"


def _sub_ip(m: re.Match[str], ctx: _Ctx) -> str:
    return _digits_with_nokta(m.group(0))


def _sub_date_dmy(m: re.Match[str], ctx: _Ctx) -> str:
    day, month, year = int(m.group("d")), int(m.group("mo")), int(m.group("y"))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return m.group(0)
    return f"{numbers.cardinal(day)} {MONTHS_TR[month]} {numbers.cardinal(year)}"


def _sub_date_iso(m: re.Match[str], ctx: _Ctx) -> str:
    year, month, day = int(m.group("y")), int(m.group("mo")), int(m.group("d"))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return m.group(0)
    tail = m.group("time") or ""
    date_words = f"{numbers.cardinal(day)} {MONTHS_TR[month]} {numbers.cardinal(year)}"
    if tail:
        # An ISO stamp may separate the time with "T" and end with a zone or a fraction
        # ("2026-09-04T18:28:21.125418Z"); none of that is spoken.
        clock = re.sub(r"[.,]\d+", "", tail.strip().lstrip("Tt"))
        clock = re.sub(r"(?:Z|[+-]\d{2}:?\d{2})$", "", clock).strip()
        if not re.fullmatch(r"\d{1,2}(?::\d{2}){0,2}", clock):
            return date_words
        return f"{date_words} saat {_clock_words(clock)}"
    return date_words


def _clock_words(hms: str) -> str:
    parts = hms.split(":")
    out: list[str] = []
    for i, p in enumerate(parts):
        if i == 0:
            out.append(numbers.cardinal(int(p)))
        elif len(p) == 2 and p[0] == "0":
            out.append(numbers.digit_by_digit(p))
        else:
            out.append(numbers.cardinal(int(p)))
    return " ".join(out)


def _sub_clock_colon(m: re.Match[str], ctx: _Ctx) -> str:
    return _clock_words(m.group("t"))


def _sub_clock_saat(m: re.Match[str], ctx: _Ctx) -> str:
    hh, mm = m.group("h"), m.group("m")
    return f"saat {_clock_words(f'{int(hh)}:{mm}')}"


def _sub_version(m: re.Match[str], ctx: _Ctx) -> str:
    prefix_raw = (m.group("v") or "").strip().lower()
    prefix = "sürüm " if prefix_raw in ("v", "sürüm", "versiyon") else ""
    body = " nokta ".join(numbers.cardinal(int(p)) for p in m.group("num").split("."))
    return f"{prefix}{body}"


def _sub_lira(m: re.Match[str], ctx: _Ctx) -> str:
    return f"{_money_amount(m.group('amt'), ctx)} Türk lirası"


def _sub_euro(m: re.Match[str], ctx: _Ctx) -> str:
    return f"{_money_amount(m.group('amt'), ctx)} avro"


def _sub_dollar(m: re.Match[str], ctx: _Ctx) -> str:
    return f"{_money_amount(m.group('amt'), ctx)} dolar"


def _sub_tl_suffix(m: re.Match[str], ctx: _Ctx) -> str:
    return f"{_money_amount(m.group('amt'), ctx)} Türk lirası"


def _money_amount(amt: str, ctx: _Ctx) -> str:
    """Read a possibly grouped/decimal money amount into words."""
    amt = amt.strip()
    if "," in amt:
        int_part, frac = amt.split(",", 1)
        return numbers.decimal(int_part.replace(".", ""), frac)
    return numbers.cardinal(_grouped_int(amt))


def _sub_percent(m: re.Match[str], ctx: _Ctx) -> str:
    return f"yüzde {_normalize_number_token(m.group('num'), ctx)}"


def _sub_grouped(m: re.Match[str], ctx: _Ctx) -> str:
    if ctx.mode == "technical":
        return m.group(0)  # keep literal; a dotted run is structure in technical mode
    return numbers.cardinal(_grouped_int(m.group(0)))


def _sub_decimal(m: re.Match[str], ctx: _Ctx) -> str:
    return numbers.decimal(m.group("i"), m.group("f"))


def _sub_dotted_tech(m: re.Match[str], ctx: _Ctx) -> str:
    # Only reached in technical mode: any leftover dotted numeric run -> nokta.
    return _digits_with_nokta(m.group(0))


def _sub_phone(m: re.Match[str], ctx: _Ctx) -> str:
    raw = m.group(0)
    spoken = [
        "artı" if ch == "+" else numbers.digit_by_digit(ch)
        for ch in raw
        if ch.isdigit() or ch == "+"
    ]
    return " ".join(spoken)


def _sub_ordinal_apos(m: re.Match[str], ctx: _Ctx) -> str:
    return numbers.ordinal(int(m.group("n")))


def _sub_ordinal_noun(m: re.Match[str], ctx: _Ctx) -> str:
    return f"{numbers.ordinal(int(m.group('n')))} {m.group('noun')}"


def _normalize_number_token(tok: str, ctx: _Ctx) -> str:
    tok = tok.strip()
    if "," in tok:
        int_part, frac = tok.split(",", 1)
        return numbers.decimal(int_part.replace(".", ""), frac)
    if "." in tok:
        if ctx.mode == "technical":
            return _digits_with_nokta(tok)
        return numbers.cardinal(_grouped_int(tok))
    return numbers.cardinal(int(tok))


def _sub_integer(m: re.Match[str], ctx: _Ctx) -> str:
    return numbers.cardinal(int(m.group(0)))


# Ordered pipeline. Order is the correctness contract (specific -> general).
_PIPELINE: list[tuple[str, re.Pattern[str], object]] = [
    (
        "email",
        re.compile(r"(?P<local>[\w.\-+]+)@(?P<domain>[\w\-]+(?:\.[\w\-]+)+)", re.UNICODE),
        _sub_email,
    ),
    (
        "url",
        re.compile(
            r"\bhttps?://(?P<host>[\w\-]+(?:\.[\w\-]+)+)(?P<path>(?:/[\w\-./%]*)?)",
            re.UNICODE,
        ),
        _sub_url,
    ),
    (
        "winpath",
        re.compile(r"(?P<drive>[A-Za-z]):(?P<tail>(?:\\[^\s\\]+)+)", re.UNICODE),
        _sub_winpath,
    ),
    (
        "cidr",
        re.compile(r"\b(?P<ip>\d{1,3}(?:\.\d{1,3}){3})/(?P<mask>\d{1,2})\b"),
        _sub_cidr,
    ),
    (
        "ip",
        re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
        _sub_ip,
    ),
    (
        "mask",
        re.compile(r"(?<![\w/])/(?P<mask>\d{1,2})\b"),
        _sub_mask,
    ),
    (
        "date_iso",
        re.compile(
            r"\b(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})"
            r"(?P<time>[ T]\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
        ),
        _sub_date_iso,
    ),
    (
        "date_dmy",
        re.compile(r"\b(?P<d>\d{1,2})\.(?P<mo>\d{1,2})\.(?P<y>\d{4})\b"),
        _sub_date_dmy,
    ),
    (
        "clock_saat",
        re.compile(r"\bsaat\s+(?P<h>\d{1,2})\.(?P<m>\d{2})\b", re.IGNORECASE),
        _sub_clock_saat,
    ),
    (
        "clock_colon",
        re.compile(r"\b(?P<t>\d{1,2}:\d{2}(?::\d{2})?)\b"),
        _sub_clock_colon,
    ),
    (
        "lira",
        re.compile(r"₺\s*(?P<amt>\d[\d.,]*)"),
        _sub_lira,
    ),
    (
        "euro",
        re.compile(r"€\s*(?P<amt>\d[\d.,]*)"),
        _sub_euro,
    ),
    (
        "dollar",
        re.compile(r"\$\s*(?P<amt>\d[\d.,]*)"),
        _sub_dollar,
    ),
    (
        "tl_suffix",
        re.compile(r"(?P<amt>\d[\d.,]*)\s*(?:TL|TRY)\b"),
        _sub_tl_suffix,
    ),
    (
        "percent",
        re.compile(r"%\s*(?P<num>\d[\d.,]*)"),
        _sub_percent,
    ),
    (
        "ordinal_apos",
        re.compile(r"\b(?P<n>\d+)['’](?:inci|ıncı|uncu|üncü|nci|ncı|ncu|ncü)\b", re.UNICODE),
        _sub_ordinal_apos,
    ),
    (
        "ordinal_noun",
        re.compile(
            r"\b(?P<n>\d+)\.\s*(?P<noun>(?:madde|bölüm|sınıf|kat|sıra|adım|aşama|başlık|gün|ay|hafta|yıl)\w*)",
            re.UNICODE,
        ),
        _sub_ordinal_noun,
    ),
    (
        "phone",
        re.compile(r"(?:\+90|0)[\s(]?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b"),
        _sub_phone,
    ),
    (
        "grouped",
        re.compile(r"\b\d{1,3}(?:\.\d{3})+\b"),
        _sub_grouped,
    ),
    (
        "decimal",
        re.compile(r"\b(?P<i>\d+),(?P<f>\d+)\b"),
        _sub_decimal,
    ),
    # version runs AFTER grouped/decimal so it never swallows "1.250.000"
    # (grouped) or a comma decimal; here it only sees dotted runs like "1.3".
    (
        "version",
        re.compile(r"(?P<v>[vV]|sürüm\s|versiyon\s)?(?P<num>\d+(?:\.\d+){1,3})\b"),
        _sub_version,
    ),
    (
        "dotted_tech",
        re.compile(r"\b\d+(?:\.\d+)+\b"),
        _sub_dotted_tech,
    ),
    (
        "integer",
        re.compile(r"\b\d+\b"),
        _sub_integer,
    ),
]


_ACRONYM_RE = re.compile(rf"(?<![\w])[{_TR_UPPER}]{{2,6}}(?![\w])", re.UNICODE)


def _acronym_fallback(text: str) -> str:
    """Spell out bare 2-6 letter ALL-CAPS acronyms via Turkish letter names."""
    return _ACRONYM_RE.sub(lambda m: _spell_acronym(m.group(0)), text)


def normalize(
    text: str,
    *,
    mode: Mode = "narration",
    pronunciation: Mapping[str, str] | None = None,
    spell_acronyms: bool = False,
) -> str:
    """Normalize written Turkish ``text`` to a spoken form.

    Parameters
    ----------
    text:
        Inline Turkish text (a sentence/paragraph). Block structures (tables,
        code fences) are handled by ``app.narration.tables`` before this call.
    mode:
        ``"narration"`` (default) or ``"technical"``. In technical mode dotted
        numeric runs are read as "nokta"-separated octets and grouped-thousands
        collapsing is disabled.
    pronunciation:
        Owner pronunciation dictionary; whole-token spoken forms that win over
        the built-in fallbacks (VOICE_SPEC §5).
    spell_acronyms:
        When True, bare ALL-CAPS acronyms not covered by ``pronunciation`` are
        spelled out with Turkish letter names ("IP" -> "i pe").
    """
    if not text:
        return ""
    ctx = _Ctx(mode, pronunciation or {})
    out = text

    # The owner dictionary runs BEFORE the substitution pipeline: an explicit
    # owner spoken form always wins (VOICE_SPEC §5, owner-authority precedence).
    # Running it afterwards silently loses every token containing digits or
    # punctuation ("CUDA12", "SQL2019", "1.250.000 TL" as an owner phrase),
    # because a numeric/date rule would already have rewritten it.
    pron = pronunciation or {}
    if pron:
        keys = sorted((re.escape(k) for k in pron), key=len, reverse=True)
        token_re = re.compile(r"(?<![\w])(?:" + "|".join(keys) + r")(?![\w])", re.UNICODE)

        def repl(m: re.Match[str]) -> str:
            tok = m.group(0)
            return pron.get(tok) or pron.get(tok.upper()) or tok

        out = token_re.sub(repl, out)

    for _name, pattern, fn in _PIPELINE:
        out = pattern.sub(lambda m, fn=fn: fn(m, ctx), out)  # type: ignore[operator]

    if spell_acronyms:
        out = _acronym_fallback(out)

    # Collapse whitespace introduced by separator expansions.
    out = re.sub(r"[ \t]+", " ", out).strip()
    return out


__all__ = ["normalize", "MONTHS_TR", "LETTER_NAMES_TR", "Mode"]
