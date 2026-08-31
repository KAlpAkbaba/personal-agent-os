# M0 Foundation Task

## Objective

Create the smallest complete local cloud-core foundation with deterministic test gates.

## Suggested repository target

```text
apps/
  web/
services/
  api/
  orchestrator/
  device-broker/
  memory/
  artifacts/
  voice/
  narration/
  evolution/
devices/
  windows-agent/
packages/
  schemas/
  protocol/
  sdk/
infra/
  docker/
  opentofu/
tests/
  unit/
  integration/
  e2e/
evals/
  voice/
  agent/
  browser/
```

## Foundation services

- API health
- web shell
- PostgreSQL + pgvector
- Redis
- MinIO
- Temporal local/dev
- telemetry skeleton

No real browser automation or Windows action required until M1/M2.
