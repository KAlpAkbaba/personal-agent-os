"""The cancel verbs ("iptal", "sil", "kaldır") never read a negated sentence as the positive.

Test team, round t-manual-20261006e (job dil-dayanikliligi, staging 72884b71): nine negated
sentences resolved to a cancel, and two bare "kaldır" sentences to an alarm create. The
owner's rule (2026-09-19, again 2026-10-06): a negated or garbled sentence NEVER deletes or
cancels - it reaches ``none`` (or a question), never the cancel intent.
"""

from __future__ import annotations

import pytest

from app.voice.intents import Intent, resolve_intent

#: Every intent that deletes or cancels in the alarm / routine / memory families.
_CANCELS = {Intent.ALARM_CANCEL, Intent.ROUTINE_CANCEL, Intent.MEMORY_FORGET}

NEGATED = [
    "alarmı silme",
    "alarmı iptal etme",
    "alarmımı sakın silme",
    "alarmi silme",  # ASCII
    "ALARMI SİLME",  # capitals, the Turkish way
    "alarmı silmeyin",
    "alarmı silmeden önce bana sor",
    "alarmı silmek istemiyorum",
    "sabah rutinini iptal etme",
    # the same, by the other verbs and politeness forms
    "Alarmı kaldırmayınız.",
    "Sabah rutinini iptal etmeyiniz.",
    "Bunu hafızandan silmeyin.",
    "Alarmı sakın sil.",
    "Alarmı silmeyi unut.",  # forget about deleting it: the verbal noun asks for no act
]


@pytest.mark.parametrize("said", NEGATED)
def test_a_negated_cancel_never_cancels(said: str) -> None:
    resolved = resolve_intent(said)
    assert resolved.intent not in _CANCELS, f"{said!r} -> {resolved.intent.value}"


@pytest.mark.parametrize("said", ["kaldırma", "onu kaldır", "Kaldırma.", "Onu kaldır."])
def test_a_bare_kaldir_with_nothing_before_it_is_no_alarm(said: str) -> None:
    resolved = resolve_intent(said)
    assert resolved.intent not in {Intent.ALARM_CREATE, *_CANCELS}, (
        f"{said!r} -> {resolved.intent.value}"
    )


@pytest.mark.parametrize(
    ("said", "intent"),
    [
        ("alarmı sil", Intent.ALARM_CANCEL),
        ("alarmı iptal et", Intent.ALARM_CANCEL),
        ("Alarmı kaldır.", Intent.ALARM_CANCEL),
        ("alarmi sil", Intent.ALARM_CANCEL),
        ("ALARMI SİL", Intent.ALARM_CANCEL),
        ("Alarmı silin.", Intent.ALARM_CANCEL),
        ("alarmı silmek istiyorum", Intent.ALARM_CANCEL),
        ("sabah rutinini iptal et", Intent.ROUTINE_CANCEL),
        ("Bunu hafızandan sil.", Intent.MEMORY_FORGET),
        ("Beni kaldır.", Intent.ALARM_CREATE),  # the bare wake: "kaldır" with the one it wakes
        ("Yarın yedide kaldır.", Intent.ALARM_CREATE),  # no thing named: still the wake
        ("Saat yedide beni uyandır.", Intent.ALARM_CREATE),
        # "don't forget to wake me" asks for the act: the verbal noun is no negation
        ("Saat yedide beni uyandırmayı unutma.", Intent.ALARM_CREATE),
        ("Alarmı iptal edin.", Intent.ALARM_CANCEL),
        ("Sakin ol, alarmı sil.", Intent.ALARM_CANCEL),  # "sakin" is calm, not "sakın"
    ],
)
def test_the_positive_forms_still_act(said: str, intent: Intent) -> None:
    assert resolve_intent(said).intent is intent, said
