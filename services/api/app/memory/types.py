"""Memory subsystem enums and constants (frozen foundation).

Canonical vocabulary shared by the data model, write policy, retrieval and
the REST surface. Values are persisted in CHECK-constrained columns — adding
a value requires a migration; renames are breaking.
"""

from enum import StrEnum


class MemoryClass(StrEnum):
    """The six first-class memory classes (M5 brief + MEMORY_SPEC §2)."""

    PREFERENCE = "preference"          # owner/profile: explicit + inferred
    EPISODIC = "episodic"              # what happened, when, where
    PROJECT = "project"                # project identity/decisions/context
    SEMANTIC = "semantic"              # learned facts with provenance/validity
    PROCEDURAL = "procedural"          # repeated workflows / proposed procedures
    VOICE_PREFERENCE = "voice_preference"  # narration/pronunciation/style


class WriteStage(StrEnum):
    """Write-policy ladder: ignore -> session -> candidate -> durable.

    `ignore` never produces a row; the persisted stages are below.
    Explicit owner instructions enter at DURABLE directly.
    """

    SESSION = "session"
    CANDIDATE = "candidate"
    DURABLE = "durable"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"  # replaced by a newer memory; excluded from
    # retrieval by default but kept for history. Forgetting is a HARD delete
    # (row + versions + evidence + embeddings), not a status.


class RetentionClass(StrEnum):
    SESSION = "session"    # ephemeral; swept after the session ends
    SHORT = "short"        # short-lived observations
    STANDARD = "standard"  # default durable retention
    PINNED = "pinned"      # owner-pinned; never auto-expired or auto-rewritten


class Actor(StrEnum):
    """Who caused a memory mutation (audit + authority ordering)."""

    OWNER = "owner"    # explicit owner instruction — highest authority
    SYSTEM = "system"  # deterministic system bookkeeping
    POLICY = "policy"  # inference/write-policy decisions — lowest authority


MEMORY_CLASSES = tuple(m.value for m in MemoryClass)
WRITE_STAGES = tuple(s.value for s in WriteStage)
MEMORY_STATUSES = tuple(s.value for s in MemoryStatus)
RETENTION_CLASSES = tuple(r.value for r in RetentionClass)

ENTITY_KINDS = (
    "project",
    "person",
    "device",
    "document",
    "decision",
    "system",
    "task",
    "capability",
)

# Inference thresholds: a single observation must never yield a
# high-confidence durable preference. Promotion candidate->durable requires
# BOTH evidence_count >= PROMOTE_MIN_EVIDENCE and confidence >=
# PROMOTE_MIN_CONFIDENCE; explicit owner instructions bypass the ladder.
PROMOTE_MIN_EVIDENCE = 3
PROMOTE_MIN_CONFIDENCE = 0.7
SINGLE_OBSERVATION_MAX_CONFIDENCE = 0.4

# The embedding dimension of the frozen vector column (see models/migration).
EMBEDDING_DIM = 256
