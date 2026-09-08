"""M9 unit tests: the hard gate is actually APPLIED.

The point of this file is the thing every previous milestone deferred. Each
module of the API gets at least one representative endpoint asserted to refuse
an unauthenticated call, so "we added an auth layer" is a claim the suite can
falsify rather than a claim the changelog makes.

Three separate properties are proven:

1. **Applied** — a representative endpoint per module returns 401 with no token
   (and the same 401 for a garbage token and a revoked token).
2. **Fail closed** — with NO owner credential bootstrapped, or with the
   identity subsystem missing entirely, everything refuses. There is no default
   credential and no "auth not configured, allow everything" path.
3. **Complete** — a route-table sweep asserts that the ONLY unauthenticated
   endpoints are the four deliberate ones, so a future endpoint added without a
   dependency fails this test instead of shipping open.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute, _IncludedRouter
from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute

from app.config import Settings
from app.identity.dependencies import (
    optional_owner_session,
    require_owner_session,
    require_scope,
)
from app.identity.root import InMemoryCredentialRoot
from app.identity.runtime import IdentityRuntime
from app.main import create_app
from tests.identity_support import bearer, make_identity_engine

DEVICE_ID = uuid.uuid4()
MEMORY_ID = uuid.uuid4()
DRAFT_ID = uuid.uuid4()
PROPOSAL_ID = uuid.uuid4()

#: One representative endpoint per module — mutating/sensitive where the module
#: has one. (module, method, path)
PROTECTED_ENDPOINTS = [
    ("memory", "post", "/v1/memory/observe"),
    ("memory-forget", "delete", f"/v1/memory/{MEMORY_ID}"),
    ("voice", "post", "/v1/voice/speaker/enroll"),
    ("voice-preferences", "patch", "/v1/voice/preferences"),
    # M12: the realtime session surface mints provider credentials and relays
    # tool calls under the owner session; every verb must be protected.
    ("voice-realtime-create", "post", "/v1/voice/realtime/sessions"),
    ("voice-realtime-tool-call", "post", f"/v1/voice/realtime/sessions/{uuid.uuid4()}/tool-calls"),
    ("voice-realtime-events", "post", f"/v1/voice/realtime/sessions/{uuid.uuid4()}/events"),
    ("voice-realtime-attach", "post", f"/v1/voice/realtime/sessions/{uuid.uuid4()}/attach"),
    ("voice-realtime-close", "post", f"/v1/voice/realtime/sessions/{uuid.uuid4()}/close"),
    ("voice-realtime-state", "get", f"/v1/voice/realtime/sessions/{uuid.uuid4()}"),
    ("voice-realtime-providers", "get", "/v1/voice/realtime/providers"),
    ("narration", "post", "/v1/narration/sessions"),
    ("narration-pronunciation", "put", "/v1/narration/pronunciation"),
    ("tasks", "post", "/v1/tasks"),
    ("artifacts", "get", "/v1/artifacts"),
    # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §4): the factory creates real artifacts
    # from the owner's spoken structured data -- never one unauthenticated call away.
    ("artifacts-factory", "post", "/v1/artifacts/factory"),
    # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §4): fetching + opening a render on the
    # owner's machine is a device mutation — never one unauthenticated call away.
    ("artifacts-open", "post", f"/v1/artifacts/{uuid.uuid4()}/open"),
    # M23 (docs/M23_APP_FACTORY_SPEC.md §6): starting, stopping or testing a project is a
    # process on the owner's machine — never one unauthenticated call away; the listing
    # names the owner's own projects and their ports.
    ("apps", "get", "/v1/apps"),
    ("apps-run", "post", f"/v1/apps/{uuid.uuid4()}/run"),
    ("apps-stop", "post", f"/v1/apps/{uuid.uuid4()}/stop"),
    ("apps-test", "post", f"/v1/apps/{uuid.uuid4()}/test"),
    ("broker-enrollment-token", "post", "/v1/devices/enrollment-tokens"),
    ("broker-command", "post", f"/v1/devices/{DEVICE_ID}/commands"),
    ("broker-revoke", "post", f"/v1/devices/{DEVICE_ID}/revoke"),
    ("selfhealing-ingest", "post", "/v1/selfhealing/incidents/ingest"),
    ("selfhealing-pipeline", "post", "/v1/selfhealing/pipeline/run"),
    ("evolution-gaps", "post", "/v1/evolution/gaps"),
    ("evolution-resolve", "post", f"/v1/evolution/gaps/{uuid.uuid4()}/resolve"),
    ("security-enroll-asset", "post", "/v1/security/assets"),
    ("security-assessment", "post", "/v1/security/assessments"),
    # M21 (docs/M21_MAIL_CALENDAR_SPEC.md §3): the Cockpit's approval pair — an EXTERNAL
    # MUTATION must never be one unauthenticated HTTP call away.
    ("mail-drafts-pending", "get", "/v1/mail/drafts/pending"),
    ("mail-drafts-confirm", "post", f"/v1/mail/drafts/{DRAFT_ID}/confirm"),
    ("mail-drafts-discard", "post", f"/v1/mail/drafts/{DRAFT_ID}/discard"),
    ("calendar-proposals-pending", "get", "/v1/calendar/proposals/pending"),
    ("calendar-proposals-confirm", "post", f"/v1/calendar/proposals/{PROPOSAL_ID}/confirm"),
    ("calendar-proposals-discard", "post", f"/v1/calendar/proposals/{PROPOSAL_ID}/discard"),
    ("identity", "get", "/v1/identity/sessions"),
]

#: The complete, deliberate list of unauthenticated surfaces (ADR-0027).
EXPECTED_OPEN = {
    ("GET", "/v1/system/health"),
    ("POST", "/v1/identity/bootstrap"),
    ("POST", "/v1/identity/sessions"),
    ("POST", "/v1/devices/enroll"),
    # M18.3 (spec §3.7, DEVICE_PROTOCOL.md §6h, ADR-0071): the greeting audio the Session
    # Companion fetches for `desktop.play_audio` carries its own authority - a 256-bit,
    # single-use, five-minute token in the path - because the device holds no owner
    # session and must never be handed one; the companion additionally checks the
    # origin and the sha256 the command named. Deliberately open, like enrollment.
    ("GET", "/v1/alarms/audio/{token}"),
    # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §4, ADR-0085 addendum 5): the device's
    # file.fetch (DEVICE_PROTOCOL.md §6k step 6) carries no owner token, cookie or header
    # of its own -- a single-use, ten-minute, 256-bit token minted by
    # app.artifacts.open_service (app.artifacts.render_fetch_store) is the authority
    # instead, naming exactly one (artifact, format, content_hash). Unknown, expired,
    # already-redeemed and content-hash-mismatched tokens are all the same bare 404 with
    # no body -- the artifact id is never named. Deliberately open, like the greeting
    # audio route above, which this mirrors.
    ("GET", "/v1/artifacts/renders/fetch/{token}"),
    # M18.4 gap 1 (ADR-0081 addendum 3): the device handoff between the two colours. The
    # release script calls these from INSIDE the draining container, which holds no owner
    # session and must not need one to finish a release; the routes are loopback-only
    # (the same peer test as the identity bootstrap - tests/unit/test_broker_drain.py
    # proves a tailnet peer gets 403). Deliberately not owner-gated.
    ("POST", "/v1/devices/drain"),
    ("POST", "/v1/devices/undrain"),
}


def build(*, bootstrap: bool = True, with_identity: bool = True):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    if with_identity:
        runtime = IdentityRuntime(
            settings, engine=make_identity_engine(), root=InMemoryCredentialRoot()
        )
        app.state.identity = runtime
        if bootstrap:
            runtime.service.bootstrap()
    else:
        app.state.identity = None
    return TestClient(app), getattr(app.state, "identity", None), app


@pytest.fixture()
def client():
    client, runtime, app = build()
    yield client, runtime, app
    client.close()


# ------------------------------------------------------------------- applied


def _call(test_client, method: str, path: str, **kwargs):
    """GET/DELETE take no body in httpx's TestClient shims."""
    if method in {"get", "delete"}:
        return getattr(test_client, method)(path, **kwargs)
    return getattr(test_client, method)(path, json={}, **kwargs)


@pytest.mark.parametrize(("module", "method", "path"), PROTECTED_ENDPOINTS)
def test_endpoint_refuses_without_a_token(client, module: str, method: str, path: str) -> None:
    test_client, _, _ = client
    response = _call(test_client, method, path)
    assert response.status_code == 401, f"{module}: {method.upper()} {path} was not protected"
    assert response.json() == {"detail": "unauthorized"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(("module", "method", "path"), PROTECTED_ENDPOINTS)
def test_endpoint_refuses_a_garbage_token(client, module: str, method: str, path: str) -> None:
    test_client, _, _ = client
    headers = bearer("pagentos_st_" + "z" * 43)
    assert _call(test_client, method, path, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Basic abc", "Bearer not-a-token", "bearer pagentos_ok_" + "x" * 43],
)
def test_malformed_authorization_headers_are_all_the_same_401(client, header: str) -> None:
    test_client, _, _ = client
    response = test_client.post("/v1/memory/observe", json={}, headers={"Authorization": header})
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_revoked_session_stops_working_immediately(client) -> None:
    test_client, runtime, _ = client
    issued = runtime.service.issue_session(client_kind="cli", label="doomed")
    assert test_client.get("/v1/identity/sessions", headers=bearer(issued.token)).status_code == 200
    runtime.service.revoke_session(issued.context.session_id)
    assert test_client.get("/v1/identity/sessions", headers=bearer(issued.token)).status_code == 401


# --------------------------------------------------------------- fail closed


@pytest.mark.parametrize(("module", "method", "path"), PROTECTED_ENDPOINTS)
def test_nothing_is_reachable_before_bootstrap(module: str, method: str, path: str) -> None:
    """No owner credential => no access. There is no default credential."""
    test_client, _, _ = build(bootstrap=False)
    try:
        headers = bearer("pagentos_st_" + "a" * 43)
        assert _call(test_client, method, path, headers=headers).status_code == 401
        assert _call(test_client, method, path).status_code == 401
    finally:
        test_client.close()


def test_missing_identity_subsystem_refuses_rather_than_opens() -> None:
    test_client, _, _ = build(with_identity=False)
    try:
        assert test_client.post("/v1/memory/observe", json={}).status_code == 401
    finally:
        test_client.close()


def test_health_stays_reachable_when_nothing_is_bootstrapped(monkeypatch) -> None:
    async def fake_checks(settings):
        return {"db": {"status": "ok", "latency_ms": 0.0}}

    monkeypatch.setattr("app.main.run_health_checks", fake_checks)
    monkeypatch.setattr(
        "app.artifacts.runtime.ArtifactRuntime.health_check",
        lambda self: {"status": "ok", "latency_ms": 0.0},
    )
    test_client, _, _ = build(bootstrap=False)
    try:
        with test_client as live:
            response = live.get("/v1/system/health")
        assert response.status_code == 200
        # ...and it does not announce whether an owner credential exists.
        assert "bootstrapped" not in repr(response.json())
    finally:
        test_client.close()


# ------------------------------------------------------------------ complete


def _walk(routes):
    for route in routes:
        if isinstance(route, _IncludedRouter):
            yield from _walk(route.original_router.routes)
        elif isinstance(route, APIRoute | WebSocketRoute):
            yield route
        elif hasattr(route, "routes"):
            yield from _walk(route.routes)


def _depends_on(dependant, target, depth: int = 0) -> bool:
    if depth > 5:
        return False
    return any(
        d.call is target or _depends_on(d, target, depth + 1) for d in dependant.dependencies
    )


def test_only_the_four_deliberate_endpoints_are_unauthenticated() -> None:
    """A new endpoint without the dependency fails HERE, not in production."""
    _, _, app = build()
    open_endpoints = set()
    total = 0
    for route in _walk(app.routes):
        if not isinstance(route, APIRoute):
            continue
        protected = _depends_on(route.dependant, require_owner_session)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            total += 1
            if not protected:
                open_endpoints.add((method, route.path))
    assert total > 70, "route table looks truncated; the sweep would prove nothing"
    assert open_endpoints == EXPECTED_OPEN


def test_the_device_websocket_is_the_only_unauthenticated_socket() -> None:
    _, _, app = build()
    sockets = {r.path for r in _walk(app.routes) if isinstance(r, WebSocketRoute)}
    # It carries its own ECDSA device authentication (DEVICE_PROTOCOL §3-5).
    assert sockets == {"/v1/devices/connect"}


# -------------------------------------------------------------------- scopes


NARRATION_READ = Depends(require_scope("narration.read"))
OPTIONAL_SESSION = Depends(optional_owner_session)


def _scoped_app(runtime: IdentityRuntime) -> FastAPI:
    app = FastAPI()
    app.state.identity = runtime

    @app.get("/needs-scope")
    async def needs_scope(_=NARRATION_READ) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/optional")
    async def optional(session=OPTIONAL_SESSION) -> dict[str, bool]:
        return {"authenticated": session is not None}

    return app


def test_unrestricted_session_satisfies_every_scope(client) -> None:
    _, runtime, _ = client
    issued = runtime.service.issue_session(client_kind="cli")
    with TestClient(_scoped_app(runtime)) as scoped:
        assert scoped.get("/needs-scope", headers=bearer(issued.token)).status_code == 200


def test_scoped_session_is_403_outside_its_scopes(client) -> None:
    _, runtime, _ = client
    allowed = runtime.service.issue_session(client_kind="mobile", scopes=["narration.read"])
    denied = runtime.service.issue_session(client_kind="mobile", scopes=["memory.read"])
    with TestClient(_scoped_app(runtime)) as scoped:
        assert scoped.get("/needs-scope", headers=bearer(allowed.token)).status_code == 200
        refused = scoped.get("/needs-scope", headers=bearer(denied.token))
        assert refused.status_code == 403
        assert refused.json() == {"detail": "forbidden"}
    # The 403 is audited with its precise reason, unlike what the caller sees.
    reasons = [e["reason"] for e in runtime.service.list_events(action="rejected")]
    assert "scope_missing:narration.read" in reasons


def test_optional_session_never_raises(client) -> None:
    _, runtime, _ = client
    issued = runtime.service.issue_session(client_kind="cli")
    with TestClient(_scoped_app(runtime)) as scoped:
        assert scoped.get("/optional").json() == {"authenticated": False}
        assert scoped.get("/optional", headers=bearer("garbage")).json() == {
            "authenticated": False
        }
        assert scoped.get("/optional", headers=bearer(issued.token)).json() == {
            "authenticated": True
        }


# ------------------------- M9 security review #2: scopes narrow, structurally


OWNER_SESSION = Depends(require_owner_session)


def _mixed_app(runtime: IdentityRuntime) -> FastAPI:
    """One route asking for unrestricted owner authority, one declaring a scope."""
    app = FastAPI()
    app.state.identity = runtime

    @app.get("/full-authority")
    async def full_authority(_=OWNER_SESSION) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/needs-scope")
    async def needs_scope(_=NARRATION_READ) -> dict[str, bool]:
        return {"ok": True}

    return app


def test_a_scoped_session_cannot_reach_a_full_authority_route(client) -> None:
    """"Scopes narrow, never elevate" must hold by construction.

    Before this rule, a scoped session sailed through every route that gated on
    bare require_owner_session - i.e. issuing a narrowed credential silently
    granted full owner authority everywhere, because forgetting `require_scope`
    on one route was enough. A scoped session must reach ONLY routes that
    explicitly declare a scope.
    """
    _, runtime, _ = client
    service = runtime.service
    full = service.issue_session(client_kind="mobile", label="full")
    narrow = service.issue_session(
        client_kind="mobile", label="narrow", scopes=["narration.read"]
    )

    with TestClient(_mixed_app(runtime)) as mixed:
        assert mixed.get("/full-authority", headers=bearer(full.token)).status_code == 200
        # Refused with 403, not 401: re-authenticating would not help, the
        # credential is deliberately narrower than this route demands.
        refused = mixed.get("/full-authority", headers=bearer(narrow.token))
        assert refused.status_code == 403
        assert refused.json() == {"detail": "forbidden"}

    reasons = [e["reason"] for e in service.list_events(action="rejected")]
    assert "scope_missing:<unrestricted>" in reasons


def test_require_scope_still_accepts_the_scoped_session_it_gates(client) -> None:
    """The narrowing rule must not lock a scoped session out of its own scope.

    `require_owner_session` refusing scoped sessions is only safe if the scoped
    route keeps working - otherwise scopes would be unusable rather than narrow.
    """
    _, runtime, _ = client
    service = runtime.service
    full = service.issue_session(client_kind="cli", label="full")
    narrow = service.issue_session(
        client_kind="mobile", label="narrow", scopes=["narration.read"]
    )

    with TestClient(_mixed_app(runtime)) as mixed:
        assert mixed.get("/needs-scope", headers=bearer(narrow.token)).status_code == 200
        assert mixed.get("/needs-scope", headers=bearer(full.token)).status_code == 200
