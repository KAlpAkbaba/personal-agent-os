"""One pass over a finished report: Turkish beside every non-Turkish quote (ADR-0189).

Where and why HERE, and not somewhere easier:

* not in :mod:`app.research.answers` — that module promises that the same report always
  renders the same Turkish, with no I/O at all, and a model call inside it would break
  both the promise and every test that relies on it;
* not inside the synthesiser — ``DeterministicSynthesisProvider`` is "seeded, offline, no
  model call, fully reproducible", and it stays that way;
* so: after the report is built and before it is stored, once per run. The owner asking
  to hear it again costs nothing, and the web view shows the same Turkish the voice reads.

The source's OWN words are never overwritten: the translation is stored next to them
(``text_tr``), so a claim can always be checked against what the page actually said.
"""

from __future__ import annotations

from typing import Any

from app.logging import get_logger
from app.research.plan import looks_turkish
from app.research.translate import TranslationProvider, TranslationResult

logger = get_logger("app.research.translation_pass")

#: How many quotes one run pays to translate: what the spoken answer can reach
#: (``app.research.answers.MAX_DETAIL_FINDINGS``) and nothing beyond it.
MAX_TRANSLATED_STATEMENTS = 6


def translate_report_details(
    report_json: dict[str, Any], *, provider: TranslationProvider
) -> dict[str, Any]:
    """``report_json`` with a Turkish rendering beside each non-Turkish detail quote.

    Returns the same object (mutated in place, as the caller's other report passes do).
    A provider that cannot translate leaves every quote exactly as it was and records why
    on the report - a research must never look translated when it is not.
    """
    translated = 0
    last: TranslationResult | None = None
    for section in report_json.get("details") or ():
        if not isinstance(section, dict):
            continue
        for statement in section.get("statements") or ():
            if not isinstance(statement, dict):
                continue
            text = str(statement.get("text") or "").strip()
            if not text or looks_turkish(text):
                continue
            if translated >= MAX_TRANSLATED_STATEMENTS:
                break
            result = provider.translate(text, source_hint=str(section.get("heading") or ""))
            last = result
            if not result.translated or not result.text.strip():
                continue
            statement["text_tr"] = result.text.strip()
            statement["translated_from"] = "en"
            translated += 1
    note: dict[str, Any] = {
        "provider": getattr(provider, "name", "unknown"),
        "statements": translated,
    }
    if last is not None and last.error_class:
        note["error_class"] = last.error_class
    if last is not None and last.model:
        note["model"] = last.model
    report_json["translation"] = note
    if translated:
        logger.info(
            "research_report_translated",
            provider=note["provider"],
            statements=translated,
        )
    return report_json


__all__ = ["MAX_TRANSLATED_STATEMENTS", "translate_report_details"]
