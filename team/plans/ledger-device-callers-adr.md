## ADR (unnumbered): operator ledger rows name the bound device

Context: ADR-0228 added the device stamp to the ledger writers (`start_task(device=)`,
`record_receipt(device=)`) but no caller passed it, so real operator rows stayed unstamped.

Decision: one helper, `_bound_device_word(ctx, device_action)` in `tools_operator.py`, returns the
word to stamp: the first `targets` entry when the sentence named a machine, else the first alias
(or name) of `session_device_ids[0]` via the existing `_device_named`; `None` when nothing is
bound or unreadable. Every `operator.start_task(...)` in `tools_operator.py` and its `_receipt`
`record_receipt` pass it. `actions.py` is unchanged: its receipts (eye, release.promote) are
cloud-side facts with no device, and stay `bulut` on purpose.

Consequence: "ofiste ne yaptın" now counts operator tasks and receipts. Evidence PROVEN_REAL
needs the owner asking after an operator action on the office PC.
