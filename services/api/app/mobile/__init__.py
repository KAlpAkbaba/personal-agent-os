"""M9 native-mobile surface: push registrations, delivery, lifecycle, share.

This package holds the *server side* of the native mobile client. There is no
native app in this repository and none can be built on the owner's current
machine (no Android SDK, no Xcode, no device), so the deliverable is the
contract a native app talks to plus a headless reference client
(`clients/reference/mobile_client.py`) that exercises that contract end to end.

Everything here sits on the M9 identity layer (ADR-0027): a push registration
belongs to an owner *session*, and a session that is revoked — directly, or
because its device was revoked — can no longer be a push target.
"""

__all__: list[str] = []
