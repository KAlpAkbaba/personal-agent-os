"""Authorized Security Agent (M8).

Defensive, owner-authorized security only. Every action in this package is
gated on the Authorized Asset Registry (SECURITY_MODEL §7/§8, ADR-0026):

    Is this target/action within the owner's stored authorization scope?

`scope.py` is the single enforcement point that answers it, and it is
fail-safe: unknown, ambiguous, expired, suspended or revoked resolves to
REFUSED, and every refusal is written to the append-only
`authorization_events` table so an out-of-scope target can never be silently
added to scope.

The assessment runner performs read-only CONFIGURATION AUDIT of files under
config roots the owner recorded on the asset. There is no network scanning, no
exploitation, and no third-party tooling anywhere in this package.
"""
