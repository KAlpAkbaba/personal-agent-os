"""Which recogniser wrote a local-mode sentence (ADR-0249 D4, stt-engine-on-turn-audit).

The web client's local mode posts every utterance with ``payload.stt_engine`` - one of the
three names of ``SttEngine`` in ``apps/web/app/lib/voice/localMode.ts``. The server keeps
that name on the turn's audit row, the turn record and the session activity, so the two
engines can be compared afterwards. The vocabulary is the client's, letter for letter: a
second spelling of the same fact would split one column into two.

The name is recorded and decides nothing. Anything that is not exactly one of the three
names is ``None`` - never truncated into a name, never guessed, never raised.
"""

from __future__ import annotations

from typing import Any

#: The three names the client sends (``SttEngine``); a test reads the web source and
#: asserts the two sets are equal.
STT_ENGINES: frozenset[str] = frozenset({"chrome-cihaz-ici", "chrome-bulut", "bilinmiyor"})

#: No name is longer than this; a longer value is not a name.
MAX_LEN = 64


def normalise(value: Any) -> str | None:
    """The engine name ``value`` is, or ``None`` when it is not one of the three."""
    if not isinstance(value, str) or len(value) > MAX_LEN:
        return None
    return value if value in STT_ENGINES else None


__all__ = ["MAX_LEN", "STT_ENGINES", "normalise"]
