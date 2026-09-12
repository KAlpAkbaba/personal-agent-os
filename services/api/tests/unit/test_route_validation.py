"""Request-schema validation guards (M4 security review findings #1, #3, #4).

These exercise the pydantic request models directly — pure, no DB — so the
rejections are proven without standing up the app/lifespan.
"""

import pytest
from pydantic import ValidationError

from app.narration.routes import CommandRequest, UpdateCursorRequest
from app.voice.routes import VerifyRequest


def test_narration_cursor_rejects_unknown_state() -> None:
    with pytest.raises(ValidationError):
        UpdateCursorRequest(state="BOGUS")


def test_narration_cursor_accepts_known_state() -> None:
    assert UpdateCursorRequest(state="READING").state == "READING"
    assert UpdateCursorRequest(state=None).state is None


def test_narration_command_utterance_is_length_bounded() -> None:
    with pytest.raises(ValidationError):
        CommandRequest(utterance="x" * 2001)
    assert CommandRequest(utterance="oku").utterance == "oku"


def test_verify_request_rejects_caller_threshold_override() -> None:
    # The per-call accept/reject threshold override was removed so a caller
    # cannot widen the band (M4 review #1); extra fields are forbidden.
    with pytest.raises(ValidationError):
        VerifyRequest(probe_embedding=[1.0, 0.0], owner_accept=0.1)
    with pytest.raises(ValidationError):
        VerifyRequest(probe_embedding=[1.0, 0.0], not_owner_max=0.99)


def test_verify_request_bounds_embedding_dims() -> None:
    with pytest.raises(ValidationError):
        VerifyRequest(probe_embedding=[0.0] * 5000)


def test_a_caller_cannot_declare_its_own_device_trusted() -> None:
    """B05 req 246/663. This assertion used to read `.device_trusted is False`, pinning a
    field the CALLER set - the one party the rule about device trust exists to constrain.
    The field is gone and the model forbids extras, so a client still sending it is told
    so (422) rather than quietly having it ignored; trust now comes from the authenticated
    session's device binding (app.voice.device_trust)."""
    with pytest.raises(ValidationError):
        VerifyRequest(probe_embedding=[1.0, 0.0], device_trusted=True)
