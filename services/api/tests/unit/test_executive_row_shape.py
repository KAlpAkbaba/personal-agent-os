"""The row the Cockpit panel reads, held to the panel's own source.

`test_uistate_contract_halves.py` holds the two halves of the BUS to each other. This file
does the same for the REST row, because that is a second surface and it drifted the moment
the two M26 tracks met: the list route sent `id` / `current_step` / `steps_done` /
`steps_total`, the detail route sent `run_id` for the very same field, and the panel
absorbed both with `str(o.run_id) ?? str(o.id)`.

The fallback chain meant the integration worked. It also meant nothing was agreed: either
side could rename a field and no test anywhere would go red, which is exactly how M25's
`/v1/scenes` row and its panel ended up in different languages while every suite passed. A
cushion is not an agreement.

So the names are settled — the bus's own words, so one fact has one name wherever it is
read — and this file reads the TypeScript to prove they still are.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.executive.models import ExecutiveRunRow
from app.executive.service import run_dict

_RELATIVE = Path("apps") / "web" / "app" / "lib" / "cockpit" / "executive.ts"


def _find_panel_client() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _RELATIVE
        if candidate.is_file():
            return candidate
    return Path(__file__).resolve().parents[-1] / _RELATIVE


_PANEL_CLIENT = _find_panel_client()


def _web_row_fields() -> set[str]:
    """The fields `ExecutiveRunRow` declares in the panel's own client."""
    text = _PANEL_CLIENT.read_text(encoding="utf-8")
    match = re.search(r"export type ExecutiveRunRow = \{(.*?)\n\};", text, re.S)
    assert match is not None, f"ExecutiveRunRow not found in {_PANEL_CLIENT}"
    fields = set(re.findall(r"^\s{2}([a-z_]+)\??:", match.group(1), re.M))
    assert fields, "ExecutiveRunRow was found but declared no fields — this would compare nothing"
    return fields


def _row(**overrides: object) -> ExecutiveRunRow:
    now = datetime.now(UTC)
    row = ExecutiveRunRow(
        id=uuid.uuid4(),
        goal="Son üç gündeki gelişmeleri araştır",
        state="running",
        current_step="s2",
        steps_done=1,
        steps_total=5,
        source="voice",
        created_at=now,
        updated_at=now,
    )
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


def test_the_panel_client_is_where_this_guard_thinks_it_is() -> None:
    """A guard that reads another file is worthless if the path goes stale: a missing file
    must fail here rather than quietly stop comparing anything."""
    assert _PANEL_CLIENT.is_file(), f"the panel client is not at {_PANEL_CLIENT}"
    assert _web_row_fields()


def test_every_field_the_panel_declares_is_one_the_route_actually_sends() -> None:
    """The direction that breaks a panel: a field the route never sends reads as `null`
    there, and a row whose `run_id` is null cannot be clicked, cancelled or named."""
    sent = set(run_dict(_row()))
    missing = sorted(_web_row_fields() - sent)
    assert not missing, (
        f"the panel reads {missing}, which the route never sends — those would be null on "
        "every row, and a row is only as usable as its id"
    )


def test_the_route_sends_the_bus_names_and_not_the_row_s_own() -> None:
    """One fact, one name, on the bus and in the row alike. The old spellings are named
    here so a revert is loud rather than quiet."""
    sent = run_dict(_row())
    assert {"run_id", "step", "done", "total"} <= set(sent)
    for stale in ("id", "current_step", "steps_done", "steps_total"):
        assert stale not in sent, f"{stale} is the row's own word, not the one clients read"


def test_a_partial_run_names_what_did_not_verify() -> None:
    """Spec §6: the panel shows a partial run as what is MISSING, not as a summary. The row
    has held these reasons since the workflow wrote them; until the two halves were read
    against each other, nothing sent them anywhere."""
    row = _row(state="partial", partial_reasons_json={"s3": "not_found", "s4": "timeout"})
    missing = run_dict(row)["missing"]
    assert missing == [
        {"step": "s3", "reason": "not_found"},
        {"step": "s4", "reason": "timeout"},
    ]
    # And a run with nothing missing says so with an empty list, never a null the panel
    # would have to guess about.
    assert run_dict(_row())["missing"] == []
