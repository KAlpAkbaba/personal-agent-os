"""Goal Engine + Cognitive Core foundation (overnight plan Phase 4).

Goals sit ABOVE tasks (``app.artifacts.models.Task``): an owner-level intent,
with success criteria evaluated only from evidence, pursued by the
deterministic reference Cognitive Core loop in ``app.goals.cognitive``. See
that module and ``app.goals.service`` for the actual contracts; this package
intentionally re-exports nothing at the top level so callers are explicit
about whether they want the ORM (``app.goals.models``), the service layer
(``app.goals.service``), the loop (``app.goals.cognitive``) or the REST
surface (``app.goals.routes``).
"""
