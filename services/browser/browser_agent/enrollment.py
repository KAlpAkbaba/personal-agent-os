"""Browser enrollment: explicit authorization records for existing browsers.

Attaching to an already-running (owner) browser is never ad-hoc debug-port
exposure (ADR-0019). It is modeled exactly like device enrollment (M1): an
explicit, owner-created authorization record naming the browser, the
transport, the endpoint, and the granted capability overrides. The
``ExistingSessionBackend`` refuses to attach without such a record and
refuses any endpoint that is not loopback.

Transports:

- ``cdp_loopback`` — implemented now: a loopback-only Chrome DevTools
  Protocol endpoint managed by a trusted local component (in production the
  owner-session companion; in tests a throwaway Chromium). Raw
  ``--remote-debugging-port`` against the owner's default profile is NOT the
  production path; the companion (or extension bridge) owns the endpoint
  lifecycle.
- ``extension_bridge`` — reserved enum value for the future browser-extension
  attach path; constructing a backend from it is a typed ``validation_error``
  until it ships.

Scope note (documented deliberately): :class:`EnrollmentRegistry` here is the
in-process authorization seam the orchestrator uses in M2. Persisting
enrollments in the broker database and surfacing owner approval flows is a
later-milestone integration; the record shape is stable so that move is
mechanical.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from .errors import BrowserError, ErrorClass
from .obs_logging import get_logger

logger = get_logger(__name__)


class Transport(StrEnum):
    """How an enrolled browser is reached."""

    CDP_LOOPBACK = "cdp_loopback"
    EXTENSION_BRIDGE = "extension_bridge"  # reserved; not implemented in M2


_LOOPBACK_HOSTS = {"localhost", "::1"}


def is_loopback_endpoint(endpoint: str) -> bool:
    """True iff the endpoint URL's host is a loopback address."""
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https", "ws", "wss"}:
        return False
    host = parsed.hostname
    if host is None:
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


@dataclass(frozen=True, slots=True)
class BrowserEnrollment:
    """One authorized browser attachment record."""

    id: str
    name: str
    transport: Transport
    endpoint: str
    capability_overrides: dict[str, bool] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def cdp_loopback(
        cls,
        endpoint: str,
        *,
        name: str = "",
        enrollment_id: str | None = None,
        capability_overrides: dict[str, bool] | None = None,
    ) -> BrowserEnrollment:
        """Convenience constructor for the implemented transport."""
        return cls(
            id=enrollment_id or str(uuid.uuid4()),
            name=name or "cdp-loopback-browser",
            transport=Transport.CDP_LOOPBACK,
            endpoint=endpoint,
            capability_overrides=dict(capability_overrides or {}),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "transport": str(self.transport),
            "endpoint": self.endpoint,
            "capability_overrides": dict(self.capability_overrides),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BrowserEnrollment:
        try:
            return cls(
                id=str(data["id"]),
                name=str(data["name"]),
                transport=Transport(str(data["transport"])),
                endpoint=str(data["endpoint"]),
                capability_overrides=dict(data.get("capability_overrides") or {}),  # type: ignore[arg-type]
                created_at=datetime.fromisoformat(str(data["created_at"])),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"malformed enrollment record: {exc}",
                retryable=False,
                evidence={"record": data},
            ) from exc


class EnrollmentRegistry:
    """In-memory registry of browser enrollments, optionally file-backed.

    This is the authorization seam the orchestrator consults before creating
    an ``ExistingSessionBackend``. Broker/DB-backed storage arrives in a later
    milestone (see module docstring); the file backing exists so a local
    single-owner deployment survives restarts.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._enrollments: dict[str, BrowserEnrollment] = {}
        if self._path is not None and self._path.exists():
            self._load()

    def register(self, enrollment: BrowserEnrollment) -> None:
        if enrollment.id in self._enrollments:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"enrollment id {enrollment.id!r} is already registered",
                retryable=False,
            )
        self._enrollments[enrollment.id] = enrollment
        self._save()
        logger.info(
            "browser.enrollment_registered",
            enrollment_id=enrollment.id,
            name=enrollment.name,
            transport=str(enrollment.transport),
        )

    def get(self, enrollment_id: str) -> BrowserEnrollment:
        enrollment = self._enrollments.get(enrollment_id)
        if enrollment is None:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"unknown enrollment id {enrollment_id!r}; browser attach is "
                "only allowed through a registered enrollment",
                retryable=False,
            )
        return enrollment

    def list(self) -> list[BrowserEnrollment]:
        return sorted(self._enrollments.values(), key=lambda e: e.created_at)

    def revoke(self, enrollment_id: str) -> None:
        if enrollment_id not in self._enrollments:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"unknown enrollment id {enrollment_id!r}",
                retryable=False,
            )
        del self._enrollments[enrollment_id]
        self._save()
        logger.info("browser.enrollment_revoked", enrollment_id=enrollment_id)

    # ------------------------------------------------------------------ #
    # persistence
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        assert self._path is not None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            records = data["enrollments"]
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"enrollment registry file {self._path} is unreadable/corrupt: {exc}",
                retryable=False,
            ) from exc
        self._enrollments = {
            record["id"]: BrowserEnrollment.from_dict(record) for record in records
        }

    def _save(self) -> None:
        if self._path is None:
            return
        payload = json.dumps(
            {"enrollments": [e.as_dict() for e in self.list()]}, indent=2
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)
