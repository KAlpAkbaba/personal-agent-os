Decision: `app/devices/cloud_registry.py` writes the two registry facts `execution.wiring` reads
(ADR-0220). One function, `sync_registry_facts(db, device)`, is called by the broker's hello path
right after `apply_hello`; it is idempotent and only ADDS (owner-set aliases and labels stay).
- A device with `platform == "cloud"` gets the alias `bulut` (case-insensitive; never on another platform).
- The label `owner_chrome` is DERIVED from the exact capability `browser.profile.owner`, never from the
  name, never implied by the family marker `browser.chrome`, never on a cloud device. It is written to
  `metadata_json.labels` because `wiring` reads `DeviceView.labels`; the capability stays the source.
- Fact found while reading: NO agent advertises `browser.profile.owner` today. The owner-Chrome
  enrollment is a file on the device (`owner-enrollment.json`, ADR-0113) and the hello carries nothing
  about it. So the label stays unwritten until the agent side advertises that capability.
- Add-only: a revoked enrollment does not remove the label (removal would also wipe a label the owner
  set by hand while no agent advertises the capability). Consequence: stale label after re-enrolment off.
Why: one writer, derived from what the device says, no second source of truth.
