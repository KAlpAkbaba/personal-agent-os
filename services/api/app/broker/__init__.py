"""Device Broker (M1).

Logical module inside services/api implementing the server side of
packages/protocol/DEVICE_PROTOCOL.md v1: device enrollment, the outbound
WebSocket session protocol (handshake / heartbeat / command dispatch),
durable command state, presence, revocation and audit.

It runs in-process with the API until scale demands a separate service
(see services/device-broker/README.md).
"""
