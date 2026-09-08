"""M26 Executive Autonomy (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md, ADR-0089).

A durable graph of steps the owner can ask about, pause, correct and cancel at any
moment, that ends in an honest state (completed, partial with what is missing named, or
cancelled) and never takes an external high-risk action on its own. No new capability is
implemented here — every step kind maps to ONE existing service call (research,
documents, artifacts, mail draft, calendar proposal, apps, scenes, synthesis); sending,
paying, deleting and publishing are not step kinds a graph can carry.
"""
