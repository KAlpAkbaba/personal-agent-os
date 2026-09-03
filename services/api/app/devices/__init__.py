"""Devices layer (M13 track C, PROJECT_CONSTITUTION.md §11a).

Cloud Core's view of an owner-enrolled device: inventory, presence
(online/stale/offline), capabilities, health and selection. Built entirely on
top of ``app.broker`` (the durable Device/DeviceCommand/DeviceSession rows and
the process-local BrokerRuntime connection table) — this package adds no new
identity or transport concept, only the composed, owner-facing view and the
selection algorithm DEVICE_PROTOCOL.md/BROWSER_CAPABILITIES.md §8 describe.

Nothing in this package (or anything it calls) may contain a hardcoded device
name/id/path — every per-device fact comes from ``devices.metadata_json``
(owner-set aliases/labels/policy) or a live query, never a literal
(guarded by tests/unit/test_multi_device_invariant.py).
"""
