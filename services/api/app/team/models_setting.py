"""The model policy's setting: a model per role and the fallback switch (ADR-0214 addendum 7).

One document in the team store::

    {"roles": {"lead": m, "researcher": m, "integrator": m, "worker": m, "inspector": m},
     "fallback": bool, "updated_at": "<UTC Z>"}

Every ``m`` is one of the three ids of :data:`CHAIN`. The chain is strongest first: that order
IS the fallback chain and the meaning of "weaker". The inspector never runs on a weaker model
than the worker (owner, 2026-10-01), so a setting that says so is refused.

Pure functions, no store and no clock: ``scripts/lib/TeamQueue.ps1`` (``Read-TeamModelSetting``)
holds the same rules for the cycle, with the same codes, and a test reads its lists against
these.
"""

from __future__ import annotations

from typing import Any

CHAIN: tuple[str, ...] = ("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5")
ROLES: tuple[str, ...] = ("lead", "researcher", "integrator", "worker", "inspector")
KEYS: tuple[str, ...] = ("roles", "fallback", "updated_at")
#: When nothing is stored: lead and inspector on the strongest, the rest one below.
DEFAULT_ROLES: dict[str, str] = {
    "lead": "claude-fable-5-1",
    "researcher": "claude-opus-5-5",
    "integrator": "claude-opus-5-5",
    "worker": "claude-opus-5-5",
    "inspector": "claude-fable-5-1",
}
DEFAULT_FALLBACK = True

Problem = tuple[str, str]  # (code, what is wrong)


def is_model(value: Any) -> bool:
    return isinstance(value, str) and value in CHAIN


def is_weaker(a: str, b: str) -> bool:
    """Whether model ``a`` is weaker than model ``b``: further down the chain."""
    return CHAIN.index(a) > CHAIN.index(b)


def defaults(updated_at: str) -> dict[str, Any]:
    return {"roles": dict(DEFAULT_ROLES), "fallback": DEFAULT_FALLBACK, "updated_at": updated_at}


def problems(document: Any, *, strict: bool) -> list[Problem]:
    """Every way ``document`` breaks the contract, each with its code. Empty when none.

    ``strict`` is the PUT: all five roles and ``fallback`` must be there. Without it (what a
    store holds - ``team/models.json`` was written by hand before the route existed) a missing
    role or a missing ``fallback`` is not a problem: :func:`effective` fills it.
    """
    if not isinstance(document, dict):
        return [("invalid", "the setting is not an object")]
    found: list[Problem] = [
        ("unknown_key", f"'{key}' is not a key of the setting")
        for key in document
        if key not in KEYS
    ]
    roles = dict(DEFAULT_ROLES)
    named: set[str] = set()
    shaped = True
    raw = document.get("roles")
    if isinstance(raw, dict):
        for role, model in raw.items():
            if role not in ROLES:
                found.append(("unknown_role", f"'{role}' is not a role"))
            elif not is_model(model):
                found.append(
                    (
                        "unknown_model",
                        f"{model!r} is not a model (role {role}): one of {', '.join(CHAIN)}",
                    )
                )
            else:
                roles[role] = model
                named.add(role)
    elif raw is not None or strict:
        shaped = False
        found.append(("invalid", "'roles' is missing or not an object"))
    fallback = document.get("fallback")
    if not isinstance(fallback, bool) and (fallback is not None or strict):
        found.append(("invalid", "'fallback' is true or false"))
    if "updated_at" in document and not isinstance(document["updated_at"], str):
        found.append(("invalid", "'updated_at' is a string"))
    if strict and shaped:
        found += [
            ("missing_role", f"the role '{role}' is missing")
            for role in ROLES
            if role not in named and role not in raw
        ]
    # Judged on what would be in force: a stored document's missing role is the default's.
    judged = named >= {"inspector", "worker"} or not strict
    if judged and is_weaker(roles["inspector"], roles["worker"]):
        found.append(
            (
                "inspector_weaker_than_worker",
                f"the inspector's model ({roles['inspector']}) is weaker than the worker's "
                f"({roles['worker']})",
            )
        )
    return found


def effective(stored: Any, updated_at: str) -> dict[str, Any]:
    """The setting in force for what a store holds: the document with what it lacks filled in,
    and the defaults when nothing is stored or what is stored breaks the contract - a model
    that is not one of the three never leaves here, nor an inspector below the worker."""
    if stored is None or problems(stored, strict=False):
        return defaults(updated_at)
    fallback = stored.get("fallback")
    return {
        "roles": {**DEFAULT_ROLES, **(stored.get("roles") or {})},
        "fallback": DEFAULT_FALLBACK if fallback is None else fallback,
        "updated_at": stored.get("updated_at") or updated_at,
    }
