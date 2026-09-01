"""Owner recovery for a lost or suspect credential — runs on the HOST.

    uv run python -m app.identity.recover --status
    uv run python -m app.identity.recover --rotate
    uv run python -m app.identity.recover --revoke-all

Why this is not an API endpoint: the thing being recovered is the credential
that authenticates every API call. A "forgot my credential" endpoint would be,
by construction, an unauthenticated way to mint owner authority — the exact
hole this milestone exists to close. The authorization here is *filesystem
access to the identity root on the owner's own machine*, which the owner has
and an attacker on the network does not (constitution §6: the identity root
stays independently usable when the application is broken).

`--rotate` mints a new owner credential, prints it once, and revokes every
existing session by default, on the assumption that a credential you had to
recover may have leaked. `--keep-sessions` opts out.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from app.config import Settings, get_settings
from app.identity.runtime import IdentityRuntime

_BANNER = (
    "Store this credential now. It is shown once, it is not recoverable, and "
    "anyone holding it can mint owner sessions."
)


def _build_runtime(settings: Settings) -> IdentityRuntime:
    return IdentityRuntime(settings)


def _status(runtime: IdentityRuntime) -> dict[str, Any]:
    record = runtime.root.load() if runtime.root.exists() else None
    payload: dict[str, Any] = {
        "action": "status",
        "root": runtime.root.describe(),
        "bootstrapped": record is not None,
        "session_ttl_s": runtime.settings.session_ttl_s,
        "session_idle_timeout_s": runtime.settings.session_idle_timeout_s,
    }
    if record is not None:
        payload["created_at"] = record.created_at.isoformat()
        payload["rotated_at"] = record.rotated_at.isoformat() if record.rotated_at else None
        payload["rotations"] = record.rotations
    try:
        payload["active_sessions"] = runtime.service.count_active_sessions()
    except Exception as exc:  # noqa: BLE001 - status must work with the DB down
        payload["active_sessions"] = None
        payload["sessions_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def _rotate(runtime: IdentityRuntime, *, keep_sessions: bool) -> dict[str, Any]:
    credential = runtime.service.bootstrap(force=True, trace_id="identity-recover")
    revoked = 0
    if not keep_sessions:
        revoked = runtime.service.revoke_all(
            reason="owner_credential_rotated", trace_id="identity-recover"
        )
    return {
        "action": "rotate",
        "owner_credential": credential,
        "sessions_revoked": revoked,
        "root": runtime.root.describe(),
        "note": _BANNER,
    }


def _revoke_all(runtime: IdentityRuntime) -> dict[str, Any]:
    revoked = runtime.service.revoke_all(reason="host_revoke_all", trace_id="identity-recover")
    return {"action": "revoke-all", "sessions_revoked": revoked}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.identity.recover",
        description="Owner identity recovery (host-side; requires filesystem access).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="report identity-root state")
    group.add_argument(
        "--rotate",
        action="store_true",
        help="mint a NEW owner credential (prints it once) and revoke all sessions",
    )
    group.add_argument(
        "--revoke-all", action="store_true", help="revoke every session, keep the credential"
    )
    parser.add_argument(
        "--keep-sessions",
        action="store_true",
        help="with --rotate: leave existing sessions active",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def _render(payload: dict[str, Any], *, as_json: bool) -> str:
    if as_json:
        return json.dumps(payload, indent=2, sort_keys=True)
    lines = []
    for key, value in payload.items():
        if key == "owner_credential":
            lines.append("")
            lines.append(f"OWNER CREDENTIAL: {value}")
            lines.append("")
            continue
        lines.append(f"{key}: {json.dumps(value) if isinstance(value, dict) else value}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, runtime: IdentityRuntime | None = None) -> int:
    args = build_parser().parse_args(argv)
    active = runtime or _build_runtime(get_settings())

    if args.rotate:
        payload = _rotate(active, keep_sessions=args.keep_sessions)
    elif args.revoke_all:
        payload = _revoke_all(active)
    else:
        payload = _status(active)

    print(_render(payload, as_json=args.json))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
