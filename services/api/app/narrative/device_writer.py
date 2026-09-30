"""For the ledger WRITERS: put the device word into ``detail_json["device"]``.

The collector reads a row's device from that key and treats a row that names none as a cloud
row (``bulut``). A writer that knows the machine a thing ran on calls :func:`stamp_device`
on the detail it is about to record, so "ofiste ne yaptın" can find the row.
"""

from __future__ import annotations

from typing import Any

from app.narrative.collector import canonical_device


def stamp_device(detail: dict[str, Any] | None, device: str | None) -> dict[str, Any]:
    """A copy of ``detail`` naming the device by its canonical word ('Ofiste' -> 'ofis', a
    machine name -> its folded self). No device, or one already named, leaves the detail as it
    was - a writer never turns a cloud row into a device row by guessing."""
    out = dict(detail or {})
    if not device or not device.strip() or out.get("device"):
        return out
    out["device"] = canonical_device(device)
    return out
