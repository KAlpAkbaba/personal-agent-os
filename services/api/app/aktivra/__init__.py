"""Aktivra's 'önemli' channel (card aktivra-inbound-events; roadmap: "Aktivra's assistant tells
JARVIS, JARVIS calls him").

Aktivra is a separate project in its own repository, and nothing of the company's - no customer,
no document, no figure - enters this system. What crosses the line is "something happened" and a
short title (``packages/protocol/AKTIVRA_EVENTS.md``):

- ``auth``    Aktivra's own bearer token, compared over hashes in constant time; never the
              owner's session, and valid on no other route.
- ``routes``  POST /v1/aktivra/events (the token) and GET /v1/aktivra/status (the owner).
- ``service`` an accepted event becomes ONE notification (``aktivra.important`` rings through
              the alarm rung and the call policy; ``aktivra.info`` waits in the inbox) and a
              ledger line; the event id makes a retry the same answer, never a second ring.
- ``models``  ``aktivra_events``: the event id, its notification, kept 30 days.
"""
