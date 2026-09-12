"""B05 req 678: the permanent-delete gate, per the owner's decision of 2026-09-13.

Soft delete is the default and reversible, so voice may ask for it in one step. Permanent
destruction needs a confirmation from a second channel - something clicked or typed. The
reason is the same one behind the step-up policy: an utterance is the easiest thing in this
system to produce by accident, and destruction is the one action with nothing behind it to
undo the mistake.

The delete SURFACES arrive later (file delete B34, event cancellation B46, artifact deletion
B42). The policy exists first so all three inherit one rule instead of inventing three.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.security import deletion

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _confirmation(**overrides) -> deletion.DeletionConfirmation:
    base = {
        "subject_kind": "file",
        "subject_id": "rapor.pdf",
        "channel": "web",
        "confirmed_at": NOW,
    }
    base.update(overrides)
    return deletion.DeletionConfirmation(**base)


def _permanent(confirmation, *, subject_id="rapor.pdf", now=NOW):
    return deletion.evaluate_permanent_delete(
        subject_kind="file", subject_id=subject_id, confirmation=confirmation, now=now
    )


def test_a_soft_delete_needs_nothing(kind="file") -> None:
    """Reversible by construction. Making the owner click twice to hide something they can
    unhide is the kind of friction that gets a policy switched off."""
    decision = deletion.evaluate_soft_delete(subject_kind=kind, subject_id="rapor.pdf")

    assert decision.allowed is True
    assert decision.mode == deletion.MODE_SOFT


def test_a_voice_confirmation_cannot_destroy_anything() -> None:
    """The decision, as one assertion. Voice may propose; it may not complete."""
    decision = _permanent(_confirmation(channel=deletion.CHANNEL_VOICE))

    assert decision.allowed is False
    assert decision.reason == deletion.REASON_VOICE_CHANNEL
    assert decision.speech == (
        "Kalıcı silmeyi sesle onaylayamam efendim; panelden onaylamanız gerekiyor."
    )


def test_a_device_cannot_confirm_its_own_destruction_request() -> None:
    """`device` is the agent process, not a person. A machine confirming what it asked for
    is not a second channel, it is the same one twice."""
    assert _permanent(_confirmation(channel="device")).allowed is False


def test_a_click_in_the_cockpit_is_enough() -> None:
    assert _permanent(_confirmation(channel="web")).allowed is True
    assert _permanent(_confirmation(channel="desktop")).allowed is True
    assert _permanent(_confirmation(channel="mobile")).allowed is True
    assert _permanent(_confirmation(channel="cli")).allowed is True


def test_no_confirmation_at_all_is_refused() -> None:
    decision = _permanent(None)

    assert decision.allowed is False
    assert decision.reason == deletion.REASON_NO_CONFIRMATION


def test_a_confirmation_authorises_only_the_thing_it_names() -> None:
    """Otherwise "yes" clicked for one file authorises the deletion of another, which is
    exactly how a confirmation dialog becomes a formality."""
    decision = _permanent(_confirmation(subject_id="rapor.pdf"), subject_id="vergi.pdf")

    assert decision.allowed is False
    assert decision.reason == deletion.REASON_WRONG_SUBJECT


def test_a_confirmation_for_another_KIND_of_thing_does_not_carry_over() -> None:
    decision = deletion.evaluate_permanent_delete(
        subject_kind="artifact",
        subject_id="rapor.pdf",
        confirmation=_confirmation(subject_kind="file", subject_id="rapor.pdf"),
        now=NOW,
    )

    assert decision.allowed is False
    assert decision.reason == deletion.REASON_WRONG_SUBJECT


def test_a_stale_confirmation_is_no_confirmation() -> None:
    """The window is the gap between reading what you are about to destroy and destroying
    it. A confirmation found in a tab from this morning is not that."""
    decision = _permanent(_confirmation(), now=NOW + timedelta(hours=1))

    assert decision.allowed is False
    assert decision.reason == deletion.REASON_EXPIRED


def test_a_confirmation_inside_the_window_still_counts() -> None:
    assert _permanent(_confirmation(), now=NOW + timedelta(minutes=4)).allowed is True


def test_an_unknown_channel_is_refused_rather_than_assumed_safe() -> None:
    decision = _permanent(_confirmation(channel="carrier_pigeon"))

    assert decision.allowed is False
    assert decision.reason == deletion.REASON_UNKNOWN_CHANNEL


def test_no_channel_is_both_confirming_and_not() -> None:
    """The two sets are the whole vocabulary and they must not overlap; a channel that is
    both would resolve by whichever check happens to run first."""
    assert not (deletion.CONFIRMING_CHANNELS & deletion.NON_CONFIRMING_CHANNELS)


def test_every_client_kind_the_system_issues_is_classified() -> None:
    """A session kind nobody classified would fall to "unknown channel" and be refused -
    safe, but silently unusable. This makes adding a client kind a decision."""
    from app.identity.models import CLIENT_KINDS

    known = deletion.CONFIRMING_CHANNELS | deletion.NON_CONFIRMING_CHANNELS
    assert set(CLIENT_KINDS) <= known, sorted(set(CLIENT_KINDS) - known)


def test_the_only_exempt_deletion_is_the_one_we_decided_on() -> None:
    """A gate with an unlisted bypass is not a gate. `forget_memory` already hard-deletes and
    stays outside on purpose - forgetting is a privacy RIGHT, and making the owner click
    before the system will stop remembering gets that backwards. This fails if a second
    permanent deletion joins it quietly."""
    assert set(deletion.GATE_EXEMPT) == {"app.memory.service.forget_memory"}
    assert all(reason.strip() for reason in deletion.GATE_EXEMPT.values()), "each names WHY"


def test_the_exempt_deletion_still_has_a_gate_of_its_own() -> None:
    """Exempt from THIS gate, not ungated: an explicit or pinned memory can only be forgotten
    by the OWNER actor, never by a policy actor or the Evolution Engine."""
    import inspect

    from app.memory import service as memory_service

    source = inspect.getsource(memory_service.forget_memory)
    assert "_require_owner_for_explicit" in source


def test_the_raising_form_carries_the_decision() -> None:
    with pytest.raises(deletion.PermanentDeleteRefused) as caught:
        deletion.require_permanent_delete(
            subject_kind="file", subject_id="rapor.pdf", confirmation=None, now=NOW
        )

    assert caught.value.decision.reason == deletion.REASON_NO_CONFIRMATION


def test_the_raising_form_returns_the_decision_when_allowed() -> None:
    decision = deletion.require_permanent_delete(
        subject_kind="file",
        subject_id="rapor.pdf",
        confirmation=_confirmation(),
        now=NOW,
    )

    assert decision.allowed is True
