"""B11 req 372: Web Push (RFC 8030 delivery, RFC 8291 payload encryption, RFC 8292
VAPID identification) — the push rung the notification ladder reserved a slot for.

Submodules:

* ``ece`` — RFC 8291 ``aes128gcm`` content-coding encryption. Pure functions, no I/O.
* ``vapid`` — RFC 8292 key handling and the ``Authorization: vapid ...`` header. Pure
  functions, no I/O.
* ``models`` — ``PushSubscriptionRow`` (one browser's subscription).
* ``provider`` — the third-party boundary (CLAUDE.md: "third-party services must be
  behind provider interfaces"): ``PushProvider`` Protocol, the real ``HttpPushProvider``,
  and ``FakePushProvider`` for tests. Also the SSRF allowlist (only known push-service
  hosts are ever dialled — a subscription's ``endpoint`` is attacker-reachable: any
  script on any page the owner's browser visited can call
  ``pushManager.subscribe()`` and choose it).
* ``service`` — subscription storage + ``send_to_all``, the orchestration the push rung
  and the REST routes both call.
* ``routes`` — owner-session-gated REST surface: the VAPID public key, and
  subscribe/list/delete.

Nothing here decides that a push was SEEN by the owner. RFC 8030's 201/202 means the
push service accepted the message for delivery — a queue, not a delivery — and the
docstring on ``app.notifications.ladder.PushRung.deliver`` says exactly that.
"""

from __future__ import annotations
