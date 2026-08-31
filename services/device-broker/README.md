# device-broker

Implemented in M1 as a logical module **inside** `services/api`:

- code: `services/api/app/broker/` (REST routes, WS session protocol, presence,
  durable command dispatch, expiry sweeper, audit)
- schema: `services/api/alembic/versions/20260831_0002_device_broker.py`
- contract: `packages/protocol/DEVICE_PROTOCOL.md` +
  `packages/schemas/device-protocol.schema.json`

The broker runs in-process with the API (`uv run uvicorn app.main:app`) until
scale demands a separate service. This directory stays as the future home if
the broker is ever split out; no code lives here today.
