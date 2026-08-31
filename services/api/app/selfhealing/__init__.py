"""Self-healing engineering subsystem (M6).

Incident ingest + fingerprint dedup, release records, the CodingBackend seam
(deterministic first, Claude Agent SDK/CLI as an inert-until-configured
skeleton) and the incident -> reproduce -> regression test -> patch -> review
-> staging -> promote pipeline (EVOLUTION_ENGINE_SPEC §4/§5/§7).

The Recovery Supervisor itself lives OUTSIDE this service on purpose
(services/recovery-supervisor, stdlib-only): recovery must survive a broken
main application release. This package is only the coding/records side.
"""
