# Security Model — Single Owner

## 1. Threat model priorities

Protect against:

- stolen device/session;
- leaked API secrets;
- prompt injection from websites/documents;
- malicious dependency/module;
- accidental destructive action;
- replay/deepfake voice impersonation;
- self-evolution breaking the system;
- scope drift during security testing.

## 2. Owner identity

> Implemented in M9 (ADR-0027): a one-time bootstrap mints the owner
> credential; it is exchanged for opaque bearer sessions stored only as SHA-256
> hashes, scoped by client kind, optionally bound to an enrolled device, with an
> absolute TTL and idle timeout, and an append-only `session_events` audit.
> `require_owner_session` protects every mutating/sensitive endpoint; only
> `GET /v1/system/health` and the ECDSA-authenticated device WebSocket handshake
> are open. No owner credential bootstrapped means everything protected is
> refused — fail closed, no default credential. Revoking a device revokes its
> sessions; a panic control revokes all sessions; credential recovery runs on
> the host, not through the API.

One human owner. No user role hierarchy.

Identity confidence combines:

- authenticated app/session;
- enrolled trusted device;
- Tailscale/private network identity signal;
- passkey where applicable;
- speaker verification where voice is used.

Voice is not a standalone password.

## 3. Device enrollment

Every device has:

- `device_id`;
- asymmetric keypair;
- capabilities;
- owner enrollment timestamp;
- revocation status;
- environment/tags.

Cloud commands include:

- command ID;
- target device;
- expiry;
- nonce/idempotency key;
- requested capability;
- signature/authentication context.

## 4. Windows privilege split

Interactive companion should normally run as the owner user.

Machine-level privileged operations go through a narrowly defined service broker. Do not run the entire LLM/browser/UI agent permanently as LocalSystem.

## 5. Secrets

- never commit;
- redact from logs;
- scope per provider/service;
- rotate when compromised;
- local developer secrets stored outside repo;
- production secrets owner-controlled and least-exposed to workers.

Evolution sandboxes receive only secrets required for the test.

M5 memory rule: the Memory Write Policy refuses to store secrets, credentials,
API keys or tokens as learned memory content (pattern guard + audit). Memory
audit events never contain memory content after a forget, and forgetting
hard-deletes the row plus all version/evidence/vector representations. The
Evolution Engine may read memory but must never silently alter explicit owner
preferences (explicit rows are only mutable by owner-actor operations) or the
recovery/security roots.

M7 evolution rules (ADR-0025 + delta):

- Generated skills are **deny-by-default** for network, filesystem, device and
  secret access; every grant is declared in the versioned capability manifest
  and recorded in the audit trail.
- Generated code never receives production secrets by default and builds/runs
  only inside an isolated workspace with capability-scoped access and enforced
  timeout/CPU/memory/disk/network budgets, retry limits and a recursion depth
  limit (no infinite agent→agent capability creation).
- Supply chain: no blind package installation; dependency name, version and
  source are recorded and pinned, scanned, and install scripts may not silently
  expand privileges.
- The self-modification restriction on recovery/security roots constrains the
  **Evolution Engine's authority over its own foundations**. It does not
  restrict owner-authorized operational capability: the owner policy subsystem
  may grant powerful tools to explicitly authorized devices/assets, and
  Evolution must never need to weaken or rewrite the security root to enable
  that.

## 6. Prompt injection defense

Treat websites/documents/email content as untrusted data.

Untrusted content cannot redefine:

- owner scope;
- authorized asset registry;
- secret-access policy;
- deployment/recovery policy.

Browser research and tool execution should maintain provenance labels for instructions originating from external content.

## 7. Authorized Asset Registry

Example:

```yaml
asset_id: lab-web-01
kind: host
locator: 10.20.30.40
authorization: owner_or_company_authorized
environment: lab
allowed_security_testing:
  configuration_audit: true
  vulnerability_scan: true
  controlled_validation: true
  remediation: true
constraints:
  max_disruption: low
```

For CIDR scopes, registry entry records the exact range.

## 8. Security testing policy

The system should not ask repetitive questions for operations already covered by the enrolled scope. The enforcement question is:

> Is this target/action within the owner's stored authorization scope?

If yes, execute according to the stored disruption/remediation policy.

If no, do not silently broaden the target. Ask for a one-time enrollment/scope update.

## 9. Audit

Append security-relevant events:

- device enrollment/revocation;
- owner identity recovery;
- secret rotation;
- security test job start/end;
- scope changes;
- release promotion/rollback;
- evolution-generated module promotion.

Audit is not a reason to require manual approval for each action.

## 10. Kill/recovery controls

Owner needs a simple way to:

- pause autonomous execution;
- revoke a device;
- disable Evolution Engine;
- roll back release;
- rotate provider credentials.

These controls should be accessible but not routinely required.
