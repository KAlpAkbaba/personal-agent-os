"""Experience Engine + Experience Compiler (overnight plan Phase 2+3).

This package turns durable, already-canonical evidence — the Activity Ledger
(``app.ledger``), incidents and research reports — into memory (via the
EXISTING ``app.memory`` subsystem) and, one level up, into generalizable
procedural lessons (incident -> root cause -> resolution -> lesson).

It owns exactly one new table (``experience_lessons``, see ``models.py``) and
never duplicates anything the ledger or memory subsystems already do:

- ``engine.py``  — durable experience -> memory (episodic + corroborated
  semantic facts), via ``app.memory.service``.
- ``compiler.py`` — incident -> root cause -> resolution -> generalized
  lesson candidates, scored and optionally auto-recorded as a candidate-stage
  PROCEDURAL memory; owner-authorized promotion goes through
  ``app.memory.service.remember_explicit`` (see ``routes.py``).
- ``routes.py``  — owner-gated ``/v1/experience`` surface. NOT registered in
  ``app.main`` by this package; the integrator wires it in alongside the
  ``experience_lessons`` migration.
"""

from __future__ import annotations

EXPERIENCE_VERSION = 1

__all__ = ["EXPERIENCE_VERSION"]
