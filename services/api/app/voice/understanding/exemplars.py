"""The intent exemplars the semantic engine is built from in production (ADR-0224 layer 2).

The exemplars ARE the corpus ("a corpus case is an exemplar"), and production cannot import
``tests/``: ``scripts/export_understanding_exemplars.py`` writes them to ``exemplars.json``
beside this module, and ``tests/unit/test_understanding_startup.py`` holds the committed
file byte-equal to today's corpus, so the two cannot drift.

The file is package data beside its one reader (the same decision as ``stt-confusions.json``
and ``thresholds.json``): ``COPY app ./app`` ships it. A file that cannot be right - an
intent the router does not know, a missing or extra field, an empty sentence - is refused
whole: a typo must never become an intent the policy can rank.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

EXEMPLARS_FILE: Final = "exemplars.json"
EXEMPLARS_VERSION: Final = 1
_ENTRY_KEYS: Final = frozenset({"intent", "sentence"})


def _known_intents() -> frozenset[str]:
    from app.voice.intents import Intent  # the router's enum; imported late (it is heavy)

    return frozenset(intent.value for intent in Intent)


def load_exemplars(path: Path | None = None) -> list[tuple[str, str]]:
    """``(intent, sentence)`` pairs, from the file beside this module unless a path is given."""
    if path is None:
        path = Path(__file__).with_name(EXEMPLARS_FILE)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != EXEMPLARS_VERSION:
        raise ValueError(f"{EXEMPLARS_FILE}: need an object with version {EXEMPLARS_VERSION}")
    entries = data.get("exemplars")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{EXEMPLARS_FILE}: 'exemplars' must be a non-empty list")
    known = _known_intents()
    out: list[tuple[str, str]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise ValueError(f"{EXEMPLARS_FILE}: entry {index} must have exactly intent, sentence")
        intent, sentence = entry["intent"], entry["sentence"]
        if not isinstance(intent, str) or intent not in known:
            raise ValueError(f"{EXEMPLARS_FILE}: entry {index} names an unknown intent {intent!r}")
        if not isinstance(sentence, str) or not sentence.strip():
            raise ValueError(f"{EXEMPLARS_FILE}: entry {index} has no sentence")
        out.append((intent, sentence))
    return out


__all__ = ["EXEMPLARS_FILE", "EXEMPLARS_VERSION", "load_exemplars"]
