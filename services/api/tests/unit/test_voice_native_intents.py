"""The M28 voice family in the ONE router (docs/M28_NATIVE_APP_FACTORY_SPEC.md §6).

Three things this suite exists to hold, and the third is the one that has burned this
repository before.

**The phrases resolve.** Every utterance spec §6 spells, plus the ASR-shaped
(diacritic-free) variants a real transcript produces, plus the target word each one
carries.

**The wiring agrees.** Every ACTION intent names a capability the tool registry actually
serves, and the one QUERY names a tool it actually serves - a map that pointed at a tool
nobody registered would pass every routing test in this file and fail at the owner's
first sentence.

**The narrowing gates hold.** This router has had five intent collisions; four of them
were MEASURED against the live router before this family's matchers were written (the
module comment above ``app.voice.intents._native_create_windows_match`` names each), and
the fifth was found by this family's own probe. Every one has an assertion here, in BOTH
directions - the M28 phrase resolves, AND the other family's phrase still resolves to the
other family. A narrowing gate nobody tests is a gate that quietly reopens, and the M27
record ("fixed by NARROWING, not by reordering, because reordering only moves the
collision to whichever family loses the race") is why this file asserts both sides rather
than just its own.

The corpus (``tests/voice_corpus/corpus.py``'s ``nativeapps`` category) proves the same
routes end to end through the real relay; what is here that cannot be there is the
``native_build_focused=True`` half of the noun-less matchers, which needs two fixtures a
single corpus case cannot hold at once (a native build AND an app project, a native build
AND a creative run).
"""

from __future__ import annotations

import pytest

from app.nativefactory.spec import NATIVE_TARGETS
from app.nativefactory.stacks import IOS_WORDS
from app.voice.intents import (
    CAPABILITY_BY_INTENT,
    QUERY_TOOL_BY_INTENT,
    Intent,
    resolve_intent,
)
from app.voice.realtime_sessions.tools import default_registry
from app.voice.realtime_sessions.tools_native import NATIVE_TOOL_NAMES

NATIVE_INTENTS: tuple[Intent, ...] = (
    Intent.NATIVE_CREATE_WINDOWS,
    Intent.NATIVE_BUILD_EXE,
    Intent.NATIVE_BUILD_INSTALLER,
    Intent.NATIVE_CREATE_ANDROID,
    Intent.NATIVE_BUILD_APK,
    Intent.NATIVE_EMULATOR_OPEN,
    Intent.NATIVE_CHECK,
    Intent.NATIVE_FIX,
    Intent.NATIVE_REBUILD,
)


def r(text: str, **kw):
    return resolve_intent(text, **kw)


# --------------------------------------------------------------- the phrases


@pytest.mark.parametrize(
    ("utterance", "intent", "target"),
    [
        # spec §6, verbatim
        ("Bana Windows için masaüstü uygulaması yap.", Intent.NATIVE_CREATE_WINDOWS, "windows_exe"),
        ("Bunu EXE olarak çıkar.", Intent.NATIVE_BUILD_EXE, "windows_exe"),
        ("Kurulum dosyasını oluştur.", Intent.NATIVE_BUILD_INSTALLER, "windows_msix"),
        ("Android sürümünü yap.", Intent.NATIVE_CREATE_ANDROID, "android_apk"),
        ("Bunun Android sürümünü yap.", Intent.NATIVE_CREATE_ANDROID, "android_apk"),
        ("APK üret.", Intent.NATIVE_BUILD_APK, "android_apk"),
        ("Uygulamayı emülatörde aç.", Intent.NATIVE_EMULATOR_OPEN, "android_apk"),
        # ASR-shaped: no punctuation, lower case, no diacritics - exactly what
        # tests.voice_corpus.corpus._strip_diacritics produces
        ("windows icin masaustu uygulamasi yap", Intent.NATIVE_CREATE_WINDOWS, "windows_exe"),
        ("bunu exe olarak cikar", Intent.NATIVE_BUILD_EXE, "windows_exe"),
        ("kurulum dosyasini olustur", Intent.NATIVE_BUILD_INSTALLER, "windows_msix"),
        ("android surumunu yap", Intent.NATIVE_CREATE_ANDROID, "android_apk"),
        ("apk uret", Intent.NATIVE_BUILD_APK, "android_apk"),
        ("uygulamayi emulatorde ac", Intent.NATIVE_EMULATOR_OPEN, "android_apk"),
    ],
)
def test_every_spec_phrase_resolves_with_the_target_the_words_named(utterance, intent, target):
    resolved = r(utterance)
    assert resolved.intent is intent, utterance
    assert resolved.native_target == target


@pytest.mark.parametrize(
    ("utterance", "intent"),
    [
        ("Çalışıyor mu kontrol et.", Intent.NATIVE_CHECK),
        ("calisiyor mu kontrol et", Intent.NATIVE_CHECK),
        ("Hata varsa düzelt.", Intent.NATIVE_FIX),
        ("hata varsa duzelt", Intent.NATIVE_FIX),
        ("Yeni sürümü build et.", Intent.NATIVE_REBUILD),
        ("yeni surumu build et", Intent.NATIVE_REBUILD),
    ],
)
def test_the_noun_less_phrases_resolve_when_a_build_exists(utterance, intent):
    """Spec §6 spells these three with no native noun at all, so the gate is the
    CALLER's live fact rather than vocabulary."""
    assert r(utterance, native_build_focused=True).intent is intent


def test_a_deictic_android_request_points_at_the_build_on_the_stack():
    """Spec §7: "Bunun Android sürümünü yap." resolves through ids on the stack. A BARE
    "Android sürümünü yap." names no antecedent, so the tool resolves the project itself
    rather than being handed a wrong one."""
    assert r("Bunun Android sürümünü yap.").native_ref == "current"
    assert r("Android sürümünü yap.").native_ref is None


def test_the_target_words_are_the_factory_s_own_target_names():
    """The router may only ever name a target ``app.nativefactory.spec`` actually
    defines: a slot carrying a word the spec would refuse is a slot the tool must
    ignore, which is worse than an empty one."""
    said = {
        r(u).native_target
        for u in (
            "Bunu EXE olarak çıkar.",
            "APK üret.",
            "Kurulum dosyasını oluştur.",
            "Bana Windows için masaüstü uygulaması yap.",
            "Android sürümünü yap.",
        )
    }
    assert said <= set(NATIVE_TARGETS)


# ------------------------------------------------------------- the wiring


@pytest.mark.parametrize("intent", NATIVE_INTENTS)
def test_every_native_intent_names_a_tool_the_registry_actually_serves(intent):
    """ADR-0078's three "built, tested, never wired" defects, applied to this family:
    a capability map pointing at a tool nobody registered passes every routing test and
    fails at the owner's first sentence."""
    registered = set(default_registry().names())
    tool = CAPABILITY_BY_INTENT.get(intent) or QUERY_TOOL_BY_INTENT.get(intent)
    assert tool is not None, f"{intent} names neither a capability nor a query tool"
    assert tool in registered, f"{intent} -> {tool} is not registered"
    assert tool in NATIVE_TOOL_NAMES


def test_check_is_a_query_and_the_rest_are_actions():
    """Spec §6: "Çalışıyor mu kontrol et." reads a row and drives nothing. Being in
    CAPABILITY_BY_INTENT is what makes an intent an ACTION, so this is the one entry that
    must NOT be there."""
    assert Intent.NATIVE_CHECK not in CAPABILITY_BY_INTENT
    assert QUERY_TOOL_BY_INTENT[Intent.NATIVE_CHECK] == "native.check"
    for intent in NATIVE_INTENTS:
        if intent is not Intent.NATIVE_CHECK:
            assert intent in CAPABILITY_BY_INTENT


def test_all_eight_spec_tools_are_registered():
    """Spec §6 names eight tools; B33 (req 462-471) adds the four of the lifecycle after
    the build. ``native.install`` has no utterance of its own in that list and is
    registered all the same - the model reaches it after a package exists, the same way
    ``artifact.render`` is reached with no ARTIFACT_RENDER intent."""
    registered = set(default_registry().names())
    assert set(NATIVE_TOOL_NAMES) <= registered
    assert set(NATIVE_TOOL_NAMES) == {
        "native.create",
        "native.build",
        "native.package",
        "native.install",
        "native.launch",
        "native.check",
        "native.fix",
        "native.rebuild",
        "native.verify",
        "native.log",
        "native.uninstall",
        "native.update",
    }


def test_the_router_s_ios_words_still_cover_the_factory_s_own_list():
    """Both halves of one contract, read from each other's source.

    ``app.voice.intents`` spells the iOS words itself (this module's own "no
    cross-module import for a string literal" convention) while
    ``app.nativefactory.stacks.refuse_ios`` owns the authoritative list. Two tables that
    must agree will not, unless something makes one read the other - so this reads
    ``IOS_WORDS`` and fails if a word were ever added there that the ROUTER would then
    happily claim as a Windows build.
    """
    from app.voice.intents import _NATIVE_IOS_WORD_STEMS

    for word in IOS_WORDS:
        if " " in word:
            # A phrase can only ever be seen here as its separate tokens, and "store"
            # alone is not evidence of anything - so a phrase is covered by design, not
            # by a stem.
            continue
        assert any(word.startswith(stem) for stem in _NATIVE_IOS_WORD_STEMS), word


# ------------------------------------------------- the gates, in both directions


@pytest.mark.parametrize(
    "utterance",
    [
        "iOS sürümünü yap.",
        "iPad uygulamasını EXE olarak çıkar.",
        "iPhone için APK üret.",
        "ios surumunu yap",
    ],
)
def test_an_ios_request_reaches_no_native_intent_at_all(utterance):
    """Spec §1: no macOS, no Xcode, and with no MAUI workload not even a shared head.
    Nothing in this family may answer - so the honest refusal is
    ``app.nativefactory.stacks.refuse_ios``'s to give at the tool, and the router's job
    is to not claim the utterance in the first place."""
    for focused in (False, True):
        assert r(utterance, native_build_focused=focused).intent not in NATIVE_INTENTS


def test_iphone_plus_the_strongest_native_noun_still_stays_with_m23():
    """The sharp version of the iOS gate: "masaüstü uygulaması yap" is exactly
    NATIVE_CREATE_WINDOWS' own sentence, and the iOS check runs FIRST."""
    assert r("iPhone için masaüstü uygulaması yap.").intent is Intent.APP_FACTORY_CREATE


@pytest.mark.parametrize(
    ("utterance", "intent"),
    [
        # collision 1: M23 keeps every app-factory request that names no platform
        ("Web uygulaması yap.", Intent.APP_FACTORY_CREATE),
        ("Bana bir görev takip uygulaması yap.", Intent.APP_FACTORY_CREATE),
        ("Komut satırı aracı yap.", Intent.APP_FACTORY_CREATE),
        # collision 3: an open with no "emülatör" noun
        ("Bir uygulama aç.", Intent.APP_FACTORY_OPEN),
        ("Uygulamayı aç.", Intent.APP_FACTORY_OPEN),
        # M23's own status/run/test/stop vocabulary, unchanged
        ("Uygulama çalışıyor mu?", Intent.APP_FACTORY_STATUS),
        ("Uygulamayı çalıştır.", Intent.APP_FACTORY_RUN),
        ("Testleri çalıştır.", Intent.APP_FACTORY_TEST),
        ("Uygulamayı durdur.", Intent.APP_FACTORY_STOP),
        ("Hangi uygulamaları yaptın?", Intent.APP_FACTORY_LIST),
        # M22 keeps "yap" for its own kinds
        ("Sunum yap.", Intent.ARTIFACT_CREATE),
        ("Bunu PDF yap.", Intent.ARTIFACT_CREATE),
        # collision 2: "masaüstü" is also the Desktop FOLDER word
        ("Masaüstündeki sözleşme dosyasını bul.", Intent.FILE_SEARCH),
        (
            "Masaüstündeki teklif dosyalarını karşılaştırıp bir Excel tablosu ve "
            "yönetici özeti hazırla.",
            Intent.EXEC_START,
        ),
        # the fifth collision, found by this family's own probe: "kontrol" is M21's too
        ("Gelen kutumu kontrol eder misin?", Intent.MAIL_INBOX),
        # M27 keeps "düzelt" for its own colour noun
        ("Renkleri biraz düzelt.", Intent.CREATIVE_ADJUST),
        # M18.4 keeps "sürüm" without a compile verb
        ("Önceki sürüme dön.", Intent.RELEASE_ROLLBACK),
    ],
)
def test_every_other_family_keeps_its_own_utterance_even_with_a_build_in_the_system(
    utterance, intent
):
    """The half a corpus case cannot make: ``native_build_focused=True`` opens the three
    noun-less matchers, and each of these utterances must still belong to the family it
    belonged to before M28 existed."""
    assert r(utterance, native_build_focused=True).intent is intent
    assert r(utterance, native_build_focused=False).intent is intent


@pytest.mark.parametrize(
    ("utterance", "was"),
    [
        ("Hata varsa düzelt.", Intent.EXPLAIN),
        ("Çalışıyor mu kontrol et.", Intent.NONE),
    ],
)
def test_the_noun_less_phrases_stay_what_they_were_when_nothing_is_built(utterance, was):
    """Collision 4, from the other side. "hata" is ``_RESEARCH_PROBLEM_WORDS``' own word
    and resolved to EXPLAIN before M28; with no build in the system it still does."""
    assert r(utterance).intent is was


def test_rebuild_needs_the_compile_verb_not_merely_a_version_word():
    """ "build"/"derle" is claimed nowhere else in this router, but "sürüm" is - so the
    COMPILE verb is required and the version word alone never fires this, in either
    context."""
    assert r("Yeni sürümü build et.").intent is Intent.NATIVE_REBUILD
    assert r("Yeni sürüm.", native_build_focused=True).intent is not Intent.NATIVE_REBUILD
    assert r("Önceki sürüme dön.", native_build_focused=True).intent is Intent.RELEASE_ROLLBACK
