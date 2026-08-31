"""Speaker-profile encryption (VOICE_SPEC §10: store encrypted derived profiles,
never raw audio).

A Fernet key is derived deterministically from the configured secret so the same
secret always decrypts a previously stored profile. The derived owner embedding
is serialized to JSON and encrypted before it ever reaches the object store; the
DB only keeps the object key (``embedding_ref``), so neither raw audio nor a
plaintext voiceprint is persisted.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.voice.errors import VoiceError, VoiceErrorClass


def derive_fernet_key(secret: str) -> bytes:
    """Deterministic 32-byte urlsafe-base64 Fernet key from an arbitrary secret."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class ProfileCipher:
    def __init__(self, secret: str) -> None:
        self._fernet = Fernet(derive_fernet_key(secret))

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def decrypt(self, token: bytes) -> bytes:
        try:
            return self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise VoiceError(
                VoiceErrorClass.VALIDATION_ERROR,
                "stored speaker profile could not be decrypted (wrong secret or tampered)",
            ) from exc


__all__ = ["ProfileCipher", "derive_fernet_key"]
