"""Mobile service: push registrations, artifact-ready delivery, share/export.

Same discipline as `app/artifacts/service.py` and `app/identity/service.py`:
synchronous, one transaction per call, so async routes run it through
`asyncio.to_thread`.

Two rules shape everything here.

**A push target is an owner session, not a person.** Registrations hang off
`owner_sessions` and the live-target query *joins* that table and requires
`status = 'active'`. It is written as a join rather than as a mirrored
`push_registrations.status` flag on purpose: a session can be revoked by four
different paths (the owner signs out, the owner revokes it from another client,
the panic control fires, the session's *device* is revoked), and only one of
those four is a code path this module could ever have hooked. The join cannot
be forgotten by a future revocation path, which is what "device revocation
invalidates session" has to mean if it is to be worth anything.

**The notification carries readiness, never the report.** Constitution §3: a
completed background task sends a short readiness notification and waits. The
push body is a bounded Turkish sentence plus the artifact id; the client then
fetches the executive summary, and the full body only if the owner asks.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity.models import SESSION_STATUS_ACTIVE, OwnerSession
from app.logging import get_logger
from app.mobile.errors import MobileError, MobileErrorClass
from app.mobile.models import (
    PUSH_PROVIDERS,
    PUSH_STATUS_ACTIVE,
    PUSH_STATUS_INVALID,
    PUSH_STATUS_REVOKED,
    PushRegistration,
)
from app.mobile.providers import (
    MAX_BODY_CHARS,
    PushDelivery,
    PushMessage,
    PushProvider,
)

logger = get_logger("app.mobile.service")

SessionMaker = Callable[[], AbstractContextManager[Session]]

MAX_PLATFORM_CHARS = 32
MAX_LOCALE_CHARS = 16

#: Notification copy. Turkish is first-class (constitution §1); anything else
#: falls back to English rather than shipping an untranslated Turkish string.
_COPY = {
    "tr": {
        "title": "Araştırma tamamlandı",
        "body": "{title} hazır. Özet için dokun.",
    },
    "en": {
        "title": "Research complete",
        "body": "{title} is ready. Tap for the summary.",
    },
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; stored times are UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware else None


# ------------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class RegistrationView:
    """Detached view of a registration. Never carries a token or a hash."""

    registration_id: uuid.UUID
    session_id: uuid.UUID
    provider: str
    status: str
    platform: str
    locale: str
    created_at: datetime | None
    last_delivery_at: datetime | None
    revoked_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "registration_id": str(self.registration_id),
            "session_id": str(self.session_id),
            "provider": self.provider,
            "status": self.status,
            "platform": self.platform,
            "locale": self.locale,
            "created_at": _iso(self.created_at),
            "last_delivery_at": _iso(self.last_delivery_at),
            "revoked_at": _iso(self.revoked_at),
        }


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    registration_id: uuid.UUID
    provider: str
    ok: bool
    error_class: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "registration_id": str(self.registration_id),
            "provider": self.provider,
            "ok": self.ok,
            "error_class": self.error_class,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class NotificationResult:
    message: PushMessage
    outcomes: list[DeliveryOutcome] = field(default_factory=list)

    @property
    def delivered(self) -> int:
        return sum(1 for o in self.outcomes if o.ok)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if not o.ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification": self.message.to_dict(),
            "delivered": self.delivered,
            "failed": self.failed,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


# ------------------------------------------------------------------- service


class MobileService:
    def __init__(
        self,
        session_factory: SessionMaker,
        providers: dict[str, PushProvider],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._providers = providers
        self._clock = clock

    # --------------------------------------------------------------- plumbing

    def _sessions(self) -> AbstractContextManager[Session]:
        return self._session_factory()

    @property
    def providers(self) -> dict[str, PushProvider]:
        return self._providers

    def provider(self, name: str) -> PushProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise MobileError(
                MobileErrorClass.CAPABILITY_MISSING,
                f"no push provider named {name!r}",
                details={"known": sorted(self._providers)},
            ) from exc

    def capabilities(self) -> list[dict[str, Any]]:
        """What every transport can do, and which ones an owner action would
        activate. This is how the orchestrator picks a transport instead of
        assuming one."""
        out = []
        for name in sorted(self._providers):
            provider = self._providers[name]
            caps = provider.capabilities().to_dict()
            caps["activated"] = not caps["requires_credentials"] or self._activated(provider)
            out.append(caps)
        return out

    @staticmethod
    def _activated(provider: PushProvider) -> bool:
        """A transport that declares `configured` answers for itself; anything
        that does not need credentials is on by definition."""
        return bool(getattr(provider, "configured", True))

    @staticmethod
    def _view(row: PushRegistration) -> RegistrationView:
        return RegistrationView(
            registration_id=row.id,
            session_id=row.session_id,
            provider=row.provider,
            status=row.status,
            platform=row.platform or "",
            locale=row.locale or "tr-TR",
            created_at=_aware(row.created_at),
            last_delivery_at=_aware(row.last_delivery_at),
            revoked_at=_aware(row.revoked_at),
        )

    # ---------------------------------------------------------- registration

    def register(
        self,
        *,
        session_id: uuid.UUID,
        provider_name: str,
        token: str,
        platform: str = "",
        locale: str = "tr-TR",
    ) -> RegistrationView:
        """Bind a provider token to the CURRENT owner session (idempotent).

        Re-registering the same (session, provider) updates the row rather than
        creating a second one — the OS rotates push tokens, and a client that
        re-registers must not leave a dead target behind.
        """
        if provider_name not in PUSH_PROVIDERS:
            raise MobileError(
                MobileErrorClass.CAPABILITY_MISSING,
                f"unknown push provider: {provider_name!r}",
                details={"known": list(PUSH_PROVIDERS)},
            )
        provider = self.provider(provider_name)
        platform = (platform or "").strip()[:MAX_PLATFORM_CHARS]
        locale = (locale or "tr-TR").strip()[:MAX_LOCALE_CHARS]
        if platform and not provider.capabilities().supports_platform(platform):
            raise MobileError(
                MobileErrorClass.VALIDATION_ERROR,
                f"{provider_name} does not deliver to platform {platform!r}",
                provider=provider_name,
                details={"platforms": list(provider.capabilities().platforms)},
            )

        with self._sessions() as db:
            if self._session_row(db, session_id) is None:
                raise MobileError(
                    MobileErrorClass.SESSION_REVOKED,
                    "the owner session is not active",
                    details={"session_id": str(session_id)},
                )
            row = db.execute(
                select(PushRegistration).where(
                    PushRegistration.session_id == session_id,
                    PushRegistration.provider == provider_name,
                )
            ).scalar_one_or_none()
            registration_id = row.id if row is not None else uuid.uuid4()
            # The provider takes the plaintext token and keeps it in memory; we
            # persist only what it hands back, which is a hash.
            registered = provider.register(
                registration_key=str(registration_id), token=token, platform=platform
            )
            now = self._clock()
            if row is None:
                row = PushRegistration(
                    id=registration_id,
                    session_id=session_id,
                    provider=provider_name,
                    token_hash=registered.token_hash,
                    status=PUSH_STATUS_ACTIVE,
                    platform=platform,
                    locale=locale,
                    created_at=now,
                )
                db.add(row)
            else:
                row.token_hash = registered.token_hash
                row.status = PUSH_STATUS_ACTIVE
                row.platform = platform
                row.locale = locale
                row.revoked_at = None
            db.commit()
            view = self._view(row)
        logger.info(
            "push_registered",
            registration_id=str(view.registration_id),
            provider=provider_name,
            platform=platform,
            token_fingerprint=registered.token_fingerprint,
        )
        return view

    def unregister(
        self,
        *,
        registration_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        status: str = PUSH_STATUS_REVOKED,
        reason: str = "owner_unregistered",
    ) -> bool:
        """Retire a registration. `session_id` scopes it to the caller's own."""
        with self._sessions() as db:
            row = db.get(PushRegistration, registration_id)
            if row is None:
                return False
            if session_id is not None and row.session_id != session_id:
                # Do not leak the existence of another session's registration.
                return False
            if row.status != PUSH_STATUS_ACTIVE:
                return False
            row.status = status
            row.revoked_at = self._clock()
            provider_name = row.provider
            db.commit()
        try:
            self.provider(provider_name).invalidate(
                registration_key=str(registration_id), reason=reason
            )
        except MobileError:  # pragma: no cover - unknown provider cannot happen here
            logger.warning("push_invalidate_failed", registration_id=str(registration_id))
        logger.info(
            "push_unregistered", registration_id=str(registration_id), reason=reason
        )
        return True

    def list_registrations(
        self, *, session_id: uuid.UUID | None = None, active_only: bool = False
    ) -> list[RegistrationView]:
        stmt = select(PushRegistration).order_by(PushRegistration.created_at.desc())
        if session_id is not None:
            stmt = stmt.where(PushRegistration.session_id == session_id)
        if active_only:
            stmt = stmt.where(PushRegistration.status == PUSH_STATUS_ACTIVE)
        with self._sessions() as db:
            return [self._view(row) for row in db.execute(stmt).scalars().all()]

    # --------------------------------------------------------------- targets

    @staticmethod
    def _session_row(db: Session, session_id: uuid.UUID) -> OwnerSession | None:
        return db.execute(
            select(OwnerSession).where(
                OwnerSession.id == session_id,
                OwnerSession.status == SESSION_STATUS_ACTIVE,
            )
        ).scalar_one_or_none()

    def live_registrations(self) -> list[RegistrationView]:
        """Every registration that may actually be delivered to.

        The join is the enforcement point: a registration whose owner session is
        revoked or expired is not a live target, whatever its own status says.
        """
        stmt = (
            select(PushRegistration)
            .join(OwnerSession, OwnerSession.id == PushRegistration.session_id)
            .where(
                PushRegistration.status == PUSH_STATUS_ACTIVE,
                OwnerSession.status == SESSION_STATUS_ACTIVE,
            )
            .order_by(PushRegistration.created_at)
        )
        with self._sessions() as db:
            return [self._view(row) for row in db.execute(stmt).scalars().all()]

    # ---------------------------------------------------------- notification

    @staticmethod
    def build_artifact_ready_message(
        *,
        artifact_id: uuid.UUID,
        title: str,
        task_id: uuid.UUID | None = None,
        locale: str = "tr-TR",
    ) -> PushMessage:
        """The readiness notification. Bounded, and it never carries the report."""
        copy = _COPY.get(locale.split("-")[0].lower(), _COPY["en"])
        body = copy["body"].format(title=title.strip() or "Rapor")
        if len(body) > MAX_BODY_CHARS:
            body = body[: MAX_BODY_CHARS - 1] + "…"
        data = {"kind": "artifact_ready", "artifact_id": str(artifact_id)}
        if task_id is not None:
            data["task_id"] = str(task_id)
        return PushMessage(
            title=copy["title"],
            body=body,
            data=data,
            # One artifact, one unread notification, however many times a
            # retrying workflow announces it.
            collapse_key=f"artifact:{artifact_id}",
            priority="high",
        )

    def notify_artifact_ready(
        self,
        *,
        artifact_id: uuid.UUID,
        title: str,
        task_id: uuid.UUID | None = None,
        targets: Sequence[RegistrationView] | None = None,
    ) -> NotificationResult:
        """Deliver the readiness notification to every live registration."""
        registrations = list(targets if targets is not None else self.live_registrations())
        # The message shown in the result is the default-locale one; each
        # registration gets its own locale's copy.
        result_message = self.build_artifact_ready_message(
            artifact_id=artifact_id, title=title, task_id=task_id
        )
        outcomes: list[DeliveryOutcome] = []
        for registration in registrations:
            message = self.build_artifact_ready_message(
                artifact_id=artifact_id,
                title=title,
                task_id=task_id,
                locale=registration.locale,
            )
            outcomes.append(self._deliver(registration, message))
        logger.info(
            "artifact_ready_notified",
            artifact_id=str(artifact_id),
            targets=len(registrations),
            delivered=sum(1 for o in outcomes if o.ok),
        )
        return NotificationResult(message=result_message, outcomes=outcomes)

    def _deliver(
        self, registration: RegistrationView, message: PushMessage
    ) -> DeliveryOutcome:
        try:
            provider = self.provider(registration.provider)
            delivery = provider.deliver(
                registration_key=str(registration.registration_id), message=message
            )
        except MobileError as exc:
            if exc.error_class == MobileErrorClass.PUSH_TOKEN_INVALID:
                # A dead token is not a retry candidate; retire the target so we
                # stop announcing into the void.
                self.unregister(
                    registration_id=registration.registration_id,
                    status=PUSH_STATUS_INVALID,
                    reason="provider_reported_token_invalid",
                )
            logger.warning(
                "push_delivery_failed",
                registration_id=str(registration.registration_id),
                provider=registration.provider,
                error_class=str(exc.error_class),
            )
            return DeliveryOutcome(
                registration_id=registration.registration_id,
                provider=registration.provider,
                ok=False,
                error_class=str(exc.error_class),
                detail=exc.message,
            )
        self._touch_delivery(registration.registration_id)
        return DeliveryOutcome(
            registration_id=registration.registration_id,
            provider=registration.provider,
            ok=True,
            detail=delivery.detail,
        )

    def _touch_delivery(self, registration_id: uuid.UUID) -> None:
        with self._sessions() as db:
            row = db.get(PushRegistration, registration_id)
            if row is not None:
                row.last_delivery_at = self._clock()
                db.commit()

    # ------------------------------------------------------------- inbox read

    def deliveries_for_session(
        self, session_id: uuid.UUID, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Notifications recorded for this session's registrations.

        Only a provider with a readable delivery log answers here — in practice
        the fake. A real transport hands the message to Apple or Google and
        there is nothing left to poll, which is precisely why this endpoint is
        the *stand-in* for an OS notification and not a second delivery channel.
        """
        registrations = self.list_registrations(session_id=session_id)
        out: list[dict[str, Any]] = []
        for registration in registrations:
            provider = self._providers.get(registration.provider)
            reader = getattr(provider, "deliveries_for", None)
            if reader is None:
                continue
            deliveries: list[PushDelivery] = reader(str(registration.registration_id))
            for delivery in deliveries:
                payload = delivery.to_dict()
                payload["registration_id"] = str(registration.registration_id)
                out.append(payload)
        out.sort(key=lambda item: item["delivered_at"], reverse=True)
        return out[:limit]


__all__ = [
    "DeliveryOutcome",
    "MobileService",
    "NotificationResult",
    "RegistrationView",
]
