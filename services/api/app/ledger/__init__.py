"""Canonical Activity Ledger (M16 track A, M16_ACTIVITY_LEDGER_SPEC.md §1, §4).

One durable, structured, append-only stream (``activity_events``) for every
current and future subsystem, plus the proactive briefing queue
(``pending_briefings``) that decides what gets spoken to the owner and when.
"""
