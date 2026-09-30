"""The cloud browser worker (ADR-0213 PR 2): the Linux companion of the browser worker.

The worker speaks stdio to a companion; on Windows that is the C# agent. On the Cloud Core
host nothing plays that role, so this package does: it enrolls as the device ``bulut``
(platform ``cloud``), dials the Device Broker over the same protocol the Windows agent uses,
and relays commands to the unchanged worker child, clamped to READ + NAVIGATE.
"""
