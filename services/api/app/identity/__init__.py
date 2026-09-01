"""Owner identity and API authentication (M9, ADR-0027).

One human owner (constitution §2): no accounts, no roles, no RBAC, no signup,
no password database. The whole layer is three moving parts:

1. an **owner credential** minted once by a bootstrap owner action and kept
   outside the database (the "identity root" of constitution §6), so a lost
   credential can be recovered on the host without the API that it protects;
2. **opaque bearer sessions** exchanged for that credential, stored only as
   SHA-256 hashes, bound to a client kind and optionally to an enrolled
   device, with an absolute TTL and an idle timeout;
3. an append-only **session_events** audit of every issuance, refresh,
   revocation, expiry and rejection — with a reason, never a token.

Fail closed: with no owner credential bootstrapped, every protected endpoint
refuses. There is no default credential.
"""
