"""Time-ordered row ids for the append-only focus tables.

Both focus stacks in this service — ``app.research.models.ResearchFocusRow`` (ADR-0076)
and ``app.operator.models.ObjectFocusRow`` (ADR-0082) — answer "which object is the owner
pointing at?" with the most recent row, read as ``ORDER BY selected_at DESC, id DESC``.
With a random v4 id that second key is a lottery, and two rows stamped with one identical
instant — Windows' default wall clock is far coarser than a microsecond — come out in
random order (the flaky ``test_voice_research_followup`` binding, 2026-09-08).

Each module's write path already refuses to CREATE a tie (a monotonic default clock, and
in ``app.research.focus`` a new row's instant pushed past the newest row's). This module
is the guard for a tie that is nevertheless IN the table: rows written before those rules,
a caller passing one explicit ``now=`` twice, or two writers in concurrent transactions
that no per-process clock can serialise. Two ids made in the same millisecond differ in
the counter, so insertion order IS id order and the later act reads as the more recent
one. Hex-string (SQLite) and native (PostgreSQL) uuid columns both sort these bytewise,
which is numeric order.

Nothing on the wire or in the schema changes: a v7 UUID is a UUID, both migrations stand
(``0022_research_focus``, ``0025_object_focus``, neither of which has a server-side
default for ``id``), and existing rows keep the ids they have.

It lives here, at the top of ``app``, rather than in either feature package so that
``app.operator`` need not import ``app.research`` (or the reverse) for an id.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_focus_id_lock = threading.Lock()
_focus_id_last_ms = 0
_focus_id_counter = 0


def focus_row_id() -> uuid.UUID:
    """A UUIDv7: 48 bits of unix milliseconds, a 12-bit in-millisecond counter, 62 random
    bits. Strictly increasing within this process (a clock that steps back holds the last
    millisecond and counts on); millisecond-ordered across processes."""
    global _focus_id_last_ms, _focus_id_counter
    with _focus_id_lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _focus_id_last_ms:
            ms = _focus_id_last_ms
            _focus_id_counter += 1
            if _focus_id_counter > 0xFFF:  # the counter wrapped: step the millisecond
                ms += 1
                _focus_id_counter = 0
        else:
            _focus_id_counter = 0
        _focus_id_last_ms = ms
        counter = _focus_id_counter
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (
        ((ms & ((1 << 48) - 1)) << 80)
        | (0x7 << 76)  # version
        | (counter << 64)  # rand_a, used as the monotonic counter (RFC 9562 §6.2 method 1)
        | (0b10 << 62)  # variant
        | rand_b
    )
    return uuid.UUID(int=value)


__all__ = ["focus_row_id"]
