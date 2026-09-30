"""Ledger event types of the execution_target decision.

The lead registers these in ``app/ledger/vocabulary.py`` at merge time.
"""

from __future__ import annotations

from typing import Final

EXECUTION_SELECTED: Final = "execution.selected"
EXECUTION_FALLBACK: Final = "execution.fallback"
EXECUTION_REFUSED: Final = "execution.refused"

EXECUTION_EVENT_TYPES: Final = (EXECUTION_SELECTED, EXECUTION_FALLBACK, EXECUTION_REFUSED)
