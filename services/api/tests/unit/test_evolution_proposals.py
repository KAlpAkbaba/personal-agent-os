"""An unmet request becomes a row, not just a sentence (ADR-0103).

Owner directive 2026-09-09: "bir şey sorduğumda 'bunu yapamıyorum' değil ... 'bunu feature
olarak ekleyeyim mi' olarak dönüp ... hayır yok gibi cevapları artık kabul etmeyeceğim."

The machinery all existed. ``GapDetector`` walks the owner's own resolution order and
``GapService`` writes the trail to ``capability_gaps``, which the Evolution Supervisor reads
on every tick — and the only callers were one REST route and the M24 genesis service. A
request the assistant could not serve produced a sentence and nothing else.

These drive the REAL detector and the REAL recorder against a temporary database: a gap that
is claimed but not written would pass a mock and fail here.
"""

from __future__ import annotations

import uuid

import pytest

from app.evolution.gaps import GapDetector, GapService
from app.evolution.proposals import (
    SPEECH_ALREADY_POSSIBLE,
    SPEECH_PRODUCT_CHANGE,
    SPEECH_QUEUED,
    capability_id,
    propose,
    speech_for,
)
from app.evolution.registry import CapabilityRegistry
from tests.unit.test_evolution_registry import make_session_factory


@pytest.fixture()
def real_stack() -> tuple[GapDetector, GapService]:
    factory = make_session_factory()
    return GapDetector(CapabilityRegistry(factory)), GapService(factory)


def test_a_request_nothing_can_serve_becomes_a_durable_gap(real_stack) -> None:
    detector, recorder = real_stack

    proposal = propose(
        detector,
        recorder,
        request_text="Ekranı ikiye bölüp sol tarafa not defterini yerleştir.",
    )

    assert proposal.gap_id, "the proposal claims a gap it never wrote"
    stored = recorder.get(uuid.UUID(proposal.gap_id))
    assert stored["request_text"] == "Ekranı ikiye bölüp sol tarafa not defterini yerleştir."
    assert stored["decision_trail"], "the decision trail is the evidence; it must be there"
    assert proposal.speech in (
        SPEECH_QUEUED,
        SPEECH_PRODUCT_CHANGE,
        SPEECH_ALREADY_POSSIBLE,
    )


def test_the_owners_sentence_is_stored_verbatim(real_stack) -> None:
    """The slug is a machine id; the owner's words are the record. Turkish letters survive
    in the second and are folded only in the first."""
    detector, recorder = real_stack
    sentence = "Şöyle bir şey istiyorum: İndirilenler'deki görselleri boyuta göre sırala."

    proposal = propose(detector, recorder, request_text=sentence)

    stored = recorder.get(uuid.UUID(proposal.gap_id))
    assert stored["request_text"] == sentence
    assert stored["requested_capability"].isascii()


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [
        ("existing_capability", SPEECH_ALREADY_POSSIBLE),
        ("composition", SPEECH_ALREADY_POSSIBLE),
        ("configuration", SPEECH_ALREADY_POSSIBLE),
        ("extension", SPEECH_ALREADY_POSSIBLE),
        ("generation", SPEECH_QUEUED),
        ("product_change_required", SPEECH_PRODUCT_CHANGE),
    ],
)
def test_each_decision_gets_the_sentence_that_is_true_of_it(
    resolution: str, expected: str
) -> None:
    """Three different things can be true of an unmet request and the owner is owed the
    right one: it is already possible, it has been queued, or it needs a change to the
    product that this engine may not start on its own."""
    assert speech_for(resolution) == expected


def test_nothing_ever_promises_a_release() -> None:
    """The engine's authority stops at shadow_ready. Not one of these sentences may say
    the feature will ship — that is the owner's decision and saying otherwise is a lie the
    owner would only discover later."""
    for speech in (SPEECH_QUEUED, SPEECH_PRODUCT_CHANGE, SPEECH_ALREADY_POSSIBLE):
        lowered = speech.lower()
        assert "yayına al" not in lowered
        assert "canlıya al" not in lowered
        assert "kurdum" not in lowered


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("Ekranı böl", "owner.ekrani_bol"),
        ("İndirilenler'i sırala", "owner.indirilenler_i_sirala"),
        ("   ", "owner.request"),
        ("!!!", "owner.request"),
        ("ÇĞİÖŞÜ", "owner.cgiosu"),
        ("3 sekmeyi kapat", "owner.sekmeyi_kapat"),  # an id may not start with a digit
    ],
)
def test_the_id_is_derived_from_the_sentence_and_never_empty(
    sentence: str, expected: str
) -> None:
    assert capability_id(sentence) == expected


@pytest.mark.parametrize(
    "sentence",
    [
        "Ekranı böl",
        "İndirilenler'i sırala",
        "   ",
        "!!!",
        "ÇĞİÖŞÜ",
        "3 sekmeyi kapat",
        "-" * 300,
        "çok uzun bir cümle " * 40,
        "a",
        "Z",
        "😀 emoji",
        "..dots..",
        "__underscores__",
    ],
)
def test_every_id_this_can_produce_satisfies_the_engine_s_own_regex(sentence: str) -> None:
    """Read the contract from the module that owns it. ``CapabilityRequest.parse`` refuses
    an id that does not match, with "refusing to derive code" — which is what happened the
    first time this was written against a hyphenated slug."""
    from app.evolution.tokens import CAPABILITY_ID_RE

    generated = capability_id(sentence)
    assert CAPABILITY_ID_RE.match(generated), generated


def test_an_empty_request_is_refused_rather_than_recorded(real_stack) -> None:
    detector, recorder = real_stack
    with pytest.raises(ValueError):
        propose(detector, recorder, request_text="   ")
