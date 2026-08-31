# `security_target` — synthetic configuration-audit fixture (M8)

This directory is the ONLY thing the M8 security agent's tests assess. It is a
local, offline stand-in for an owner-enrolled lab host's configuration bundle.
Nothing here is deployed, served or connected to; the assessment runner only
opens these files and matches regexes against their lines.

## The values in here are FAKE

Every credential-looking string below is synthetic and deliberately
non-functional. They exist so the redaction pass (`app/security/redaction.py`)
and the `secret_in_config` check have something to find:

- Every credential VALUE is an obvious placeholder string, deliberately shaped
  so it does not match the repository's own secret-content scan
  (`scripts/quality-gate.ps1`). The `credential_assignment` detector fires on
  the credential-meaning KEY name, so the fixture stays meaningful without
  planting a scannable token literal in the repo.
- Every password/token/private-key body is an obvious placeholder string.

They are committed on purpose. The acceptance requirement is that a fixture
target containing a fake credential produces a finding that NAMES the exposure
(file, line, which credential pattern) **without reproducing the value** in the
finding, the assessment result, the security artifact or the audit trail. The
test `test_security_redaction.py` asserts exactly that.

Do not put a real credential in this directory.

## What each file is for

| file | checks it triggers |
|---|---|
| `app.env` | `secret_in_config` (several patterns), `debug_mode_enabled` |
| `sshd_config` | `ssh_root_login_permitted`, `ssh_password_authentication_enabled` |
| `service.yaml` | `tls_disabled`, `permissive_cors_origin`, `weak_password_policy` |
| `backup.conf` | `backup_encryption_disabled` |
| `web.conf` | `directory_listing_enabled`, `obsolete_tls_version_allowed` |
| `clean.ini` | nothing — proves the runner does not invent findings |
