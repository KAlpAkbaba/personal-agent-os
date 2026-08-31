"""M5 Memory subsystem — first-class, evidence-linked owner memory.

Foundation (lead-authored, frozen): types, ORM models, embedding seam,
migration 0005. Behavior (write policy, lifecycle, retrieval, evaluation)
builds on top without changing the core data model (MEMORY_SPEC.md §3:
PostgreSQL is canonical; any external memory framework sits behind an
adapter and never becomes an opaque source of truth).
"""
