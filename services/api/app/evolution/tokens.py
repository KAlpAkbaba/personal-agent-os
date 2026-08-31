"""Strict token shapes for every value that can reach GENERATED SOURCE.

ADR-0024 security addendum (the M6 Critical lesson), restated by ADR-0025 §3:

    ANY value taken from a request or an observation that ends up in generated
    source MUST be validated to a strict token shape at the parsing CHOKE POINT
    *and* re-validated at each SPLICE POINT.

This module is that single vocabulary. `gaps.py`/`skills.py` call the
``require_*`` helpers when parsing a request into a ``SkillSpec`` (choke point),
and ``skills.py`` calls them again immediately before interpolating a value into
a rendered file (splice point). Nothing here does I/O; failure is always a typed
``EvolutionError`` so the REST surface maps it to 422 rather than 500.

The shapes are deliberately narrow: by construction a validated token cannot
contain a quote, a backslash, a newline, a comment marker or a dot-dot path
segment, so it can never terminate a string literal, escape a directory or open
a code block in a generated file.
"""

from __future__ import annotations

import re

from app.evolution.errors import EvolutionError, EvolutionErrorClass

# research.web.deep — dotted lowercase segments, 2..5 segments.
CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,4}$")
# A python-safe identifier used for module names, function names, skill names.
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
# Semantic version; the registry column is String(32).
VERSION_RE = re.compile(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")
# Manifest list entries: permissions, dependencies, health metric names, io names.
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}(\.[a-z][a-z0-9_]{0,47}){0,3}$")
# Free-ish prose that still reaches generated files (README/docstrings).
SUMMARY_RE = re.compile(r"^[\w \-,.:()/']{1,200}$", re.UNICODE)

OWNER_SCOPES = ("normal", "restricted", "elevated")

# Python identifiers we refuse even though they match IDENTIFIER_RE: emitting a
# generated function under one of these names would shadow a builtin or a
# dunder hook inside the generated module.
RESERVED_IDENTIFIERS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "import",
        "from",
        "class",
        "def",
        "return",
        "lambda",
        "async",
        "await",
        "run",  # reserved for the generated entrypoint itself
        "main",
    }
)

MAX_LITERAL_STR_LEN = 200


def _fail(field: str, value: object, expectation: str) -> EvolutionError:
    # The offending value is NOT echoed back: it is attacker-controlled text and
    # the API response/logs are not a place to reflect it.
    return EvolutionError(
        EvolutionErrorClass.VALIDATION_ERROR,
        f"{field} is not a valid {expectation}; refusing to derive code",
        details={"field": field, "expected": expectation},
    )


def require_capability_id(value: object, *, field: str = "capability_id") -> str:
    if not isinstance(value, str) or not CAPABILITY_ID_RE.match(value):
        raise _fail(field, value, "dotted capability id (a.b[.c])")
    return value


def require_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.match(value):
        raise _fail(field, value, "lowercase python identifier")
    if value in RESERVED_IDENTIFIERS or value.startswith("_"):
        raise _fail(field, value, "non-reserved identifier")
    return value


def require_version(value: object, *, field: str = "version") -> str:
    if not isinstance(value, str) or not VERSION_RE.match(value):
        raise _fail(field, value, "semantic version (major.minor.patch)")
    return value


def require_slug(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SLUG_RE.match(value):
        raise _fail(field, value, "lowercase slug")
    return value


def require_slug_list(values: object, *, field: str, max_items: int = 16) -> list[str]:
    if not isinstance(values, list | tuple):
        raise _fail(field, values, "list of lowercase slugs")
    if len(values) > max_items:
        raise _fail(field, values, f"list of at most {max_items} slugs")
    return [require_slug(v, field=f"{field}[{i}]") for i, v in enumerate(values)]


def require_summary(value: object, *, field: str = "summary") -> str:
    if not isinstance(value, str) or not SUMMARY_RE.match(value):
        raise _fail(field, value, "single-line summary (letters/digits/basic punctuation)")
    return value


def require_owner_scope(value: object, *, field: str = "owner_scope") -> str:
    if value not in OWNER_SCOPES:
        raise _fail(field, value, f"one of {OWNER_SCOPES}")
    return str(value)


def require_literal(value: object, *, field: str) -> str | int | float | bool:
    """A test/eval case value that will be emitted via ``repr()``.

    ``repr()`` of a str is already escape-safe, but the M6 lesson says do not
    rely on a single mechanism: the value is additionally restricted to a small
    set of scalar types, bounded length and printable single-line text.
    """
    if isinstance(value, bool) or isinstance(value, int):
        if isinstance(value, int) and abs(int(value)) > 10**12:
            raise _fail(field, value, "bounded integer")
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/Inf
            raise _fail(field, value, "finite float")
        return value
    if isinstance(value, str):
        if len(value) > MAX_LITERAL_STR_LEN:
            raise _fail(field, value, f"string of at most {MAX_LITERAL_STR_LEN} chars")
        if not value.isprintable():
            raise _fail(field, value, "printable single-line string")
        return value
    raise _fail(field, value, "scalar literal (str/int/float/bool)")


__all__ = [
    "CAPABILITY_ID_RE",
    "IDENTIFIER_RE",
    "MAX_LITERAL_STR_LEN",
    "OWNER_SCOPES",
    "RESERVED_IDENTIFIERS",
    "SLUG_RE",
    "SUMMARY_RE",
    "VERSION_RE",
    "require_capability_id",
    "require_identifier",
    "require_literal",
    "require_owner_scope",
    "require_slug",
    "require_slug_list",
    "require_summary",
    "require_version",
]
