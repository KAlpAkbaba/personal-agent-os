"""The Evolution Engine authority kernel — the production boundary, in code.

CLAUDE.md self-development rule and constitution §6 both say the same thing:
the system may build its own next version, but it may never be the thing that
puts that version into production. This module is where that sentence stops
being a prompt and becomes a type.

The boundary
------------

The engine runs with a **LAB** authority. A lab authority may:

    read source, write a lab workspace, run lab tests, read evidence,
    benchmark a candidate, security-review a candidate, critique/explain it,
    propose a candidate, and mark it SHADOW_READY.

A lab authority may never:

    deploy, sign a release, write unrestricted production data, read
    production secrets, or modify the policy kernel.

Those five are the ``PRODUCTION`` grants and they are **not grantable to a lab
authority at all** — not "denied at call time", not "checked by a reviewer":
:class:`Authority` refuses to construct with a production grant unless it was
minted from an :class:`OwnerCapability`, and an ``OwnerCapability`` can only be
derived from a *verified, unscoped owner session* (the object
``app.identity.dependencies.require_owner_session`` returns). Lab code is never
handed a session context, so there is no expression it can evaluate that yields
a production authority.

Knowledge is not authority
--------------------------

Nothing here restricts *reading*. The engine may inspect, diff, benchmark and
explain any LAB/SHADOW module in full detail; ``READ_SOURCE`` and
``READ_EVIDENCE`` are lab grants. What it cannot do is *run that module in
production*. Being able to explain a change and being able to ship it are
deliberately different capabilities.

Three independent mechanisms
----------------------------

1. **Grant algebra** — :data:`LAB_GRANTS` and :data:`PRODUCTION_GRANTS` are
   disjoint; :class:`LabAuthority` has no parameter through which a production
   grant could be requested, and refuses one if asked.
2. **Capability construction** — production actions go through
   :func:`guard_production_action`, which requires a production-scoped
   authority; that authority requires an owner-session capability the lab
   never receives.
3. **Import guard** — :func:`scan_evolution_package` fails the build if any
   module under ``app/evolution`` imports a deployment, release-mutation or
   secret-root module. Authority checks only help if the code cannot simply
   reach around them and call the deployer directly.

An honest limit: Python has no memory-safe object capability model, so a
sufficiently determined caller *inside this process* could reach module
privates. The claim made here is narrower and testable: there is no exported
API, and no import path, by which evolution code obtains production authority —
and both facts are asserted by tests, so regressions fail the suite rather than
being discovered in production.

Root policies
-------------

:data:`ROOT_POLICIES` are the invariants evolution may never weaken. They are
not configuration: :func:`apply_policy_change` refuses any change touching one,
*including* from a production authority, and :func:`verify_root_policies`
re-derives their digest so a silent edit is detected rather than trusted.
"""

from __future__ import annotations

import ast
import contextlib
import contextvars
import functools
import hashlib
import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, NoReturn

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.logging import get_logger

logger = get_logger("app.evolution.authority")


class AuthorityError(EvolutionError):
    """A caller attempted something its authority does not cover.

    A subclass of :class:`~app.evolution.errors.EvolutionError` with the
    ``permission_denied`` class, so the REST surface already maps it to 403
    without a new branch.
    """

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(EvolutionErrorClass.PERMISSION_DENIED, message, dict(details))


# --------------------------------------------------------------------- grants


class Scope(StrEnum):
    """Who a given authority speaks for."""

    #: The Evolution Engine's own workspace. Everything it may do is here.
    LAB = "lab"
    #: The running product. Only reachable from a verified owner session.
    PRODUCTION = "production"


class Grant(StrEnum):
    """One named permission. Lab grants first, production grants after."""

    # ------------------------------------------------------------ lab grants
    #: Read the product's own source to understand and critique it.
    READ_SOURCE = "read_source"
    #: Create/modify files inside an isolated lab workspace (never the product).
    WRITE_LAB_WORKSPACE = "write_lab_workspace"
    #: Execute tests inside the sandbox, against the candidate only.
    RUN_LAB_TESTS = "run_lab_tests"
    #: Read ledger events, incidents and lessons as evidence for an opportunity.
    READ_EVIDENCE = "read_evidence"
    #: Measure the candidate against the incumbent on a fixed case set.
    BENCHMARK_CANDIDATE = "benchmark_candidate"
    #: Run the independent security review over the candidate.
    SECURITY_REVIEW_CANDIDATE = "security_review_candidate"
    #: Produce an owner-facing explanation/critique of a candidate.
    EXPLAIN_CANDIDATE = "explain_candidate"
    #: Record a new opportunity/candidate in the backlog.
    PROPOSE_CANDIDATE = "propose_candidate"
    #: Declare a packaged candidate ready for shadow — the last lab step.
    MARK_SHADOW_READY = "mark_shadow_ready"

    # ----------------------------------------------------- production grants
    #: Put a release into production.
    DEPLOY = "deploy"
    #: Sign a release so the recovery root will accept it.
    SIGN_RELEASE = "sign_release"
    #: Write production data outside the engine's own tables.
    WRITE_PRODUCTION_DB = "write_production_db"
    #: Read unrestricted production secrets.
    READ_PRODUCTION_SECRETS = "read_production_secrets"
    #: Change the security/approval policy kernel.
    MODIFY_POLICY_KERNEL = "modify_policy_kernel"


LAB_GRANTS: Final[frozenset[Grant]] = frozenset(
    {
        Grant.READ_SOURCE,
        Grant.WRITE_LAB_WORKSPACE,
        Grant.RUN_LAB_TESTS,
        Grant.READ_EVIDENCE,
        Grant.BENCHMARK_CANDIDATE,
        Grant.SECURITY_REVIEW_CANDIDATE,
        Grant.EXPLAIN_CANDIDATE,
        Grant.PROPOSE_CANDIDATE,
        Grant.MARK_SHADOW_READY,
    }
)

PRODUCTION_GRANTS: Final[frozenset[Grant]] = frozenset(
    {
        Grant.DEPLOY,
        Grant.SIGN_RELEASE,
        Grant.WRITE_PRODUCTION_DB,
        Grant.READ_PRODUCTION_SECRETS,
        Grant.MODIFY_POLICY_KERNEL,
    }
)

# Structural, not aspirational: if a future edit adds a grant to both sets, or
# forgets to classify a new one, the module fails to import.
assert not (LAB_GRANTS & PRODUCTION_GRANTS), "lab and production grants must be disjoint"
assert (LAB_GRANTS | PRODUCTION_GRANTS) == set(Grant), "every Grant must be classified"


#: Named production actions and the grant each one needs. Enumerated so a test
#: can assert that a lab caller is refused for *every* production action rather
#: than for the handful someone remembered to check.
PRODUCTION_ACTIONS: Final[dict[str, Grant]] = {
    "deploy_release": Grant.DEPLOY,
    "promote_production": Grant.DEPLOY,
    "rollback_production": Grant.DEPLOY,
    "sign_release": Grant.SIGN_RELEASE,
    "publish_release_manifest": Grant.SIGN_RELEASE,
    "write_production_database": Grant.WRITE_PRODUCTION_DB,
    "mutate_release_record": Grant.WRITE_PRODUCTION_DB,
    "read_production_secret": Grant.READ_PRODUCTION_SECRETS,
    "rotate_owner_credential": Grant.READ_PRODUCTION_SECRETS,
    "modify_policy_kernel": Grant.MODIFY_POLICY_KERNEL,
    "weaken_root_policy": Grant.MODIFY_POLICY_KERNEL,
}


# ------------------------------------------------------- capability minting

# Module-private mint tokens. They are never exported, never accepted as an
# argument from outside this module, and never serialized.
_CAPABILITY_MINT: Final[object] = object()
_AUTHORITY_MINT: Final[object] = object()


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OwnerCapability:
    """Proof that a *verified, unscoped owner session* is behind this call.

    The only way to obtain one is :func:`mint_owner_capability`, whose argument
    is the ``SessionContext`` that ``require_owner_session`` returns after a
    real bearer-token verification. Lab code is constructed without a session
    context and never sees a request, so it has nothing to pass.

    A *scoped* session is refused for the same reason
    ``require_owner_session`` refuses one: scopes narrow a client, they never
    elevate one, and production authority is unrestricted owner authority.
    """

    __slots__ = ("client_kind", "issued_at", "session_id", "_mint")

    def __init__(self, *, mint: object, session_id: str, client_kind: str) -> None:
        if mint is not _CAPABILITY_MINT:
            raise AuthorityError(
                "OwnerCapability cannot be constructed directly; it is derived "
                "from a verified owner session by mint_owner_capability()",
            )
        self._mint = mint
        self.session_id = session_id
        self.client_kind = client_kind
        self.issued_at = _utcnow()

    def __repr__(self) -> str:  # never leak the session id into logs verbatim
        return f"<OwnerCapability client_kind={self.client_kind!r}>"


def mint_owner_capability(session: Any) -> OwnerCapability:
    """Derive an owner capability from an authenticated owner session.

    ``session`` must look like ``app.identity.service.SessionContext``: it must
    carry a ``session_id`` and a ``scopes`` collection, and the scopes must be
    empty (unrestricted owner authority). Anything else — including a plain
    dict a caller assembled to look the part — is refused.
    """
    session_id = getattr(session, "session_id", None)
    if session_id is None or not hasattr(session, "scopes"):
        raise AuthorityError(
            "an owner capability requires a verified owner session context",
            reason="not_a_session_context",
        )
    if getattr(session, "scopes", None):
        raise AuthorityError(
            "a scoped session is a narrowed credential and never carries production authority",
            reason="scoped_session",
        )
    return OwnerCapability(
        mint=_CAPABILITY_MINT,
        session_id=str(session_id),
        client_kind=str(getattr(session, "client_kind", "unknown")),
    )


# ------------------------------------------------------------------ authority


@dataclass(frozen=True, slots=True)
class Authority:
    """An immutable set of grants held by one named subject.

    Constructing an ``Authority`` that carries any production grant requires
    the module-private mint token, which only :class:`ProductionAuthority`
    holds. A direct ``Authority(scope=Scope.PRODUCTION, ...)`` from anywhere
    else raises :class:`AuthorityError` before the object exists.
    """

    subject: str
    scope: Scope
    grants: frozenset[Grant]
    issued_at: datetime = field(default_factory=_utcnow, compare=False)
    #: Never exported, never compared, never serialized.
    mint: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        unknown = {g for g in self.grants if g not in LAB_GRANTS | PRODUCTION_GRANTS}
        if unknown:
            raise AuthorityError("unknown grant requested", grants=sorted(str(g) for g in unknown))
        production = sorted(str(g) for g in self.grants & PRODUCTION_GRANTS)
        if self.scope is Scope.LAB and production:
            raise AuthorityError(
                "a lab authority can never hold a production grant",
                subject=self.subject,
                refused_grants=production,
            )
        if self.scope is Scope.PRODUCTION and self.mint is not _AUTHORITY_MINT:
            raise AuthorityError(
                "a production authority must be minted from an owner-session "
                "capability; it cannot be constructed by the lab",
                subject=self.subject,
            )

    # ------------------------------------------------------------- predicates

    @property
    def is_lab(self) -> bool:
        return self.scope is Scope.LAB

    @property
    def is_production(self) -> bool:
        return self.scope is Scope.PRODUCTION

    def can(self, grant: Grant) -> bool:
        return grant in self.grants

    def require(self, grant: Grant, *, action: str | None = None) -> None:
        """Raise :class:`AuthorityError` unless this authority holds ``grant``."""
        if grant in self.grants:
            return
        raise AuthorityError(
            f"{self.scope} authority {self.subject!r} lacks the {grant} grant",
            subject=self.subject,
            scope=str(self.scope),
            required_grant=str(grant),
            action=action,
            production_grant=grant in PRODUCTION_GRANTS,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "scope": str(self.scope),
            "grants": sorted(str(g) for g in self.grants),
            "issued_at": self.issued_at.isoformat(),
        }


class LabAuthority:
    """Factory that can only ever produce lab grants.

    There is no parameter here through which a production grant could be
    smuggled: :meth:`issue` refuses a request that is not a subset of
    :data:`LAB_GRANTS`, and even if that check were removed the
    ``Authority.__post_init__`` invariant would still refuse the object.
    """

    #: The default set the engine runs with: everything the lab is allowed to do.
    DEFAULT_GRANTS: Final[frozenset[Grant]] = LAB_GRANTS

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:  # pragma: no cover - guard
        raise AuthorityError("LabAuthority is a factory, not an instance; use .issue()")

    @staticmethod
    def issue(subject: str, grants: Iterable[Grant] | None = None) -> Authority:
        requested = frozenset(grants) if grants is not None else LabAuthority.DEFAULT_GRANTS
        refused = sorted(str(g) for g in requested - LAB_GRANTS)
        if refused:
            raise AuthorityError(
                "LabAuthority can only issue lab grants",
                subject=subject,
                refused_grants=refused,
            )
        return Authority(subject=subject, scope=Scope.LAB, grants=requested)

    @staticmethod
    def read_only(subject: str) -> Authority:
        """Inspect-and-explain only: knowledge without any write capability."""
        return LabAuthority.issue(
            subject,
            {Grant.READ_SOURCE, Grant.READ_EVIDENCE, Grant.EXPLAIN_CANDIDATE},
        )


class ProductionAuthority:
    """Factory for production authority. Requires an :class:`OwnerCapability`.

    This is the whole boundary in one place: the argument type is the thing the
    lab cannot obtain. There is no ``ProductionAuthority.issue(subject)``
    overload, no environment variable, and no configuration flag that produces
    one without a verified owner session.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> NoReturn:  # pragma: no cover - guard
        raise AuthorityError("ProductionAuthority is a factory; use .for_owner()")

    @staticmethod
    def for_owner(capability: OwnerCapability, grants: Iterable[Grant] | None = None) -> Authority:
        if not isinstance(capability, OwnerCapability):
            raise AuthorityError(
                "production authority requires an owner-session capability",
                reason="not_an_owner_capability",
            )
        requested = frozenset(grants) if grants is not None else PRODUCTION_GRANTS
        unknown = sorted(str(g) for g in requested - (LAB_GRANTS | PRODUCTION_GRANTS))
        if unknown:
            raise AuthorityError("unknown grant requested", refused_grants=unknown)
        return Authority(
            subject=f"owner_session:{capability.session_id}",
            scope=Scope.PRODUCTION,
            grants=requested,
            mint=_AUTHORITY_MINT,
        )


# ------------------------------------------------------------------- guards

_ambient: contextvars.ContextVar[Authority | None] = contextvars.ContextVar(
    "pagentos_evolution_authority", default=None
)


@contextlib.contextmanager
def authority_scope(authority: Authority) -> Iterator[Authority]:
    """Bind ``authority`` for the duration of the block.

    Used where threading an explicit argument through every helper would be
    noise; :func:`requires` prefers an explicit argument and falls back here.
    """
    token = _ambient.set(authority)
    try:
        yield authority
    finally:
        _ambient.reset(token)


def current_authority() -> Authority | None:
    return _ambient.get()


def _resolve_authority(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Authority:
    candidate = kwargs.get("authority")
    if isinstance(candidate, Authority):
        return candidate
    for arg in args:
        if isinstance(arg, Authority):
            return arg
        bound = getattr(arg, "authority", None)
        if isinstance(bound, Authority):
            return bound
    ambient = _ambient.get()
    if ambient is not None:
        return ambient
    raise AuthorityError(
        "no authority in scope; an unauthenticated caller has no grants",
        reason="no_authority",
    )


def requires(grant: Grant) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Guard a function or method with a required grant.

    The authority is taken from (in order) an ``authority=`` keyword, an
    ``Authority`` positional argument, the ``.authority`` attribute of the
    receiver, or the ambient :func:`authority_scope`. With none of those the
    call is refused — absence of an authority is never treated as permission.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            authority = _resolve_authority(args, kwargs)
            authority.require(grant, action=fn.__qualname__)
            return fn(*args, **kwargs)

        wrapper.required_grant = grant  # type: ignore[attr-defined]
        return wrapper

    return decorate


def guard_production_action(action: str, authority: Authority | None) -> None:
    """The single choke point for every production action.

    Refuses when the action is unknown (fail closed on a typo rather than
    letting an unlisted action through), when there is no authority, when the
    authority is lab-scoped, or when the required grant is absent.
    """
    grant = PRODUCTION_ACTIONS.get(action)
    if grant is None:
        raise AuthorityError(
            "unknown production action; refusing rather than assuming it is safe",
            action=action,
        )
    if authority is None:
        raise AuthorityError("production action attempted with no authority", action=action)
    if authority.scope is not Scope.PRODUCTION:
        raise AuthorityError(
            "a lab-scoped caller may never perform a production action; the "
            "Evolution Engine proposes, the owner disposes",
            action=action,
            subject=authority.subject,
            scope=str(authority.scope),
            required_grant=str(grant),
        )
    authority.require(grant, action=action)


# -------------------------------------------------------------- root policies


@dataclass(frozen=True, slots=True)
class RootPolicy:
    """An invariant the Evolution Engine may never weaken, only satisfy."""

    policy_id: str
    title: str
    statement: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "policy_id": self.policy_id,
            "title": self.title,
            "statement": self.statement,
            "source": self.source,
        }


ROOT_POLICIES: Final[tuple[RootPolicy, ...]] = (
    RootPolicy(
        policy_id="owner_identity",
        title="Owner identity",
        statement=(
            "There is exactly one human authority. Evolution may never create, "
            "elevate, impersonate or widen an identity, nor alter the owner "
            "identity root."
        ),
        source="PROJECT_CONSTITUTION.md §2, §6",
    ),
    RootPolicy(
        policy_id="approval_boundary",
        title="Approval boundary",
        statement=(
            "Promotion past SHADOW_READY requires an explicit owner action. No "
            "system or lab actor may approve on the owner's behalf, and no "
            "amount of passing evidence substitutes for that approval."
        ),
        source="PROJECT_CONSTITUTION.md §5, CLAUDE.md self-development rule",
    ),
    RootPolicy(
        policy_id="deployment_authority",
        title="Deployment authority",
        statement=(
            "The Evolution Engine may package and qualify a candidate but may "
            "never deploy it, sign it, or activate it. Deployment authority "
            "lives outside the engine and is reachable only from a verified "
            "owner session."
        ),
        source="CLAUDE.md self-development rule",
    ),
    RootPolicy(
        policy_id="audit_guarantees",
        title="Audit guarantees",
        statement=(
            "Every lifecycle transition is recorded with its evidence before it "
            "takes effect. Evolution may add audit records; it may never "
            "delete, rewrite or disable them."
        ),
        source="M16_ACTIVITY_LEDGER_SPEC.md §1.1 (append-only)",
    ),
    RootPolicy(
        policy_id="secret_boundaries",
        title="Secret boundaries",
        statement=(
            "The engine has no access to unrestricted production secrets or to "
            "the credential/secret root, and must never widen its own access."
        ),
        source="PROJECT_CONSTITUTION.md §6, CLAUDE.md 'Never commit secrets'",
    ),
    RootPolicy(
        policy_id="sandbox_boundary",
        title="Sandbox boundary",
        statement=(
            "Generated and candidate code lives only in an isolated workspace "
            "that can never overlap the recovery supervisor, the product source, "
            "the migrations, config or state trees."
        ),
        source="EVOLUTION_ENGINE_SPEC §13, app/evolution/sandbox.py",
    ),
    RootPolicy(
        policy_id="rollback_guarantees",
        title="Rollback guarantees",
        statement=(
            "A last-known-good release pointer and the recovery supervisor must "
            "survive any release the engine produces. Evolution may never "
            "remove, bypass or degrade the rollback path."
        ),
        source="PROJECT_CONSTITUTION.md §6",
    ),
    RootPolicy(
        policy_id="security_policy_kernel",
        title="Security policy kernel",
        statement=(
            "Authorization scope, deny-by-default permission grants and the "
            "authorized-asset boundary are set by the owner. Evolution may "
            "propose a change to them but may never apply one."
        ),
        source="PROJECT_CONSTITUTION.md §8, SECURITY_MODEL M7",
    ),
)

ROOT_POLICY_IDS: Final[frozenset[str]] = frozenset(p.policy_id for p in ROOT_POLICIES)


def _digest(policies: Iterable[RootPolicy]) -> str:
    canonical = json.dumps([p.to_dict() for p in policies], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


#: Pinned at import. `verify_root_policies` re-derives it, so a policy edited
#: at runtime (or an in-place mutation of the tuple) is detected, not trusted.
ROOT_POLICY_DIGEST: Final[str] = _digest(ROOT_POLICIES)


def root_policies() -> tuple[dict[str, str], ...]:
    return tuple(p.to_dict() for p in ROOT_POLICIES)


def verify_root_policies() -> None:
    """Raise if the root policy set no longer matches its pinned digest."""
    current = _digest(ROOT_POLICIES)
    if current != ROOT_POLICY_DIGEST:
        raise AuthorityError(
            "the root policy set has been modified since import",
            expected_digest=ROOT_POLICY_DIGEST,
            actual_digest=current,
        )


def refuse_root_policy_mutation(policy_id: str, **context: Any) -> NoReturn:
    """Always raises. There is no argument combination that permits a change."""
    logger.error("root_policy_mutation_refused", policy_id=policy_id)
    raise AuthorityError(
        f"root policy {policy_id!r} is immutable to the Evolution Engine; "
        "propose a change for the owner instead of applying one",
        policy_id=policy_id,
        **context,
    )


def apply_policy_change(
    changes: Mapping[str, Any], authority: Authority | None = None
) -> dict[str, Any]:
    """Apply non-root policy changes; refuse anything touching a root policy.

    A production authority is *not* a way around this. The root policies bound
    what the engine may change, and the engine is the only caller of this
    function — so the refusal is unconditional rather than authority-dependent.
    """
    verify_root_policies()
    for policy_id in changes:
        if policy_id in ROOT_POLICY_IDS:
            refuse_root_policy_mutation(
                policy_id,
                subject=None if authority is None else authority.subject,
                scope=None if authority is None else str(authority.scope),
            )
    return dict(changes)


# ------------------------------------------------------------- import guard

#: Modules that carry deployment, release-mutation or secret authority. No
#: module under ``app/evolution`` may import any of them, at module level or
#: inside a function: importing the deployer is the same thing as being able to
#: deploy, and no authority check helps once the callable is in hand.
FORBIDDEN_MODULES: Final[tuple[str, ...]] = (
    # deployment / promotion / rollback
    "app.selfhealing.pipeline",
    "app.selfhealing.runtime",
    "app.selfhealing.routes",
    # security remediation applies changes to the running system
    "app.security.remediation",
    "app.security.runtime",
    "app.security.routes",
)

#: ``app.identity`` is the owner-identity and secret root. Exactly one module in
#: it may be imported: the FastAPI dependency that *consumes* an already
#: verified owner session. Everything else there mints or stores credentials.
IDENTITY_PREFIX: Final[str] = "app.identity"
ALLOWED_IDENTITY_MODULES: Final[frozenset[str]] = frozenset({"app.identity.dependencies"})

#: Modules that mix a pure helper with a production-authority surface. Only the
#: listed symbols may be imported — reading an incident is evidence, mutating a
#: release is authority, and they happen to live in neighbouring files.
RESTRICTED_MODULE_SYMBOLS: Final[dict[str, frozenset[str]]] = {
    "app.selfhealing.service": frozenset(
        {"compute_manifest_digest", "build_manifest", "compute_fingerprint"}
    ),
    "app.selfhealing.models": frozenset({"Incident", "INCIDENT_STATUSES", "JSONColumn"}),
}

EVOLUTION_PACKAGE_DIR: Final[Path] = Path(__file__).resolve().parent


def _imported_modules(tree: ast.AST) -> list[tuple[str, str | None, int]]:
    """Every (module, imported_name, lineno) pair in a parsed file.

    Function-level and ``TYPE_CHECKING`` imports are included: an import that
    only happens on one code path is still an import.
    """
    found: list[tuple[str, str | None, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, None, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — never leaves the package
                continue
            module = node.module or ""
            for alias in node.names:
                found.append((module, alias.name, node.lineno))
    return found


def scan_module_for_forbidden_imports(path: Path) -> list[str]:
    """Return human-readable violations for one source file (empty = clean)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - a broken file fails elsewhere
        return [f"{path.name}: could not parse ({exc})"]

    violations: list[str] = []
    for module, name, lineno in _imported_modules(tree):
        # `from app.selfhealing import pipeline` targets app.selfhealing.pipeline;
        # `from app.selfhealing.service import x` targets app.selfhealing.service.
        # Only a `from <package> import <submodule>` needs the qualified form,
        # so the candidate set is (module) plus (module.name) when name is set.
        candidates = [module] + ([f"{module}.{name}"] if name and module else [])
        for candidate in candidates:
            if any(
                candidate == forbidden or candidate.startswith(f"{forbidden}.")
                for forbidden in FORBIDDEN_MODULES
            ):
                violations.append(
                    f"{path.name}:{lineno} imports {candidate} "
                    "(deployment/release/secret authority)"
                )
                break

        # Identity: the effective module is the one actually being reached. A
        # `from app.identity.dependencies import x` reaches the dependencies
        # module; a `from app.identity import root` reaches app.identity.root.
        identity_module = module
        if module == IDENTITY_PREFIX and name:
            identity_module = f"{IDENTITY_PREFIX}.{name}"
        if (
            identity_module == IDENTITY_PREFIX or identity_module.startswith(f"{IDENTITY_PREFIX}.")
        ) and identity_module not in ALLOWED_IDENTITY_MODULES:
            violations.append(
                f"{path.name}:{lineno} imports {identity_module} (owner identity / secret root)"
            )

        allowed = RESTRICTED_MODULE_SYMBOLS.get(module)
        if allowed is not None and name is not None and name not in allowed:
            violations.append(
                f"{path.name}:{lineno} imports {module}.{name}; only "
                f"{sorted(allowed)} are evidence-only symbols"
            )
        if module in RESTRICTED_MODULE_SYMBOLS and name is None:
            violations.append(
                f"{path.name}:{lineno} imports the whole of {module}; only "
                f"{sorted(RESTRICTED_MODULE_SYMBOLS[module])} may be imported"
            )
    return violations


def scan_evolution_package(package_dir: Path | None = None) -> list[str]:
    """Scan every ``app/evolution/*.py`` file. Empty list means the boundary holds."""
    root = package_dir or EVOLUTION_PACKAGE_DIR
    violations: list[str] = []
    for source in sorted(root.glob("*.py")):
        violations.extend(scan_module_for_forbidden_imports(source))
    return violations


__all__ = [
    "ALLOWED_IDENTITY_MODULES",
    "EVOLUTION_PACKAGE_DIR",
    "FORBIDDEN_MODULES",
    "LAB_GRANTS",
    "PRODUCTION_ACTIONS",
    "PRODUCTION_GRANTS",
    "RESTRICTED_MODULE_SYMBOLS",
    "ROOT_POLICIES",
    "ROOT_POLICY_DIGEST",
    "ROOT_POLICY_IDS",
    "Authority",
    "AuthorityError",
    "Grant",
    "LabAuthority",
    "OwnerCapability",
    "ProductionAuthority",
    "RootPolicy",
    "Scope",
    "apply_policy_change",
    "authority_scope",
    "current_authority",
    "guard_production_action",
    "mint_owner_capability",
    "refuse_root_policy_mutation",
    "requires",
    "root_policies",
    "scan_evolution_package",
    "scan_module_for_forbidden_imports",
    "verify_root_policies",
]
