"""The action receipt (docs/M18_ACTION_CONTRACT.md §1, §5.5): the speech table, the
banned fake-completion phrases, the ``as_dict`` shape, the ledger row.

The persona is checked here too (contract §6, §8: "contains the tool names and the
banned phrases; no test reads the model").
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.receipt import (
    ACTION_TRACE_MAX_STEPS,
    CLIENT_ERROR_CLASSES,
    EXECUTION_EXECUTED,
    EXECUTION_NOOP,
    EXECUTION_REFUSED,
    EYE_SPEECH_DISABLE_ALREADY,
    EYE_SPEECH_DISABLE_RECORD_UNVERIFIED,
    EYE_SPEECH_DISABLE_UNVERIFIED,
    EYE_SPEECH_DISABLE_VERIFIED,
    EYE_SPEECH_ENABLE_ALREADY,
    EYE_SPEECH_ENABLE_DEVICE_BUSY,
    EYE_SPEECH_ENABLE_DEVICE_NOT_FOUND,
    EYE_SPEECH_ENABLE_DEVICE_UNAVAILABLE,
    EYE_SPEECH_ENABLE_GET_USER_MEDIA_FAILED,
    EYE_SPEECH_ENABLE_PERCEPTION_START_FAILED,
    EYE_SPEECH_ENABLE_PERMISSION_DENIED,
    EYE_SPEECH_ENABLE_RECORD_UNVERIFIED,
    EYE_SPEECH_ENABLE_STATE_TRANSITION_FAILED,
    EYE_SPEECH_ENABLE_TRACK_ENDED,
    EYE_SPEECH_ENABLE_UNVERIFIED,
    EYE_SPEECH_ENABLE_VERIFIED,
    FAKE_COMPLETION_PHRASES,
    RELEASE_PROMOTE_REFUSED_SPEECH,
    SPEECH_TEMPLATES,
    TERMINAL_ALREADY,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    bound_action_trace,
    contains_fake_completion,
    eye_speech,
    record_receipt,
)
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import EVENT_TYPE_ACTION_RECEIPT, SUBSYSTEM_PRESENCE
from app.voice.realtime_sessions import persona

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


def _receipt(**overrides) -> ActionReceipt:
    base = dict(
        action_id="call_1",
        capability="eye.disable",
        requested_state="disabled",
        execution_status=EXECUTION_EXECUTED,
        terminal_status=TERMINAL_VERIFIED,
        observed_after={"server": {"eye_enabled": False}, "local": {"state": "DISABLED"}},
        evidence_refs=[{"kind": "realtime_session", "ref": "s1"}],
        error_class=None,
        speech=EYE_SPEECH_DISABLE_VERIFIED,
        started_at=NOW,
        completed_at=NOW,
    )
    base.update(overrides)
    return ActionReceipt(**base)


# ------------------------------------------------------------------ speech table


@pytest.mark.parametrize(
    ("enable", "terminal", "error_class", "expected"),
    [
        (False, TERMINAL_VERIFIED, None, "Gözümü kapattım efendim."),
        (False, TERMINAL_ALREADY, None, "Gözüm zaten kapalı efendim."),
        (False, TERMINAL_UNVERIFIED, "state_mismatch", "Kamerayı kapatamadım; işlem doğrulanmadı."),
        (False, TERMINAL_FAILED, "capability_missing", "Kamerayı kapatamadım; işlem doğrulanmadı."),
        (True, TERMINAL_VERIFIED, None, "Gözümü açtım efendim."),
        (True, TERMINAL_ALREADY, None, "Gözüm zaten açık efendim."),
        (
            True,
            TERMINAL_FAILED,
            "permission_denied",
            "Kamerayı açamadım; tarayıcı kamera izni vermedi.",
        ),
        (
            True,
            TERMINAL_FAILED,
            "device_unavailable",
            "Kamerayı açamadım; kamera bulunamadı ya da meşgul.",
        ),
        (True, TERMINAL_FAILED, "device_not_found", "Kamerayı açamadım; kamera bulunamadı."),
        (
            True,
            TERMINAL_FAILED,
            "device_busy",
            "Kamerayı açamadım; kamera başka bir uygulama tarafından kullanılıyor.",
        ),
        (
            True,
            TERMINAL_FAILED,
            "get_user_media_failed",
            "Kamerayı açamadım; tarayıcı kamera akışını başlatamadı.",
        ),
        (
            True,
            TERMINAL_FAILED,
            "stream_created_but_track_ended",
            "Kamera açıldı ama görüntü akışı hemen kesildi.",
        ),
        (
            True,
            TERMINAL_FAILED,
            "perception_start_failed",
            "Kamera açıldı ama algılama döngüsü başlatılamadı.",
        ),
        (
            True,
            TERMINAL_FAILED,
            "state_transition_failed",
            "Kamerayı açamadım; durum geçişi tamamlanamadı.",
        ),
        (True, TERMINAL_FAILED, "timeout", "Kamerayı açamadım; işlem doğrulanmadı."),
        (True, TERMINAL_FAILED, "capability_missing", "Kamerayı açamadım; işlem doğrulanmadı."),
        (True, TERMINAL_FAILED, "something_new", "Kamerayı açamadım; işlem doğrulanmadı."),
        (True, TERMINAL_UNVERIFIED, "state_mismatch", "Kamerayı açamadım; işlem doğrulanmadı."),
    ],
)
def test_eye_speech_table_is_the_contracts(enable, terminal, error_class, expected) -> None:
    assert eye_speech(enable=enable, terminal_status=terminal, error_class=error_class) == expected


@pytest.mark.parametrize(
    ("enable", "terminal", "error_class", "expected"),
    [
        # the camera DID what was asked; only the Cloud Core's record is in doubt
        (False, TERMINAL_UNVERIFIED, "durable_write_failed", EYE_SPEECH_DISABLE_RECORD_UNVERIFIED),
        (False, TERMINAL_UNVERIFIED, "read_back_failed", EYE_SPEECH_DISABLE_RECORD_UNVERIFIED),
        (False, TERMINAL_UNVERIFIED, "state_mismatch", EYE_SPEECH_DISABLE_RECORD_UNVERIFIED),
        (True, TERMINAL_UNVERIFIED, "durable_write_failed", EYE_SPEECH_ENABLE_RECORD_UNVERIFIED),
        (True, TERMINAL_UNVERIFIED, "state_mismatch", EYE_SPEECH_ENABLE_RECORD_UNVERIFIED),
        # physical_ok changes nothing above unverified, or for a failed local report
        (False, TERMINAL_VERIFIED, None, EYE_SPEECH_DISABLE_VERIFIED),
        (True, TERMINAL_ALREADY, None, EYE_SPEECH_ENABLE_ALREADY),
        (False, TERMINAL_FAILED, "capability_missing", EYE_SPEECH_DISABLE_UNVERIFIED),
    ],
)
def test_a_physically_done_action_with_an_unverified_record_says_exactly_that(
    enable, terminal, error_class, expected
) -> None:
    speech = eye_speech(
        enable=enable, terminal_status=terminal, error_class=error_class, physical_ok=True
    )
    assert speech == expected


def test_the_exact_strings_are_exported_by_name() -> None:
    assert EYE_SPEECH_DISABLE_VERIFIED == "Gözümü kapattım efendim."
    assert EYE_SPEECH_DISABLE_ALREADY == "Gözüm zaten kapalı efendim."
    assert EYE_SPEECH_DISABLE_UNVERIFIED == "Kamerayı kapatamadım; işlem doğrulanmadı."
    assert EYE_SPEECH_DISABLE_RECORD_UNVERIFIED == (
        "Kamera kapandı ancak işlem kaydını doğrulayamadım."
    )
    assert EYE_SPEECH_ENABLE_VERIFIED == "Gözümü açtım efendim."
    assert EYE_SPEECH_ENABLE_ALREADY == "Gözüm zaten açık efendim."
    assert EYE_SPEECH_ENABLE_PERMISSION_DENIED == "Kamerayı açamadım; tarayıcı kamera izni vermedi."
    assert EYE_SPEECH_ENABLE_DEVICE_NOT_FOUND == "Kamerayı açamadım; kamera bulunamadı."
    assert EYE_SPEECH_ENABLE_DEVICE_BUSY == (
        "Kamerayı açamadım; kamera başka bir uygulama tarafından kullanılıyor."
    )
    assert EYE_SPEECH_ENABLE_DEVICE_UNAVAILABLE == (
        "Kamerayı açamadım; kamera bulunamadı ya da meşgul."
    )
    assert EYE_SPEECH_ENABLE_GET_USER_MEDIA_FAILED == (
        "Kamerayı açamadım; tarayıcı kamera akışını başlatamadı."
    )
    assert EYE_SPEECH_ENABLE_TRACK_ENDED == "Kamera açıldı ama görüntü akışı hemen kesildi."
    assert EYE_SPEECH_ENABLE_PERCEPTION_START_FAILED == (
        "Kamera açıldı ama algılama döngüsü başlatılamadı."
    )
    assert EYE_SPEECH_ENABLE_STATE_TRANSITION_FAILED == (
        "Kamerayı açamadım; durum geçişi tamamlanamadı."
    )
    assert EYE_SPEECH_ENABLE_UNVERIFIED == "Kamerayı açamadım; işlem doğrulanmadı."
    assert EYE_SPEECH_ENABLE_RECORD_UNVERIFIED == (
        "Kamera açıldı ancak işlem kaydını doğrulayamadım."
    )
    assert RELEASE_PROMOTE_REFUSED_SPEECH == (
        "Canlıya alma kararı sizin efendim; onayı Core'daki Onay Merkezi'nden verirsiniz. "
        "Ben kendi başıma canlıya almam."
    )


def test_every_client_error_class_is_in_the_vocabulary() -> None:
    assert CLIENT_ERROR_CLASSES == (
        "permission_denied",
        "device_not_found",
        "device_busy",
        "device_unavailable",
        "get_user_media_failed",
        "stream_created_but_track_ended",
        "perception_start_failed",
        "state_transition_failed",
        "timeout",
        "capability_missing",
    )
    # every named enable failure has its own sentence; only the two the contract keeps
    # generic (timeout, capability_missing) fall back to "işlem doğrulanmadı"
    for error_class in CLIENT_ERROR_CLASSES:
        speech = eye_speech(enable=True, terminal_status=TERMINAL_FAILED, error_class=error_class)
        if error_class in ("timeout", "capability_missing"):
            assert speech == EYE_SPEECH_ENABLE_UNVERIFIED
        else:
            assert speech != EYE_SPEECH_ENABLE_UNVERIFIED, error_class


def test_speech_that_is_not_verified_never_claims_the_mutation() -> None:
    """The defect: "öyle olmuş gibi düşün". Any outcome below verified/already, with no
    local evidence the camera changed, says the camera could NOT be changed, in those
    words."""
    for terminal in (TERMINAL_UNVERIFIED, TERMINAL_FAILED):
        for enable in (True, False):
            speech = eye_speech(enable=enable, terminal_status=terminal, error_class=None)
            assert "amadım" in speech  # kapatamadım / açamadım
            assert "kapattım" not in speech and "açtım" not in speech


def test_a_physically_done_but_unverified_action_never_says_it_could_not_be_done() -> None:
    """The second defect (session 3eb6fee7): the browser closed the camera and the
    assistant said "kapatamadım". Neither "kapattım" (the record is unverified) nor
    "kapatamadım" (the camera is off) is true; the sentence carries both facts."""
    for enable in (True, False):
        speech = eye_speech(
            enable=enable,
            terminal_status=TERMINAL_UNVERIFIED,
            error_class="durable_write_failed",
            physical_ok=True,
        )
        assert "amadım;" not in speech and "kapatamadım" not in speech and "açamadım" not in speech
        assert "kapattım" not in speech and "açtım" not in speech
        assert "doğrulayamadım" in speech


# ------------------------------------------------------------------ banned phrases


def test_banned_phrases_live_in_one_place_and_are_the_contracts() -> None:
    assert FAKE_COMPLETION_PHRASES == (
        "yapmış gibi düşün",
        "olmuş gibi düşün",
        "gibi düşün",
        "sayabiliriz",
        "varsayalım",
        "oldu varsay",
    )


def test_no_speech_template_contains_a_banned_phrase() -> None:
    for template in SPEECH_TEMPLATES:
        assert not contains_fake_completion(template), template


def test_contains_fake_completion_is_turkish_casefolded() -> None:
    assert contains_fake_completion("Öyle OLMUŞ GİBİ DÜŞÜN.")
    assert contains_fake_completion("Kapandı sayabiliriz.")
    assert not contains_fake_completion("Gözümü kapattım efendim.")


def test_a_receipt_refuses_banned_speech_and_unknown_statuses() -> None:
    with pytest.raises(ValueError):
        _receipt(speech="Kapandı sayabiliriz.")
    with pytest.raises(ValueError):
        _receipt(terminal_status="done")
    with pytest.raises(ValueError):
        _receipt(execution_status="ok")


# ------------------------------------------------------------------ as_dict


def test_as_dict_is_the_contract_shape() -> None:
    out = _receipt().as_dict()
    assert set(out) == {
        "action_id",
        "capability",
        "requested_state",
        "execution_status",
        "terminal_status",
        "observed_after",
        "evidence_refs",
        "error_class",
        "speech",
        "started_at",
        "completed_at",
        "session_id",
        "observed_at",
        "action_trace",
    }
    assert out["observed_after"] == {
        "server": {"eye_enabled": False},
        "local": {"state": "DISABLED"},
    }
    assert out["started_at"] == "2026-09-06T12:00:00Z"
    assert out["speech"] == "Gözümü kapattım efendim."
    # a non-voice actor: no session, no read-back moment, no trace
    assert out["session_id"] is None and out["observed_at"] is None
    assert out["action_trace"] == []
    assert "speech" not in _receipt().as_dict(include_speech=False)


def test_session_observed_at_and_a_bounded_trace_ride_the_receipt() -> None:
    receipt = _receipt(
        session_id="3eb6fee7-0000-0000-0000-000000000000",
        observed_at=NOW,
        action_trace=[f"s{n}" for n in range(ACTION_TRACE_MAX_STEPS + 5)] + [None, 3],
    )
    out = receipt.as_dict()
    assert out["session_id"] == "3eb6fee7-0000-0000-0000-000000000000"
    assert out["observed_at"] == "2026-09-06T12:00:00Z"
    assert len(out["action_trace"]) == ACTION_TRACE_MAX_STEPS
    assert out["action_trace"][0] == "s0"


def test_bound_action_trace_is_lenient_and_bounded() -> None:
    assert bound_action_trace(None) == []
    assert bound_action_trace("disable") == []
    assert bound_action_trace(["a", "", "  b  ", 1, None]) == ["a", "b"]
    assert bound_action_trace(["x" * 500]) == ["x" * 80]
    assert len(bound_action_trace(["s"] * 40)) == ACTION_TRACE_MAX_STEPS


def test_only_verified_and_already_are_claimable() -> None:
    assert _receipt(terminal_status=TERMINAL_VERIFIED).claimable is True
    assert _receipt(
        terminal_status=TERMINAL_ALREADY,
        execution_status=EXECUTION_NOOP,
        speech=EYE_SPEECH_DISABLE_ALREADY,
    ).claimable
    assert not _receipt(
        terminal_status=TERMINAL_UNVERIFIED, speech=EYE_SPEECH_DISABLE_UNVERIFIED
    ).claimable
    assert not _receipt(
        terminal_status=TERMINAL_FAILED, speech=EYE_SPEECH_DISABLE_UNVERIFIED
    ).claimable


# ------------------------------------------------------------------ ledger row


def test_record_receipt_writes_the_action_receipt_row_without_speech(session) -> None:
    receipt = _receipt(session_id="s1", observed_at=NOW, action_trace=["disable"])
    row = record_receipt(session, receipt, SUBSYSTEM_PRESENCE)
    assert row is not None
    rows = ledger_service.query(
        session, subsystems=[SUBSYSTEM_PRESENCE], event_types=[EVENT_TYPE_ACTION_RECEIPT]
    )
    assert len(rows) == 1
    assert rows[0].action == "eye.disable"
    assert rows[0].status == "verified"
    assert rows[0].severity == "info"
    assert rows[0].detail_json["terminal_status"] == "verified"
    assert rows[0].detail_json["observed_after"]["server"] == {"eye_enabled": False}
    # correlate by session, place in time, see the client's steps - from the ledger alone
    assert rows[0].detail_json["session_id"] == "s1"
    assert rows[0].detail_json["observed_at"] == "2026-09-06T12:00:00Z"
    assert rows[0].detail_json["action_trace"] == ["disable"]
    assert "speech" not in rows[0].detail_json
    assert "kapattım" not in rows[0].factual_summary
    assert rows[0].evidence_refs == [{"kind": "realtime_session", "ref": "s1"}]

    # idempotent on the action id
    record_receipt(session, receipt, SUBSYSTEM_PRESENCE)
    assert len(ledger_service.query(session, event_types=[EVENT_TYPE_ACTION_RECEIPT])) == 1


def test_a_refusal_is_recorded_as_a_notice_and_a_failure_as_a_warning(session) -> None:
    refused = _receipt(
        action_id="call_r",
        capability="release.promote",
        requested_state="promoted",
        execution_status=EXECUTION_REFUSED,
        terminal_status=TERMINAL_FAILED,
        error_class="owner_authorization_required",
        speech=RELEASE_PROMOTE_REFUSED_SPEECH,
    )
    failed = _receipt(
        action_id="call_f",
        capability="eye.enable",
        requested_state="active",
        execution_status="failed",
        terminal_status=TERMINAL_FAILED,
        error_class="permission_denied",
        speech=EYE_SPEECH_ENABLE_PERMISSION_DENIED,
    )
    record_receipt(session, refused, "deployment")
    record_receipt(session, failed, SUBSYSTEM_PRESENCE)
    by_action = {
        r.action: r for r in ledger_service.query(session, event_types=[EVENT_TYPE_ACTION_RECEIPT])
    }
    assert by_action["release.promote"].severity == "notice"
    assert by_action["release.promote"].status == "failed"
    assert by_action["release.promote"].subsystem == "deployment"
    assert by_action["eye.enable"].severity == "warning"


# ------------------------------------------------------------------ persona (§6)


def test_persona_names_the_tools_and_the_banned_phrases() -> None:
    block = persona.ACTION_GROUNDING_TR
    for tool in ("state.now", "activity.explain", "eye.enable", "eye.disable", "release.promote"):
        assert tool in block
    for phrase in FAKE_COMPLETION_PHRASES:
        assert phrase in block
    for command in ("Gözünü aç", "kamerayı aç", "beni izle", "gözünü kapat", "Canlıya al"):
        assert command.lower() in block.lower()
    assert "bakıyorum" in block  # named as forbidden for these tools
    # the eye changes ONLY through the tool - there is no server-side net (contract §5.3)
    assert "yalnizca bu araçlarla" in block.lower().replace("ı", "i")
    instructions = persona.build_instructions()
    assert block in instructions
    # the earlier blocks are kept
    assert persona.PERSONA_TR in instructions
    assert persona.SELF_EXPLANATION_TR in instructions
