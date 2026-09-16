"""B33 req 462-471: the router's five lifecycle intents, gated on the ONE fact
``native_build_focused`` the way M28's check/fix/rebuild are.

The expected values below were measured against the live router before the matchers were
written (2026-09-15): every "stays where it was" row is the unchanged route, not a hope.
"""

from __future__ import annotations

import pytest

from app.voice.intents import CAPABILITY_BY_INTENT, Intent, resolve_intent


@pytest.mark.parametrize(
    ("text", "intent", "tool"),
    [
        ("Masaüstü uygulamasını aç.", Intent.NATIVE_LAUNCH, "native.launch"),
        ("Windows uygulamasını çalıştır.", Intent.NATIVE_LAUNCH, "native.launch"),
        ("EXE'yi aç.", Intent.NATIVE_LAUNCH, "native.launch"),
        ("Yerel uygulamayı aç.", Intent.NATIVE_LAUNCH, "native.launch"),
        ("Derlenen uygulamayı çalıştır.", Intent.NATIVE_LAUNCH, "native.launch"),
        ("Programı başlat.", Intent.NATIVE_LAUNCH, "native.launch"),
        ("Uygulamayı doğrula.", Intent.NATIVE_VERIFY, "native.verify"),
        ("Arayüzünü test et.", Intent.NATIVE_VERIFY, "native.verify"),
        ("Uygulamanın günlüğünü oku.", Intent.NATIVE_LOG, "native.log"),
        ("Uygulamanın logunu göster.", Intent.NATIVE_LOG, "native.log"),
        ("Kurulumu kaldır.", Intent.NATIVE_UNINSTALL, "native.uninstall"),
        ("Uygulamayı kaldır.", Intent.NATIVE_UNINSTALL, "native.uninstall"),
        ("Uygulamayı güncelle.", Intent.NATIVE_UPDATE, "native.update"),
    ],
)
def test_with_a_native_build_focused_the_lifecycle_verbs_reach_their_tools(
    text: str, intent: Intent, tool: str
) -> None:
    resolved = resolve_intent(text, native_build_focused=True)
    assert resolved.intent is intent, resolved
    assert CAPABILITY_BY_INTENT[intent] == tool
    assert resolved.native_ref == "current"


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        # M23's App Factory keeps the bare application verbs, and the qualified ones too
        # while there is no native build to be about.
        ("Uygulamayı aç.", Intent.APP_FACTORY_OPEN),
        ("Uygulamayı çalıştır.", Intent.APP_FACTORY_RUN),
        ("Bir uygulama aç.", Intent.APP_FACTORY_OPEN),
        ("Masaüstü uygulamasını aç.", Intent.APP_FACTORY_OPEN),
        ("Windows uygulamasını çalıştır.", Intent.APP_FACTORY_RUN),
        ("EXE'yi aç.", Intent.NONE),
        # ... and the verbs that have no owner without a build resolve to nothing.
        ("Uygulamayı doğrula.", Intent.NONE),
        ("Uygulamanın günlüğünü oku.", Intent.NONE),
        ("Uygulamayı güncelle.", Intent.NONE),
        ("Programı başlat.", Intent.NONE),
    ],
)
def test_without_a_native_build_the_same_words_stay_where_they_were(
    text: str, intent: Intent
) -> None:
    assert resolve_intent(text, native_build_focused=False).intent is intent


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Uygulamayı aç.", Intent.APP_FACTORY_OPEN),
        ("Uygulamayı çalıştır.", Intent.APP_FACTORY_RUN),
        ("Bir uygulama aç.", Intent.APP_FACTORY_OPEN),
        ("Yaptığın uygulamayı aç.", Intent.APP_FACTORY_OPEN),
    ],
)
def test_the_bare_application_sentence_stays_m23s_even_with_a_native_build(
    text: str, intent: Intent
) -> None:
    """The older contract (test_voice_native_intents: every other family keeps its own
    utterance even with a build in the system): the native launch is reached by a native
    word, never by taking M23's sentence."""
    assert resolve_intent(text, native_build_focused=True).intent is intent


def test_the_installer_noun_needs_no_focus_because_it_names_the_family_itself() -> None:
    """ "Kurulumu kaldır." is this family's own sentence (spec §6's negative): resolved
    with or without a build, and the tool then says whether anything was installed."""
    assert (
        resolve_intent("Kurulumu kaldır.", native_build_focused=False).intent
        is Intent.NATIVE_UNINSTALL
    )


@pytest.mark.parametrize(
    "text",
    [
        "Sistemi güncelle.",
        "Sunumu güncelle.",
        "Belgeyi doğrula.",
        "Uygulamayı emülatörde aç.",
        "Notepad'i aç.",
        "Pencereyi kapat.",
    ],
)
def test_other_families_nouns_are_never_taken_by_the_lifecycle_matchers(text: str) -> None:
    resolved = resolve_intent(text, native_build_focused=True)
    assert resolved.intent not in (
        Intent.NATIVE_LAUNCH,
        Intent.NATIVE_VERIFY,
        Intent.NATIVE_LOG,
        Intent.NATIVE_UNINSTALL,
        Intent.NATIVE_UPDATE,
    ), resolved


def test_ios_is_refused_by_name_before_any_lifecycle_verb_is_read() -> None:
    resolved = resolve_intent("iOS uygulamasını aç.", native_build_focused=True)
    assert resolved.intent is not Intent.NATIVE_LAUNCH
