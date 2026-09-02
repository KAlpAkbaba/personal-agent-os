"""Realtime voice session service (M12 spec §4, §6, §7, §9).

Cloud Core's side of a ``ConversationRealtime`` session: the session record,
provider selection by capability, ephemeral credential minting THROUGH the
provider adapter, the sideband tool relay (idempotent on ``call_id``,
long-running tools with a Turkish preamble completed over the authenticated
push surface), client timing/state events for the benchmark and the audit,
continuity across clients (attach), and ``voice_*`` audit rows that carry ids
and timings only — never audio, never credentials.

Modules: ``models`` (ORM, migration 0011), ``persona`` (Turkish instructions),
``tools`` (the capability manifest exposed to a session), ``sideband`` (push
seam over the broker WebSocket), ``service`` (transactions), ``runtime``
(app.state.voice_realtime), ``routes`` (``/v1/voice/realtime``).
"""
