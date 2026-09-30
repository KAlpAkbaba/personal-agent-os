## The ledger writers that know the machine stamp it (ADR-0221 follow-up; ledger-device-stamp)

**Decision.** `detail_json["device"]` is written through `app.narrative.device_writer.stamp_device`
by the writers that know which device acted: `actions.receipt.record_receipt(..., device=)`,
`OperatorService.start_task(..., device=)` (task rows + receipt), `mission_service._ledger`
(the one `mission.device_targets` word), and the research writers (quality gate, provider
fallback, completed, failed) via the run's device: its owner-set alias when it has one, else its
name. No device known = no stamp = 'bulut'. A stamp already on the detail is never overwritten.

**Why.** The collector reads the device only from that key, so "ofiste ne yaptın" found nothing
in real data.

**Also fixed.** `_record_provider_fallback` built its `ActivityEvent` without the required
`source`/`source_ref`; the TypeError was swallowed, so that ledger row was never written.

**Consequence.** `record_receipt` has ten callers; only operator passes `device` here. The callers
that know a device (voice tools holding the bound device) must pass it - see the lead's wiring list.
