"""Small security-relevant primitives shared across ``app.artifacts.{spec,renderers}``
(security review of M22's Cloud Core half, ADR-0085 addendum 6, HIGH finding).

Kept in one tiny, dependency-free module rather than duplicated so the spec-level
refusal (``app.artifacts.spec``) and the renderers' own defence-in-depth neutralisation
(``app.artifacts.renderers``) can never quietly drift apart on exactly which leading
characters name a spreadsheet formula.
"""

from __future__ import annotations

#: Excel/LibreOffice treat a cell as a live FORMULA once it is opened when the cell's
#: text starts with "=" (an XLSX cell whose ``data_type`` becomes ``"f"``); "+" / "-" /
#: "@" are what a spreadsheet application additionally auto-converts into a formula the
#: moment it IMPORTS a CSV (the classic "CSV/formula injection" class — OWASP). Every
#: one of the four is refused the same way here, regardless of the render format the
#: caller eventually asks for, since a spec is validated once and rendered many times.
FORMULA_INJECTION_LEAD_CHARS: tuple[str, ...] = ("=", "+", "-", "@")

#: Characters a spreadsheet engine strips or ignores before it looks at the FIRST
#: character of a cell's text — a UTF-8 BOM some exporters prepend, and ordinary
#: whitespace/tab/newline characters a copy-paste or an ASR transcript might carry in
#: front of the real content. A cell reading ``"﻿=1+1"`` or ``"\t=1+1"`` is exactly
#: as live as ``"=1+1"`` once opened, so both must be caught the same way.
_LEAD_STRIP = "﻿ \t\r\n\v\f"


def is_formula_injection(value: str) -> bool:
    """Whether the STRING ``value`` — a spreadsheet/dataset cell, a column header, or a
    totals label — would become a live formula once opened in a spreadsheet
    application, after stripping a leading BOM/whitespace the way such an application
    would before sniffing the first character.

    Only ever meaningful for STRING values: a genuinely numeric cell (``int``/``float``)
    can never carry this, whatever its sign — a negative NUMBER -5 is not "-5" the
    string, and this function is never called on the former.
    """
    if not value:
        return False
    stripped = value.lstrip(_LEAD_STRIP)
    return bool(stripped) and stripped[0] in FORMULA_INJECTION_LEAD_CHARS


__all__ = ["FORMULA_INJECTION_LEAD_CHARS", "is_formula_injection"]
