"""The ONE place ``"<step_id>.<output_name>"`` — a step's input reference syntax (spec
§1) — is parsed. ``app.executive.graph`` (DAG validation) and ``app.executive.activities``
(resolving a step's real inputs before it runs) both need the identical parse; a second
regex anywhere else risks the same "two tables that must agree will not" drift
``app.voice.intents._explain_kind`` warns about for Turkish pattern tables.
"""

from __future__ import annotations

import re

#: A value that does not match this shape, or whose left part names no step in the
#: graph, is a literal (spec §1: "``<step id>.<output name>`` | literal").
REFERENCE_RE = re.compile(r"^([A-Za-z0-9_]{1,8})\.([A-Za-z0-9_]{1,64})$")


def parse_reference(value: str) -> tuple[str, str] | None:
    """(step_id, output_name) if ``value`` has reference shape, else ``None``. Does
    NOT check that ``step_id`` actually exists in any particular graph — the caller
    decides what "not a real step" means (graph.py: a literal; activities.py: a
    dangling reference, which is an activity bug since graph.py already refused any
    dangling reference at plan time)."""
    match = REFERENCE_RE.match(value)
    return (match.group(1), match.group(2)) if match else None


__all__ = ["REFERENCE_RE", "parse_reference"]
