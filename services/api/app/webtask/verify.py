"""VERIFY: does the step's expectation hold in a NEW observation (ADR-0207 a).

The rule every verification in this repository follows: never the act's own return
value. ``browser.click`` answering ``clicked: true`` says the click was delivered, not
that the cart has an item in it. So the loop observes again after every act and this
module reads the expectation off that second observation - which then serves as the next
round's first, so verifying costs no extra command.

Pure: two observations in, a verdict out.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.webtask.risk import fold
from app.webtask.types import (
    EXPECT_CHECKED,
    EXPECT_ELEMENT_ABSENT,
    EXPECT_ELEMENT_PRESENT,
    EXPECT_FIELD_HAS_VALUE,
    EXPECT_PAGE_CHANGED,
    EXPECT_TEXT_ABSENT,
    EXPECT_TEXT_PRESENT,
    EXPECT_URL_CONTAINS,
    EXPECTATIONS,
    Expectation,
    Observation,
)


@dataclass(frozen=True, slots=True)
class Verdict:
    ok: bool
    detail: str


#: What a form label ends with and a model leaves out: "Customer name:", "E-posta *"
#: (live run 2026-10-07 - a filled field was "no element with that name").
_LABEL_TAIL = " :*"


def _label(text: str) -> str:
    return fold(text).rstrip(_LABEL_TAIL)


def _elements_named(observation: Observation, name: str, role: str) -> list:
    wanted = _label(name)
    return [
        e for e in observation.elements if _label(e.name) == wanted and (not role or e.role == role)
    ]


def check(expectation: Expectation, before: Observation, after: Observation) -> Verdict:
    kind, value, role = expectation.kind, expectation.value, expectation.role
    if kind not in EXPECTATIONS:
        return Verdict(False, f"unknown expectation {kind!r}")
    if kind == EXPECT_PAGE_CHANGED:
        changed = after.url != before.url or after.digest() != before.digest()
        changed = changed or fold(after.text) != fold(before.text)
        return Verdict(changed, "the page changed" if changed else "the page is what it was")
    if not value:
        return Verdict(False, f"{kind} names nothing to look for")
    if kind == EXPECT_URL_CONTAINS:
        ok = value.casefold() in after.url.casefold()
        return Verdict(ok, "the address matches" if ok else "the address does not match")
    if kind in (EXPECT_TEXT_PRESENT, EXPECT_TEXT_ABSENT):
        present = fold(value) in fold(after.text)
        ok = present if kind == EXPECT_TEXT_PRESENT else not present
        return Verdict(ok, "the text is there" if present else "the text is not there")
    found = _elements_named(after, value, role)
    if kind == EXPECT_ELEMENT_PRESENT:
        return Verdict(bool(found), f"{len(found)} element(s) with that name")
    if kind == EXPECT_ELEMENT_ABSENT:
        return Verdict(not found, f"{len(found)} element(s) with that name")
    if not found:
        return Verdict(False, "no element with that name")
    if len(found) > 1:
        # Two fields with one name: which one holds the value cannot be read off a name.
        return Verdict(False, f"{len(found)} elements with that name; the check is ambiguous")
    state = found[0].state
    if kind == EXPECT_FIELD_HAS_VALUE:
        ok = "has_value" in state
        return Verdict(ok, "the field holds a value" if ok else "the field is empty")
    if kind == EXPECT_CHECKED:
        ok = "checked" in state
        return Verdict(ok, "it is ticked" if ok else "it is not ticked")
    return Verdict(False, f"unhandled expectation {kind!r}")  # pragma: no cover


__all__ = ["Verdict", "check"]
