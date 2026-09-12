"""B05 req 246/663: device trust is DERIVED, never asserted by the caller.

``POST /v1/voice/speaker/verify`` took ``device_trusted`` from the request body. The field's
own comment said what that meant - "an UNTRUSTED, client-asserted hint" - and the classifier
was careful to cap an untrusted device at UNCERTAIN, so the rule was written down and then
handed to the one party it was written to constrain. Anything holding an owner token could
send ``device_trusted: true`` and move the acceptance band in its own favour.

Trust is answerable from state this server already holds: an owner session is bound to a
device at issue time (``OwnerSession.device_id``, set when the session belongs to an enrolled
device), and that device row says whether it is still enrolled. So:

    trusted  <=>  the session is bound to a device that exists and is not revoked

A browser tab, a curl call or any session with no device binding is untrusted - which is the
honest answer, not a penalty. The whole product invariant this protects is that **voice is
never the sole secret**: on an untrusted device a perfect voice match is still only
UNCERTAIN, and it is exactly that outcome the caller could previously talk its way out of.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.identity.service import SessionContext

logger = get_logger("app.voice.device_trust")


def device_is_trusted(db: Session, session: SessionContext | None) -> bool:
    """True when this authenticated session speaks from an enrolled, live device."""
    from app.broker.models import DEVICE_STATUS_REVOKED, Device

    if session is None or session.device_id is None:
        return False
    device = db.get(Device, session.device_id)
    if device is None:
        # A session bound to a device row that no longer exists: not trusted, and worth
        # saying out loud - it means a device was deleted without its sessions.
        logger.warning(
            "voice_device_trust_dangling_binding",
            session_id=str(session.session_id),
            device_id=str(session.device_id),
        )
        return False
    return device.status != DEVICE_STATUS_REVOKED


__all__ = ["device_is_trusted"]
