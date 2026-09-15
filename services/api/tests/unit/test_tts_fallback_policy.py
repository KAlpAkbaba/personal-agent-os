"""B20 req 234: the TTS fallback is a policy, and the policy is tested.

B13 req 267 established the rule with one sentence of code: with no TTS key the offline
fake produces a 110 Hz sine wave, and a sine wave played into a bedroom after an alarm is
a fault that sounds deliberate, so it is refused and the refusal is recorded. The rule was
right. What it was made of was not:

* it recognised the tone by ONE NAME, ``fake-tts-greeting``, while every other fake this
  codebase builds - the registry's ``fake-tts``, the benchmark's ``fake-tts-a`` and
  ``fake-tts-b``, and any name a caller passes - produces the identical tone from the
  identical function and walked straight past the guard;
* and nothing tested that a delivery path consults it at all, so a new one could be
  written without the guard and nothing would say so until the owner heard a buzz.

What is pinned here: the policy answers about the CLASS of provider, both delivery paths
refuse a tone provider through their real entry points, and a new path that synthesises
greeting audio cannot be added without consulting the policy.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.alarms.greeting_audio import (
    FALLBACK_PROVIDER_NAME,
    build_greeting_tts,
    is_fallback_provider,
)
from app.briefing.delivery import tts_synthesise
from app.voice.providers import FakeTTSProvider, OpenAITTSProvider, synthesize_wav
from app.voice.registry import benchmark_tts_candidates, real_tts_providers


class _Settings:
    def __init__(self, key: str = "") -> None:
        self.voice_openai_api_key = key
        self.openai_api_key = key
        self.voice_elevenlabs_api_key = ""
        self.voice_azure_speech_key = ""
        self.voice_azure_speech_region = "westeurope"


class _RealEnough:
    """A provider that speaks: no `synthetic_speech`, and a name nobody special-cases."""

    name = "some-real-tts"

    def synthesize(self, text: str, **_: object) -> object:  # pragma: no cover - trivial
        class _R:
            audio = synthesize_wav(text)
            duration_ms = 100
            fmt = "wav"
            voice = "default"

        return _R()


def test_every_fake_this_codebase_can_build_is_recognised_as_a_tone():
    # The defect: the policy knew one name. These are the fakes that actually exist.
    built = [
        FakeTTSProvider(),
        FakeTTSProvider(name=FALLBACK_PROVIDER_NAME),
        *benchmark_tts_candidates(),
        FakeTTSProvider("a-name-nobody-thought-of"),
        build_greeting_tts(_Settings()),
    ]
    assert [p for p in built if not is_fallback_provider(p)] == []


def test_a_provider_that_actually_speaks_is_not_refused():
    assert is_fallback_provider(_RealEnough()) is False
    assert is_fallback_provider(build_greeting_tts(_Settings("sk-real"))) is False
    assert is_fallback_provider(None) is False


def test_no_real_adapter_declares_itself_synthetic():
    # The guard must not be able to silence the real thing: a keyless real adapter is
    # inert (it raises PROVIDER_AUTH_MISSING when called), which is a different failure
    # and must stay a different failure.
    for provider in real_tts_providers(_Settings()):
        assert is_fallback_provider(provider) is False
    assert is_fallback_provider(OpenAITTSProvider(None)) is False


def test_the_two_kinds_of_tone_provider_produce_the_same_tone():
    # Why the name test was never enough, stated as a measurement rather than as an
    # assertion about intent: the byte streams are identical.
    named = FakeTTSProvider(name=FALLBACK_PROVIDER_NAME).synthesize("Günaydın efendim.")
    other = FakeTTSProvider("fake-tts").synthesize("Günaydın efendim.")
    assert named.audio == other.audio


def test_briefing_refuses_every_tone_provider_through_its_own_entry_point():
    for provider in [FakeTTSProvider("fake-tts"), FakeTTSProvider(name=FALLBACK_PROVIDER_NAME)]:
        assert tts_synthesise(provider, "Bugün üç toplantın var.") is None
    assert tts_synthesise(None, "Bugün üç toplantın var.") is None
    # And it is not refusing everything: a provider that speaks gets through.
    spoken = tts_synthesise(_RealEnough(), "Bugün üç toplantın var.")
    assert spoken is not None and spoken[:4] == b"RIFF"


def test_a_new_delivery_path_cannot_synthesise_greeting_audio_without_the_policy():
    """Every module that synthesises greeting audio also consults the policy.

    Read from the source, because the point is the path that does NOT exist yet. A future
    module that imports `synthesize_greeting` and forgets `is_fallback_provider` is the
    exact regression B13 fixed and B20 widened, and nothing else in the suite would catch
    it - the new path would work perfectly, and buzz.
    """
    root = Path(__file__).resolve().parents[2] / "app"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "greeting_audio.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "synthesize_greeting" not in source:
            continue
        names = {
            node.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Name)
        } | {
            alias.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        if "synthesize_greeting" in names and "is_fallback_provider" not in names:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
