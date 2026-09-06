"""Action receipts: WRITE -> READ-BACK -> SPEAK (docs/M18_ACTION_CONTRACT.md §1, §5.5).

Every capability that mutates something on the owner's behalf ends in an
:class:`app.actions.receipt.ActionReceipt`; the spoken acknowledgement is the
receipt's ``speech``, chosen from the read-back state, never from the intent.
"""

from app.actions.receipt import (
    BOOKKEEPING_PHRASES,
    FAKE_COMPLETION_PHRASES,
    ActionReceipt,
    contains_bookkeeping,
    contains_fake_completion,
    record_receipt,
)

__all__ = [
    "BOOKKEEPING_PHRASES",
    "FAKE_COMPLETION_PHRASES",
    "ActionReceipt",
    "contains_bookkeeping",
    "contains_fake_completion",
    "record_receipt",
]
