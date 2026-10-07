"""Conversation search folding: the owner types without Turkish letters and finds them anyway.

Test team round t-manual-20261006e: ``?q=ISIGI`` did not find a line saying 'ışığı'. The owner
types on a phone keyboard, often without Turkish letters or in capitals, so the needle and every
line fold to one plain spelling: the four i's (I, İ, ı, i) are one letter, and ç/ğ/ö/ş/ü (and the
circumflexed â/î/û) are their ASCII letters. 'ISIGI', 'isigi', 'IŞIĞI' and 'ışığı' all fold to
'isigi'; 'sut' finds 'süt'.

The fold runs the same in Python (the needle) and in SQL (the stored lines), so the search needs
no folded column and no migration. Every Turkish capital is spelled out before ``lower()``:
SQLite's lower() is ASCII-only and PostgreSQL's depends on the database's locale.
"""

from __future__ import annotations

from sqlalchemy import func

#: Each letter straight to its folded form (never through another row of the table, so the
#: order of the replacements cannot matter).
SEARCH_FOLD = (
    ("I", "i"),
    ("İ", "i"),
    ("ı", "i"),
    ("Ç", "c"),
    ("ç", "c"),
    ("Ğ", "g"),
    ("ğ", "g"),
    ("Ö", "o"),
    ("ö", "o"),
    ("Ş", "s"),
    ("ş", "s"),
    ("Ü", "u"),
    ("ü", "u"),
    ("Â", "a"),
    ("â", "a"),
    ("Î", "i"),
    ("î", "i"),
    ("Û", "u"),
    ("û", "u"),
)


def search_fold(text: str) -> str:
    for letter, folded in SEARCH_FOLD:
        text = text.replace(letter, folded)
    return text.lower()


def search_fold_sql(column):  # noqa: ANN001, ANN201
    for letter, folded in SEARCH_FOLD:
        column = func.replace(column, letter, folded)
    return func.lower(column)
