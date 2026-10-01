"""What acting on an element WOULD BE (ADR-0207 c: risk comes from the contract).

The class is computed from the element - its role, its name, whether it submits - by the
rule of BROWSER_CAPABILITIES.md section 4 and the words of
``packages/protocol/browser-risk-markers.json``. The worker computes the same class from
the same inputs when the command arrives; this side gates BEFORE sending, and tells the
worker the ceiling it gated at, so a step that turns out to be more than it looked is
refused there and never silently performed.

A model's opinion of the risk is not an input anywhere in this module.

The folding is the worker's (``browser_agent.risk_markers.fold``), written here a second
time because the two services ship separately; ``tests/unit/test_webtask_risk.py`` holds
the two to each other by reading the worker's source and by running both on one table.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

from app.protocol_files import protocol_file
from app.webtask.types import (
    ACTION_BACK,
    ACTION_CHECK,
    ACTION_CLICK,
    ACTION_FILL,
    ACTION_NAVIGATE,
    ACTION_SCROLL,
    ACTION_SELECT,
    RISK_EXTERNAL_COMMUNICATION,
    RISK_HIGH_IMPACT,
    RISK_NAVIGATE,
    RISK_ORDER,
    RISK_REVERSIBLE_WRITE,
    Element,
    risk_rank,
)

#: The run-time copy of ``packages/protocol/browser-risk-markers.json``
#: (app/protocol_files.py): the image holds no repository.
MARKERS_PATH: Final[Path] = protocol_file("browser-risk-markers.json")

_ZERO_WIDTH = re.compile("[​‌‍⁠﻿­]")
_WHITESPACE = re.compile(r"\s+")
_TURKISH_FOLD = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "I": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ö": "o",
        "Ö": "o",
        "ü": "u",
        "Ü": "u",
        "ç": "c",
        "Ç": "c",
    }
)

#: Roles that hold a value or a choice rather than doing something when pressed.
FIELD_ROLES: Final = frozenset(
    {
        "textbox",
        "searchbox",
        "combobox",
        "listbox",
        "checkbox",
        "radio",
        "switch",
        "slider",
        "spinbutton",
    }
)
#: Field roles where PRESSING is the write: a click on a checkbox is ``set_checked``.
TOGGLE_ROLES: Final = frozenset({"checkbox", "radio", "switch"})


def fold(text: str) -> str:
    """NFKC, zero-width characters removed, whitespace collapsed, Turkish letters folded
    to ASCII (the dotted capital I BEFORE casefold), casefold."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text)
    folded = _ZERO_WIDTH.sub("", folded)
    folded = _WHITESPACE.sub(" ", folded)
    return folded.translate(_TURKISH_FOLD).casefold().strip()


def _compile(markers: list[str]) -> re.Pattern[str]:
    folded = sorted({fold(m) for m in markers if fold(m)}, key=lambda m: (-len(m), m))
    body = "|".join(re.escape(m).replace(r"\ ", " ") for m in folded)
    return re.compile(rf"(?<![0-9a-z])(?:{body})(?![0-9a-z])")


@dataclass(frozen=True, slots=True)
class Markers:
    high_impact: re.Pattern[str]
    payment: re.Pattern[str]
    external_communication: re.Pattern[str]
    version: int


@lru_cache(maxsize=1)
def markers() -> Markers:
    """The shared file, read once. No fallback when it is missing: a gate that classified
    from a list of its own would be a second policy."""
    shared = json.loads(MARKERS_PATH.read_text(encoding="utf-8"))
    return Markers(
        high_impact=_compile(list(shared["high_impact"])),
        payment=_compile(list(shared["payment"])),
        external_communication=_compile(list(shared["external_communication"])),
        version=int(shared["version"]),
    )


def is_high_impact(name: str) -> bool:
    return markers().high_impact.search(fold(name)) is not None


def is_payment(name: str) -> bool:
    """The subset of HIGH_IMPACT that moves money. A task never performs these at all."""
    return markers().payment.search(fold(name)) is not None


def is_external_communication(name: str) -> bool:
    return markers().external_communication.search(fold(name)) is not None


def classify_element(element: Element) -> str:
    """Contract section 4, from what the observation says the element IS.

    ``href_host`` being set is the observation's way of saying ``a[href]``; an element
    that is a link and nothing more is a navigation. The observation does not carry
    ``has_onclick`` - that stays in the worker - so this side uses the worker's own
    ``risk_hint`` for the one distinction it cannot make itself, and never LOWERS it.
    """
    name = element.name or ""
    if element.role in FIELD_ROLES and not element.submits:
        return RISK_REVERSIBLE_WRITE
    if is_high_impact(name):
        return RISK_HIGH_IMPACT
    if element.submits or is_external_communication(name):
        return RISK_EXTERNAL_COMMUNICATION
    if element.role == "link" and element.href_host is not None:
        hinted = element.risk_hint
        return RISK_REVERSIBLE_WRITE if hinted == RISK_REVERSIBLE_WRITE else RISK_NAVIGATE
    return RISK_REVERSIBLE_WRITE


def is_unnamed(name: str) -> bool:
    """No accessible name, or one with no letter and no digit in it: a glyph says
    nothing about what its control does."""
    return not any(ch.isalnum() for ch in fold(name))


def classify_write(element: Element) -> str:
    """What CHANGING an element would be - typing into it, choosing in it, ticking it -
    from its name and whether it submits, as a click is classified.

    The contract's rule lets a field be REVERSIBLE_WRITE whatever it is called, because
    PRESSING a field only puts the cursor in it. Changing one is another matter: a
    ``<select>`` that buys on change and a checkbox wired to a request are named by the
    page like any button, and are judged by that name.
    """
    name = element.name or ""
    if is_high_impact(name):
        return RISK_HIGH_IMPACT
    if element.submits or is_external_communication(name):
        return RISK_EXTERNAL_COMMUNICATION
    return RISK_REVERSIBLE_WRITE


def _unnamed_and_wired(action: str, element: Element) -> bool:
    """A control that has no name and submits, or sits in a form: nothing says what it
    does, and where it sits says it may send. Not a plain link (it goes somewhere and
    does nothing else), and not text entry: typing sends nothing, and what sends the
    form is judged when IT is pressed."""
    if not is_unnamed(element.name) or not (element.submits or element.in_form):
        return False
    if element.role == "link" and element.href_host is not None:
        return False
    if action == ACTION_FILL:
        return False
    return not (action == ACTION_CLICK and element.role in FIELD_ROLES - TOGGLE_ROLES)


def classify_step(action: str, element: Element | None) -> str:
    """The class of one step, read off its element. ``click`` is the contract's rule;
    ``fill`` / ``select_option`` / ``set_checked`` - and a click on a checkbox, which is
    ``set_checked`` by another name - are classified from the name and ``submits`` the
    same way. Filling a field that is called nothing special and SUBMITS nothing is
    reversible."""
    if action in (ACTION_NAVIGATE, ACTION_BACK, ACTION_SCROLL):
        return RISK_NAVIGATE
    if action in (ACTION_FILL, ACTION_SELECT, ACTION_CHECK) and element is None:
        return RISK_REVERSIBLE_WRITE
    if action in (ACTION_CLICK, ACTION_FILL, ACTION_SELECT, ACTION_CHECK) and element is not None:
        if action == ACTION_CLICK and element.role not in TOGGLE_ROLES:
            own = classify_element(element)
        else:
            own = classify_write(element)
        if _unnamed_and_wired(action, element):
            own = _stricter(own, RISK_EXTERNAL_COMMUNICATION)
        # Never below what the worker itself hinted: two readers, the stricter one wins.
        return _stricter(own, element.risk_hint)
    return RISK_HIGH_IMPACT  # an action this module does not know is not a safe one


def _stricter(own: str, other: str) -> str:
    if other in RISK_ORDER and risk_rank(other) > risk_rank(own):
        return other
    return own


__all__ = [
    "FIELD_ROLES",
    "MARKERS_PATH",
    "TOGGLE_ROLES",
    "classify_element",
    "classify_step",
    "classify_write",
    "fold",
    "is_external_communication",
    "is_high_impact",
    "is_payment",
    "is_unnamed",
    "markers",
]
