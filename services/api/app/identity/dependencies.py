"""FastAPI dependencies that enforce owner authentication.

Contract:

- `require_owner_session` — reads `Authorization: Bearer <token>`, verifies it,
  updates `last_seen_at`, and returns the `SessionContext`. Any failure raises
  401 with `{"detail": "unauthorized"}` and `WWW-Authenticate: Bearer`. The
  caller never learns which check failed; the precise reason is in
  `session_events`.
- `require_scope("x")` — same, plus the session must carry scope `x`. A session
  with no scopes has unrestricted owner authority (single-owner model: scopes
  *narrow* a client, they never elevate one). A scope failure is 403
  `{"detail": "forbidden"}`, which is a different coarse class because it is
  not fixable by re-authenticating.

"Scopes narrow, never elevate" is enforced STRUCTURALLY, not by remembering to
use the right dependency: a **scoped** session is refused by bare
`require_owner_session`, so it can only ever reach a route that explicitly
declares a scope. Today no route declares one, so a scoped session can reach
nothing — which is the safe direction. Without this rule, issuing a scoped
session would silently grant full owner authority everywhere, because a route
that forgot `require_scope` would happily accept it (M9 security review #2, the
same "built but not wired" class as M8's authorization provider).

- `optional_owner_session` — returns the context or None and never raises. For
  surfaces that must stay reachable unauthenticated (health).

Fail closed: if the identity subsystem is missing from `app.state`, or no owner
credential has been bootstrapped, every protected endpoint refuses.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.identity.errors import AUTHENTICATION_REFUSALS, Refusal
from app.identity.runtime import IdentityRuntime
from app.identity.service import SessionContext, Verdict
from app.identity.tokens import parse_bearer
from app.logging import get_logger, trace_id_var

logger = get_logger("app.identity.dependencies")

AUTH_HEADER = "Authorization"
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="unauthorized", headers=dict(_CHALLENGE))


def _forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail="forbidden")


def _runtime(request: Request) -> IdentityRuntime | None:
    return getattr(request.app.state, "identity", None)


async def _authenticate(request: Request) -> Verdict:
    runtime = _runtime(request)
    if runtime is None:
        # The subsystem is not wired: refuse rather than assume "open".
        logger.error("identity_runtime_missing", path=request.url.path)
        return Verdict(refusal=Refusal.UNAVAILABLE)
    token = parse_bearer(request.headers.get(AUTH_HEADER))
    trace_id = trace_id_var.get()
    return await asyncio.to_thread(runtime.service.verify, token, trace_id=trace_id)


async def require_owner_session(request: Request) -> SessionContext:
    """Authenticate a FULL-AUTHORITY owner session, or refuse.

    A session carrying scopes is deliberately refused here: it is a narrowed
    credential, and this dependency guards routes that ask for unrestricted
    owner authority. Letting it through would turn every route that forgot
    `require_scope` into a silent full-authority grant.
    """
    verdict = await _authenticate(request)
    if verdict.session is None:
        raise _unauthorized()
    if verdict.session.scopes:
        runtime = _runtime(request)
        if runtime is not None:
            await asyncio.to_thread(
                lambda: runtime.service.record_scope_refusal(
                    session_id=verdict.session.session_id,
                    client_kind=verdict.session.client_kind,
                    scope="<unrestricted>",
                    trace_id=trace_id_var.get(),
                )
            )
        raise _forbidden()
    request.state.owner_session = verdict.session
    return verdict.session


async def _authenticated_session(request: Request) -> SessionContext:
    """Authentication only — used by `require_scope`, which does its own
    narrowing check and must therefore accept a scoped session."""
    verdict = await _authenticate(request)
    if verdict.session is None:
        raise _unauthorized()
    request.state.owner_session = verdict.session
    return verdict.session


async def optional_owner_session(request: Request) -> SessionContext | None:
    """Best-effort authentication for endpoints that must stay open."""
    verdict = await _authenticate(request)
    if verdict.session is not None:
        request.state.owner_session = verdict.session
    return verdict.session


def require_scope(scope: str) -> Callable[..., Awaitable[SessionContext]]:
    """Dependency factory: owner session that additionally carries `scope`."""

    async def dependency(
        request: Request,
        session: Annotated[SessionContext, Depends(_authenticated_session)],
    ) -> SessionContext:
        # An unscoped session is full owner authority and passes any scope
        # check; a scoped one must carry exactly this scope.
        if not session.scopes or session.has_scope(scope):
            return session
        runtime = _runtime(request)
        if runtime is not None:
            await asyncio.to_thread(
                lambda: runtime.service.record_scope_refusal(
                    session_id=session.session_id,
                    client_kind=session.client_kind,
                    scope=scope,
                    trace_id=trace_id_var.get(),
                )
            )
        raise _forbidden()

    return dependency


def refusal_status(refusal: Refusal) -> int:
    """Coarse class for a refusal: 401 to authenticate, 403 to be told no."""
    return 401 if refusal in AUTHENTICATION_REFUSALS else 403


__all__ = [
    "AUTH_HEADER",
    "optional_owner_session",
    "refusal_status",
    "require_owner_session",
    "require_scope",
]
