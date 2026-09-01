"""Owner identity service: bootstrap, issuance, verification, revocation.

Single owner (constitution §2). There is no user table, no role, no signup and
no password: exactly one credential exists, it is minted once by an owner
action, and everything else in this module is the lifecycle of the opaque
bearer sessions exchanged for it.

Every decision this service makes appends a row to `session_events` with a
reason — issued, refreshed, revoked, expired, rejected — and never the token.
That audit is what makes "why was I logged out?" and "what used this session?"
answerable without weakening the refusals, which stay coarse to the caller.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity import tokens
from app.identity.errors import (
    AlreadyBootstrapped,
    CorruptIdentityRoot,
    InvalidOwnerCredential,
    NotBootstrapped,
    Refusal,
    Throttled,
)
from app.identity.models import (
    CLIENT_KINDS,
    EVENT_EXPIRED,
    EVENT_ISSUED,
    EVENT_REFRESHED,
    EVENT_REJECTED,
    EVENT_REVOKED,
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_EXPIRED,
    SESSION_STATUS_REVOKED,
    OwnerSession,
    SessionEvent,
)
from app.identity.root import CredentialRoot, RootRecord, utc_now
from app.logging import get_logger

logger = get_logger("app.identity.service")

SessionMaker = Callable[[], AbstractContextManager[Session]]

MAX_SCOPES = 16
MAX_SCOPE_CHARS = 64
MIN_TTL_S = 60

#: Rejection-audit key. One owner, one process: the limiter exists to bound how
#: much a scanner can write into `session_events`, not to lock the owner out.
_BEARER_KEY = "bearer"
_CREDENTIAL_KEY = "credential"


# --------------------------------------------------------------------- results


@dataclass(frozen=True)
class SessionContext:
    """Detached view of an authenticated session handed to route handlers."""

    session_id: uuid.UUID
    client_kind: str
    client_label: str
    device_id: uuid.UUID | None
    scopes: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None

    @property
    def unrestricted(self) -> bool:
        """No scope list means full owner authority (single-owner model)."""
        return not self.scopes

    def has_scope(self, scope: str) -> bool:
        return self.unrestricted or scope in self.scopes


@dataclass(frozen=True)
class IssuedSession:
    """A freshly minted session. `token` is returned exactly once, ever."""

    token: str
    context: SessionContext


@dataclass(frozen=True)
class Verdict:
    """The outcome of verifying a presented bearer token."""

    session: SessionContext | None = None
    refusal: Refusal | None = None

    @property
    def ok(self) -> bool:
        return self.session is not None


# -------------------------------------------------------------------- limiter


class AttemptLimiter:
    """Sliding-window failure counter (in-process, thread-safe).

    Honest about its scope: one API process serves one owner, so an in-process
    counter is the whole population. It is not a distributed rate limiter and
    does not pretend to be one.
    """

    def __init__(
        self,
        *,
        max_failures: int = 10,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        cutoff = now - self.window_s
        while hits and hits[0] < cutoff:
            hits.popleft()
        return hits

    def record_failure(self, key: str) -> int:
        """Record one failure; return the count inside the current window."""
        with self._lock:
            now = self._clock()
            hits = self._prune(key, now)
            hits.append(now)
            return len(hits)

    def failures(self, key: str) -> int:
        with self._lock:
            return len(self._prune(key, self._clock()))

    def throttled(self, key: str) -> bool:
        return self.failures(key) >= self.max_failures

    def retry_after_s(self, key: str) -> int:
        with self._lock:
            now = self._clock()
            hits = self._prune(key, now)
            if not hits:
                return 0
            return max(1, int(self.window_s - (now - hits[0])) + 1)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


# -------------------------------------------------------------------- service


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat stored times as UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class IdentityService:
    def __init__(
        self,
        session_factory: SessionMaker,
        root: CredentialRoot,
        *,
        ttl_s: int,
        idle_timeout_s: int,
        clock: Callable[[], datetime] = utc_now,
        limiter: AttemptLimiter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.root = root
        self.ttl_s = ttl_s
        self.idle_timeout_s = idle_timeout_s
        self._clock = clock
        self.limiter = limiter or AttemptLimiter()

    # --------------------------------------------------------------- plumbing

    def _sessions(self) -> AbstractContextManager[Session]:
        return self._session_factory()

    def _record(
        self,
        db: Session,
        action: str,
        *,
        reason: str,
        session_id: uuid.UUID | None = None,
        client_kind: str | None = None,
        detail: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> None:
        db.add(
            SessionEvent(
                action=action,
                session_id=session_id,
                client_kind=client_kind,
                reason=reason[:256],
                detail_json=detail or {},
                trace_id=trace_id,
                created_at=self._clock(),
            )
        )

    @staticmethod
    def _context(row: OwnerSession) -> SessionContext:
        scopes = tuple(str(s) for s in (row.scopes_json or []))
        return SessionContext(
            session_id=row.id,
            client_kind=row.client_kind,
            client_label=row.client_label or "",
            device_id=row.device_id,
            scopes=scopes,
            created_at=_aware(row.created_at) or utc_now(),
            expires_at=_aware(row.expires_at) or utc_now(),
            last_seen_at=_aware(row.last_seen_at),
        )

    # -------------------------------------------------------------- bootstrap

    def is_bootstrapped(self) -> bool:
        return self.root.exists()

    def _load_root(self) -> RootRecord | None:
        """Read the identity root, turning an unreadable one into a typed error.

        `FileCredentialRoot.load()` deliberately re-raises on a corrupt file
        rather than returning None. Callers need that distinction typed, not as
        an OSError/ValueError escaping to the framework as a 500 (M9 security
        review #5).
        """
        try:
            return self.root.load()
        except (OSError, ValueError) as exc:
            raise CorruptIdentityRoot(
                "the owner identity root exists but cannot be read; "
                "repair it on the host with `python -m app.identity.recover`"
            ) from exc

    def bootstrap(self, *, trace_id: str | None = None, force: bool = False) -> str:
        """Mint the FIRST owner credential. One-time owner action.

        Returns the plaintext credential exactly once; only its SHA-256 hash is
        persisted. Refuses when a credential already exists — re-minting is the
        recovery path (`app.identity.recover`), which runs on the host.
        """
        # A corrupt root raises here, and that is the point: bootstrap is the
        # one path that mints authority from nothing, so it must never mistake
        # "unreadable" for "absent" and overwrite a real owner credential.
        existing = self._load_root()
        if existing is not None and not force:
            raise AlreadyBootstrapped("an owner credential already exists")
        credential = tokens.new_owner_credential()
        now = self._clock()
        record = RootRecord(
            credential_hash=tokens.hash_token(credential),
            created_at=existing.created_at if existing else now,
            rotated_at=now if existing else None,
            rotations=(existing.rotations + 1) if existing else 0,
        )
        self.root.store(record)
        self.limiter.reset(_CREDENTIAL_KEY)
        with self._sessions() as db:
            self._record(
                db,
                EVENT_ISSUED,
                reason="owner_credential_rotated" if existing else "owner_credential_bootstrapped",
                detail={"kind": "owner_credential", "rotations": record.rotations},
                trace_id=trace_id,
            )
            db.commit()
        logger.info(
            "identity_owner_credential_minted",
            rotated=existing is not None,
            rotations=record.rotations,
        )
        return credential

    def verify_owner_credential(self, credential: str) -> bool:
        record = self._load_root()
        if record is None:
            raise NotBootstrapped("no owner credential has been bootstrapped")
        if not tokens.looks_like_owner_credential(credential):
            return False
        return tokens.hashes_equal(tokens.hash_token(credential), record.credential_hash)

    # ---------------------------------------------------------------- issuing

    @staticmethod
    def _clean_scopes(scopes: Sequence[str] | None) -> list[str]:
        if not scopes:
            return []
        if len(scopes) > MAX_SCOPES:
            raise ValueError(f"at most {MAX_SCOPES} scopes")
        cleaned: list[str] = []
        for scope in scopes:
            value = str(scope).strip()
            if not value or len(value) > MAX_SCOPE_CHARS:
                raise ValueError("scope must be 1..64 characters")
            if value not in cleaned:
                cleaned.append(value)
        return cleaned

    def issue_session(
        self,
        *,
        client_kind: str,
        label: str = "",
        device_id: uuid.UUID | None = None,
        scopes: Sequence[str] | None = None,
        ttl_s: int | None = None,
        trace_id: str | None = None,
    ) -> IssuedSession:
        """Mint a session. The caller must have already proven owner authority."""
        if client_kind not in CLIENT_KINDS:
            raise ValueError(f"unknown client kind: {client_kind!r}")
        effective_ttl = self.ttl_s if ttl_s is None else int(ttl_s)
        if effective_ttl < MIN_TTL_S or effective_ttl > self.ttl_s:
            raise ValueError(f"ttl_s must be between {MIN_TTL_S} and {self.ttl_s}")
        cleaned_scopes = self._clean_scopes(scopes)

        token = tokens.new_session_token()
        now = self._clock()
        row = OwnerSession(
            id=uuid.uuid4(),
            token_hash=tokens.hash_token(token),
            client_kind=client_kind,
            client_label=(label or "")[:128],
            device_id=device_id,
            status=SESSION_STATUS_ACTIVE,
            scopes_json=cleaned_scopes,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(seconds=effective_ttl),
        )
        with self._sessions() as db:
            db.add(row)
            db.flush()
            self._record(
                db,
                EVENT_ISSUED,
                reason="credential_exchange",
                session_id=row.id,
                client_kind=client_kind,
                detail={
                    "device_bound": device_id is not None,
                    "scopes": cleaned_scopes,
                    "ttl_s": effective_ttl,
                },
                trace_id=trace_id,
            )
            db.commit()
            context = self._context(row)
        logger.info(
            "identity_session_issued",
            session_id=str(row.id),
            client_kind=client_kind,
            device_bound=device_id is not None,
        )
        return IssuedSession(token=token, context=context)

    def exchange_credential(
        self,
        credential: str,
        *,
        client_kind: str,
        label: str = "",
        device_id: uuid.UUID | None = None,
        scopes: Sequence[str] | None = None,
        ttl_s: int | None = None,
        trace_id: str | None = None,
    ) -> IssuedSession:
        """Owner credential -> session. Throttled, audited, fails closed."""
        if self.limiter.throttled(_CREDENTIAL_KEY):
            raise Throttled(self.limiter.retry_after_s(_CREDENTIAL_KEY))
        try:
            valid = self.verify_owner_credential(credential)
        except NotBootstrapped:
            self._reject(
                Refusal.NOT_BOOTSTRAPPED,
                reason="credential_exchange_before_bootstrap",
                trace_id=trace_id,
                audit_key=_CREDENTIAL_KEY,
            )
            raise
        except CorruptIdentityRoot:
            # Fails closed like any other refusal, and is audited under its own
            # reason so the owner can tell "damaged root" from "wrong credential".
            self._reject(
                Refusal.UNAVAILABLE,
                reason="identity_root_unreadable",
                trace_id=trace_id,
                audit_key=_CREDENTIAL_KEY,
            )
            raise
        if not valid:
            self._reject(
                Refusal.UNKNOWN,
                reason="owner_credential_mismatch",
                trace_id=trace_id,
                audit_key=_CREDENTIAL_KEY,
                detail={"credential_fingerprint": tokens.fingerprint(credential)},
            )
            raise InvalidOwnerCredential("owner credential rejected")
        self.limiter.reset(_CREDENTIAL_KEY)
        return self.issue_session(
            client_kind=client_kind,
            label=label,
            device_id=device_id,
            scopes=scopes,
            ttl_s=ttl_s,
            trace_id=trace_id,
        )

    # ----------------------------------------------------------- verification

    def _reject(
        self,
        refusal: Refusal,
        *,
        reason: str,
        trace_id: str | None,
        audit_key: str,
        session_id: uuid.UUID | None = None,
        client_kind: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> Verdict:
        """Record a refusal (bounded) and return it typed."""
        count = self.limiter.record_failure(audit_key)
        payload = dict(detail or {})
        payload["refusal"] = refusal.value
        try:
            if count <= self.limiter.max_failures:
                with self._sessions() as db:
                    self._record(
                        db,
                        EVENT_REJECTED,
                        reason=reason,
                        session_id=session_id,
                        client_kind=client_kind,
                        detail=payload,
                        trace_id=trace_id,
                    )
                    db.commit()
            elif count == self.limiter.max_failures + 1:
                with self._sessions() as db:
                    self._record(
                        db,
                        EVENT_REJECTED,
                        reason="rejection_audit_suppressed",
                        detail={"window_s": self.limiter.window_s, "after": count - 1},
                        trace_id=trace_id,
                    )
                    db.commit()
        except Exception:  # noqa: BLE001 - an audit failure must not grant access
            logger.exception("identity_rejection_audit_failed")
        logger.info("identity_rejected", refusal=refusal.value, reason=reason)
        return Verdict(refusal=refusal)

    def record_scope_refusal(
        self,
        *,
        session_id: uuid.UUID,
        client_kind: str,
        scope: str,
        trace_id: str | None = None,
    ) -> None:
        """Audit an authenticated-but-out-of-scope call (403, not 401)."""
        self._reject(
            Refusal.SCOPE_MISSING,
            reason=f"scope_missing:{scope}",
            trace_id=trace_id,
            audit_key="scope",
            session_id=session_id,
            client_kind=client_kind,
        )

    def verify(self, token: str | None, *, trace_id: str | None = None) -> Verdict:
        """Authenticate a presented bearer token.

        Order matters: the not-bootstrapped check comes first so that an
        un-bootstrapped system refuses *everything* rather than falling through
        to a lookup that could, in a future refactor, find a stale row.
        """
        if not self.is_bootstrapped():
            return self._reject(
                Refusal.NOT_BOOTSTRAPPED,
                reason="no_owner_credential",
                trace_id=trace_id,
                audit_key=_BEARER_KEY,
            )
        if not token or not tokens.looks_like_session_token(token):
            return self._reject(
                Refusal.MALFORMED,
                reason="malformed_bearer",
                trace_id=trace_id,
                audit_key=_BEARER_KEY,
            )

        token_hash = tokens.hash_token(token)
        now = self._clock()
        with self._sessions() as db:
            row = db.execute(
                select(OwnerSession).where(OwnerSession.token_hash == token_hash)
            ).scalar_one_or_none()
            if row is None or not tokens.hashes_equal(row.token_hash, token_hash):
                return self._reject(
                    Refusal.UNKNOWN,
                    reason="no_such_session",
                    trace_id=trace_id,
                    audit_key=_BEARER_KEY,
                    detail={"token_fingerprint": tokens.fingerprint(token)},
                )

            if row.status == SESSION_STATUS_REVOKED:
                return self._reject(
                    Refusal.REVOKED,
                    reason=row.revoked_reason or "revoked",
                    trace_id=trace_id,
                    audit_key=_BEARER_KEY,
                    session_id=row.id,
                    client_kind=row.client_kind,
                )

            expires_at = _aware(row.expires_at)
            if row.status == SESSION_STATUS_EXPIRED or (
                expires_at is not None and now >= expires_at
            ):
                self._expire(db, row, reason="absolute_ttl", trace_id=trace_id)
                db.commit()
                return self._reject(
                    Refusal.EXPIRED,
                    reason="absolute_ttl",
                    trace_id=trace_id,
                    audit_key=_BEARER_KEY,
                    session_id=row.id,
                    client_kind=row.client_kind,
                )

            idle_since = _aware(row.last_seen_at) or _aware(row.created_at) or now
            if self.idle_timeout_s > 0 and now - idle_since > timedelta(
                seconds=self.idle_timeout_s
            ):
                self._expire(db, row, reason="idle_timeout", trace_id=trace_id)
                db.commit()
                return self._reject(
                    Refusal.IDLE_TIMEOUT,
                    reason="idle_timeout",
                    trace_id=trace_id,
                    audit_key=_BEARER_KEY,
                    session_id=row.id,
                    client_kind=row.client_kind,
                )

            row.last_seen_at = now
            db.commit()
            self.limiter.reset(_BEARER_KEY)
            return Verdict(session=self._context(row))

    def _expire(
        self, db: Session, row: OwnerSession, *, reason: str, trace_id: str | None
    ) -> None:
        if row.status == SESSION_STATUS_ACTIVE:
            row.status = SESSION_STATUS_EXPIRED
        self._record(
            db,
            EVENT_EXPIRED,
            reason=reason,
            session_id=row.id,
            client_kind=row.client_kind,
            trace_id=trace_id,
        )

    # --------------------------------------------------------------- refresh

    def refresh(self, token: str, *, trace_id: str | None = None) -> IssuedSession | Verdict:
        """Rotate the token and extend the window on the SAME session row.

        Rotation (rather than extending the existing token) means a token that
        leaked from a log, a proxy or a backup stops working as soon as the
        client refreshes, while the session identity — and therefore its device
        binding and audit history — stays stable.
        """
        verdict = self.verify(token, trace_id=trace_id)
        if not verdict.ok:
            return verdict
        assert verdict.session is not None
        new_token = tokens.new_session_token()
        now = self._clock()
        with self._sessions() as db:
            row = db.get(OwnerSession, verdict.session.session_id)
            if row is None or row.status != SESSION_STATUS_ACTIVE:  # pragma: no cover
                return Verdict(refusal=Refusal.UNKNOWN)
            row.token_hash = tokens.hash_token(new_token)
            row.last_seen_at = now
            row.expires_at = now + timedelta(seconds=self.ttl_s)
            self._record(
                db,
                EVENT_REFRESHED,
                reason="token_rotated",
                session_id=row.id,
                client_kind=row.client_kind,
                trace_id=trace_id,
            )
            db.commit()
            context = self._context(row)
        logger.info("identity_session_refreshed", session_id=str(context.session_id))
        return IssuedSession(token=new_token, context=context)

    # -------------------------------------------------------------- revoking

    def _revoke_rows(
        self,
        db: Session,
        rows: Iterator[OwnerSession] | Sequence[OwnerSession],
        *,
        reason: str,
        trace_id: str | None,
    ) -> int:
        now = self._clock()
        count = 0
        for row in rows:
            row.status = SESSION_STATUS_REVOKED
            row.revoked_at = now
            row.revoked_reason = reason[:256]
            self._record(
                db,
                EVENT_REVOKED,
                reason=reason,
                session_id=row.id,
                client_kind=row.client_kind,
                trace_id=trace_id,
            )
            count += 1
        return count

    def revoke_session(
        self, session_id: uuid.UUID, *, reason: str = "owner_revoked", trace_id: str | None = None
    ) -> bool:
        with self._sessions() as db:
            row = db.get(OwnerSession, session_id)
            if row is None or row.status == SESSION_STATUS_REVOKED:
                return False
            self._revoke_rows(db, [row], reason=reason, trace_id=trace_id)
            db.commit()
        logger.info("identity_session_revoked", session_id=str(session_id), reason=reason)
        return True

    def revoke_sessions_for_device(
        self,
        device_id: uuid.UUID,
        *,
        reason: str = "device_revoked",
        trace_id: str | None = None,
    ) -> int:
        """M9 acceptance: revoking a device invalidates every session it holds."""
        with self._sessions() as db:
            rows = (
                db.execute(
                    select(OwnerSession).where(
                        OwnerSession.device_id == device_id,
                        OwnerSession.status == SESSION_STATUS_ACTIVE,
                    )
                )
                .scalars()
                .all()
            )
            count = self._revoke_rows(db, rows, reason=reason, trace_id=trace_id)
            db.commit()
        if count:
            logger.info(
                "identity_device_sessions_revoked", device_id=str(device_id), revoked=count
            )
        return count

    def revoke_all(self, *, reason: str = "owner_panic", trace_id: str | None = None) -> int:
        """SECURITY_MODEL §10 kill control: every session, including the caller's."""
        with self._sessions() as db:
            rows = (
                db.execute(
                    select(OwnerSession).where(OwnerSession.status == SESSION_STATUS_ACTIVE)
                )
                .scalars()
                .all()
            )
            count = self._revoke_rows(db, rows, reason=reason, trace_id=trace_id)
            db.commit()
        logger.info("identity_all_sessions_revoked", revoked=count, reason=reason)
        return count

    # ---------------------------------------------------------------- sweeps

    def sweep_expired(self, *, trace_id: str | None = None) -> int:
        """Flip active-but-past-expiry sessions to `expired` and record it once."""
        now = self._clock()
        with self._sessions() as db:
            rows = (
                db.execute(
                    select(OwnerSession).where(
                        OwnerSession.status == SESSION_STATUS_ACTIVE,
                        OwnerSession.expires_at <= now,
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                self._expire(db, row, reason="swept", trace_id=trace_id)
            db.commit()
            return len(rows)

    # ----------------------------------------------------------------- reads

    def list_sessions(self, *, active_only: bool = True, limit: int = 100) -> list[SessionContext]:
        stmt = select(OwnerSession).order_by(OwnerSession.created_at.desc()).limit(limit)
        if active_only:
            stmt = stmt.where(OwnerSession.status == SESSION_STATUS_ACTIVE)
        with self._sessions() as db:
            rows = db.execute(stmt).scalars().all()
            return [self._context(row) for row in rows]

    def count_active_sessions(self) -> int:
        with self._sessions() as db:
            return len(
                db.execute(
                    select(OwnerSession.id).where(OwnerSession.status == SESSION_STATUS_ACTIVE)
                )
                .scalars()
                .all()
            )

    def list_events(
        self, *, limit: int = 50, action: str | None = None
    ) -> list[dict[str, Any]]:
        stmt = select(SessionEvent).order_by(SessionEvent.id.desc()).limit(limit)
        if action:
            stmt = stmt.where(SessionEvent.action == action)
        with self._sessions() as db:
            rows = db.execute(stmt).scalars().all()
            return [
                {
                    "id": row.id,
                    "action": row.action,
                    "session_id": str(row.session_id) if row.session_id else None,
                    "client_kind": row.client_kind,
                    "reason": row.reason,
                    "detail": row.detail_json,
                    "trace_id": row.trace_id,
                    "created_at": (_aware(row.created_at) or utc_now()).isoformat(),
                }
                for row in rows
            ]


__all__ = [
    "AttemptLimiter",
    "IdentityService",
    "IssuedSession",
    "SessionContext",
    "Verdict",
]
