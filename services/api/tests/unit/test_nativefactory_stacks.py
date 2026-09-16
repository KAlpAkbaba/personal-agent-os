"""The stack router: what it chooses, and — mostly — how it refuses.

ADR-0095 decision 3 makes honest classification an OUTPUT rather than an apology, so most
of this suite is about refusals. The distinction every test below defends is the one the
owner actually feels: `unavailable` is a fact about this machine and `failed` is a defect,
and telling them one when the other is true is the failure the milestone exists to prevent.

The toolchain is injected, never measured, in every test but one: a suite whose result
depends on whether a JDK happens to be installed today is a suite that reports the weather.
"""

from __future__ import annotations

import pytest

from app.nativefactory.models import (
    NATIVE_BUILD_STATES,
    STATE_FAILED,
    STATE_UNAVAILABLE,
    TERMINAL_STATES,
)
from app.nativefactory.spec import parse_spec
from app.nativefactory.stacks import (
    DEVICE_BUILDABLE_TARGETS,
    SPEECH_ANDROID_BUILD_NOT_WIRED,
    SPEECH_DEVICE_ANDROID,
    ToolchainFacts,
    choose,
    choose_on_device,
    detect,
    refuse_ios,
)

FULL = ToolchainFacts(
    dotnet=r"C:\Program Files\dotnet\dotnet.exe",
    dotnet_sdk="10.0.400",
    makeappx=r"C:\Windows Kits\makeappx.exe",
    signtool=r"C:\Windows Kits\signtool.exe",
    java=r"C:\Java\bin\java.exe",
    java_home=r"C:\Java",
    android_sdk=r"C:\Android\Sdk",
    aapt2=r"C:\Android\Sdk\build-tools\33.0.0\aapt2.exe",
    macos=False,
)

#: This machine, as measured on 2026-09-09: everything for Windows, the whole Android SDK,
#: and no Java at all.
THIS_MACHINE = ToolchainFacts(**{**FULL.as_dict(), "java": None, "java_home": None})

WINDOWS = {"name": "Notlarim", "template": "notes-desktop", "targets": ["windows_exe"]}
ANDROID = {
    "name": "Sayac",
    "template": "counter-mobile",
    "targets": ["android_apk"],
    "features": ["counter"],
}


def test_windows_is_chosen_with_the_sentence_the_receipt_will_say() -> None:
    choice = choose(parse_spec(WINDOWS), FULL)
    assert choice.available
    assert choice.stack == "dotnet_wpf"
    assert "WPF seçtim" in choice.reason
    assert "10.0.400" in choice.reason  # the ACTUAL sdk, not a hardcoded number
    assert choice.error_class is None


@pytest.mark.parametrize(
    "facts",
    [
        THIS_MACHINE,
        FULL,
        ToolchainFacts(**{**THIS_MACHINE.as_dict(), "android_sdk": None}),
    ],
    ids=["sdk-no-java", "sdk-and-java", "nothing"],
)
def test_this_machine_never_builds_android_whatever_it_has(facts) -> None:
    """ADR-0160/0161. THIS machine's build step is dotnet, so its SDK and its Java change
    nothing: with both present the row used to be `planned` and native.build handed
    app/build.gradle.kts to `dotnet build` (2026-09-16). Android is built on the device."""
    choice = choose(parse_spec(ANDROID), facts)
    assert not choice.available
    assert choice.stack == "android_kotlin"
    assert choice.error_class == "dependency_unavailable"
    assert choice.owner_action is None, "installing something HERE is not what would fix this"
    assert choice.reason == SPEECH_ANDROID_BUILD_NOT_WIRED
    assert "cihazınız" in choice.reason
    assert "aynı hat çalışır" not in choice.reason


def test_the_device_plans_android_and_leaves_its_toolchain_to_the_device() -> None:
    """The device builds it (three Gradle shapes); whether it HAS Java and Gradle is the
    device's own answer, in its own words, at project.run."""
    for target in ("android_apk", "android_aab"):
        choice = choose_on_device(parse_spec({**ANDROID, "targets": [target]}))
        assert choice.available
        assert choice.stack == "android_kotlin"
        assert choice.reason == SPEECH_DEVICE_ANDROID
    assert {"android_apk", "android_aab"} <= DEVICE_BUILDABLE_TARGETS


def test_msix_is_refused_when_makeappx_is_absent_but_the_exe_is_not() -> None:
    facts = ToolchainFacts(**{**FULL.as_dict(), "makeappx": None})
    msix = choose(parse_spec({**WINDOWS, "targets": ["windows_msix"]}), facts)
    assert not msix.available
    assert "makeappx" in msix.reason
    # ...and the plain EXE is still fine, because the two are different capabilities.
    assert choose(parse_spec(WINDOWS), facts).available


def test_no_dotnet_means_no_windows_build_and_no_owner_item_to_offer() -> None:
    facts = ToolchainFacts(**{**FULL.as_dict(), "dotnet": None, "dotnet_sdk": None})
    choice = choose(parse_spec(WINDOWS), facts)
    assert not choice.available
    assert ".NET SDK" in choice.reason


@pytest.mark.parametrize(
    "request_text",
    ["iPhone için de yap", "iOS sürümü lazım", "App Store'a koyalım", "iPad uygulaması"],
)
def test_an_ios_request_is_refused_as_itself(request_text: str) -> None:
    """Not as a validation error about an unknown target - as the sentence it deserves."""
    choice = refuse_ios(request_text)
    assert choice is not None
    assert not choice.available
    assert choice.error_class == "platform_unreachable"
    assert "macOS" in choice.reason


def test_ios_offers_no_owner_action_because_none_exists() -> None:
    """No licence, no download and no credential turns a Windows machine into a Mac.
    Offering an action here would be a hopeful sentence, which is what this milestone is
    written to avoid."""
    assert refuse_ios("iOS sürümü").owner_action is None


def test_a_request_that_is_not_about_ios_falls_through() -> None:
    assert refuse_ios("Windows için masaüstü uygulaması yap") is None
    assert refuse_ios("Android sürümünü de çıkar") is None


def test_detect_measures_and_changes_nothing() -> None:
    """The one test that touches the real machine. It asserts SHAPE, not contents, so it
    says the same thing on the owner's PC and on a CI runner with no Android SDK."""
    facts = detect()
    for key, value in facts.as_dict().items():
        assert value is None or isinstance(value, (str, bool)), key


# ------------------------------------------------------------------ the state vocabulary


def test_unavailable_and_failed_are_different_states() -> None:
    """The distinction the whole milestone's honesty rests on, asserted rather than
    assumed: a machine that cannot build something has not failed at it."""
    assert STATE_UNAVAILABLE != STATE_FAILED
    assert STATE_UNAVAILABLE in NATIVE_BUILD_STATES
    assert STATE_FAILED in NATIVE_BUILD_STATES
    assert {STATE_UNAVAILABLE, STATE_FAILED} <= TERMINAL_STATES


def test_every_terminal_state_is_a_real_state() -> None:
    assert TERMINAL_STATES <= set(NATIVE_BUILD_STATES)
