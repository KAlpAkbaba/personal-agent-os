"""The realtime session contract version, import-free so health and the release
qualification can read it without touching the routes.

Bump on every change to the accepted fields of a request body (see contract.py).
  1 - M12 tracks A+E as first shipped.
  2 - ADR-0043: ``voice`` on create.
  3 - el hareketiyle kumanda, Stage 1 (ADR-0198): ``gesture`` on a client event.
"""

CONTRACT_VERSION = 3
#: Fields a version-1 server accepts on create; a client that gets 404 from the
#: contract endpoint is talking to v1.
LEGACY_V1_CREATE_FIELDS = (
    "client_kind",
    "transport",
    "language",
    "narration_session_id",
    "session_ttl_s",
)

__all__ = ["CONTRACT_VERSION", "LEGACY_V1_CREATE_FIELDS"]
