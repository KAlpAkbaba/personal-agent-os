"""Presence Engine + Active Eye observation intake (M18_HOLOGRAPHIC_CORE_SPEC.md §1, §2).

Structured, image-free observations (``app.presence.observations``) are fused into an
evidence-backed, never-certain presence/wake assertion (``app.presence.states``,
``app.presence.engine``); a data-driven policy decides when that amounts to a morning or
return greeting (``app.presence.greeting``); the Active Eye disable path
(``app.presence.eye``) is durable and stops perception immediately; ``app.presence.service``
wires all of it to the Activity Ledger and the UI-state bus, published only on meaningful
transitions. This package intentionally re-exports nothing at the top level so callers are
explicit about whether they want the vocabulary (``app.presence.states``), the intake
boundary (``app.presence.observations``), the fusion engine (``app.presence.engine``), the
greeting policy (``app.presence.greeting``), the Active Eye (``app.presence.eye``), the
integration wiring (``app.presence.service``) or the REST surface (``app.presence.routes``).
"""
