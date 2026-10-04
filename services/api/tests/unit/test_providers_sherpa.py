"""Unit tests: the on-device Turkish recogniser for MEASUREMENT (local-tr-stt-measure).

No real ``sherpa_onnx`` and no real model: a fake module is put into ``sys.modules`` (and
``importlib.util.find_spec`` is told about it) and the four model files are small fakes in a
temporary folder whose sha256 the test pins in place of the real ones. No network.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import io
import sys
import types
import wave
from pathlib import Path
from typing import Any

import pytest

from app.voice import providers_sherpa as ps
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import STTResult


def _wav(seconds: float = 1.2, rate: int = 16_000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x10\x00" * int(seconds * rate) * channels)
    return buffer.getvalue()


class _FakeStream:
    def __init__(self) -> None:
        self.samples = 0
        self.finished = False

    def accept_waveform(self, sample_rate: int, waveform: Any) -> None:
        assert sample_rate == 16_000
        self.samples += len(waveform)

    def input_finished(self) -> None:
        self.finished = True


class _FakeRecognizer:
    def __init__(self, text: str) -> None:
        self._text = text
        self.stream: _FakeStream | None = None

    def create_stream(self) -> _FakeStream:
        self.stream = _FakeStream()
        return self.stream

    def is_ready(self, stream: _FakeStream) -> bool:
        return False

    def decode_stream(self, stream: _FakeStream) -> None:  # pragma: no cover - never ready
        pass

    def get_result(self, stream: _FakeStream) -> str:
        assert stream.finished, "the tail padding and input_finished() come before the result"
        return self._text


class _FakeOnlineRecognizer:
    built: list[dict[str, Any]] = []
    text = " alarmı kapat "

    @classmethod
    def from_transducer(cls, **kwargs: Any) -> _FakeRecognizer:
        cls.built.append(kwargs)
        return _FakeRecognizer(cls.text)


@pytest.fixture
def fake_sherpa(monkeypatch: pytest.MonkeyPatch) -> type[_FakeOnlineRecognizer]:
    module = types.ModuleType("sherpa_onnx")
    module.OnlineRecognizer = _FakeOnlineRecognizer  # type: ignore[attr-defined]
    _FakeOnlineRecognizer.built = []
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    real_find_spec = importlib.util.find_spec

    def find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sherpa_onnx":
            return importlib.machinery.ModuleSpec("sherpa_onnx", None)
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    return _FakeOnlineRecognizer


@pytest.fixture
def no_sherpa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)  # an import raises ImportError
    real_find_spec = importlib.util.find_spec

    def find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        return None if name == "sherpa_onnx" else real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)


@pytest.fixture
def model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Four fake model files whose hashes replace the pinned ones for this test."""
    folder = tmp_path / "model"
    folder.mkdir()
    pinned: dict[str, str] = {}
    for name in ps.MODEL_FILES:
        content = f"fake {name}".encode()
        (folder / name).write_bytes(content)
        pinned[name] = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(ps, "MODEL_FILES", pinned)
    monkeypatch.setenv(ps.MODEL_DIR_ENV, str(folder))
    return folder


# --------------------------------------------------------------- the pins


def test_the_pins_are_the_integrators_four_files_at_one_commit() -> None:
    assert ps.MODEL_COMMIT == "cebec199e0a9a1bdcd195b9124922b4a33e98bc8"
    assert ps.MODEL_FILES == {
        "encoder-epoch-1-avg-1-chunk-32-left-128.int8.onnx": (
            "f5a4b439cf0dd11d01774a97fd580c20ad944756c7e44f539cb6a46165013432"
        ),
        "decoder-epoch-1-avg-1-chunk-32-left-128.int8.onnx": (
            "28f4caee9c57dc22afc0967b7e81994b85c0b67f9980f1df0bb5648a7554b18b"
        ),
        "joiner-epoch-1-avg-1-chunk-32-left-128.int8.onnx": (
            "c1f4ebed5cbbed2fceefabca8c2657f157bcf2deead3ad067c8b2dc1d2c3bfb1"
        ),
        "tokens.txt": "1885ade359d5939dde820f0f0a1b31f43dad6e5bf8362ad66983fb78d24d65b6",
    }
    assert ps.model_url("tokens.txt") == (
        "https://huggingface.co/duxx/turkish-stt-zipformer/resolve/"
        "cebec199e0a9a1bdcd195b9124922b4a33e98bc8/tokens.txt"
    )


def test_the_model_folder_is_under_local_app_data_unless_the_variable_names_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(ps.MODEL_DIR_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert ps.model_dir() == tmp_path / "PagentOS" / "models" / ps.MODEL_DIR_NAME
    monkeypatch.setenv(ps.MODEL_DIR_ENV, str(tmp_path / "elsewhere"))
    assert ps.model_dir() == tmp_path / "elsewhere"


# ------------------------------------------------------- (a) no package


def test_a_without_the_package_it_is_unavailable_and_transcribe_says_optional_missing(
    no_sherpa: None, model_dir: Path
) -> None:
    provider = ps.SherpaOnnxSTTProvider()
    assert provider.available() is False
    assert provider.status().reason == ps.REASON_NOT_INSTALLED
    with pytest.raises(VoiceError) as caught:
        provider.transcribe(_wav())
    assert caught.value.error_class is VoiceErrorClass.OPTIONAL_DEPENDENCY_MISSING
    assert caught.value.provider == "sherpa-onnx"


# --------------------------------------------------- (b) a model file missing


def test_b_a_missing_model_file_is_not_loaded_and_the_reason_says_model_missing(
    fake_sherpa: type[_FakeOnlineRecognizer], model_dir: Path
) -> None:
    (model_dir / "tokens.txt").unlink()
    provider = ps.SherpaOnnxSTTProvider()
    status = provider.status()
    assert (status.ok, status.reason) == (False, ps.REASON_MODEL_MISSING)
    assert "model eksik" in status.detail_tr and "tokens.txt" in status.detail_tr
    assert provider.available() is False
    with pytest.raises(VoiceError) as caught:
        provider.transcribe(_wav())
    assert "model eksik" in caught.value.message
    assert fake_sherpa.built == []


# ------------------------------------------------------ (c) a hash that differs


def test_c_a_file_whose_sha256_differs_is_never_loaded(
    fake_sherpa: type[_FakeOnlineRecognizer], model_dir: Path
) -> None:
    encoder = next(name for name in ps.MODEL_FILES if name.startswith("encoder"))
    (model_dir / encoder).write_bytes(b"a different, perhaps hostile, onnx graph")
    provider = ps.SherpaOnnxSTTProvider()
    status = provider.status()
    assert (status.ok, status.reason) == (False, ps.REASON_MODEL_HASH)
    assert "hash" in status.detail_tr and encoder in status.detail_tr
    assert provider.available() is False
    with pytest.raises(VoiceError) as caught:
        provider.transcribe(_wav())
    assert "hash" in caught.value.message
    assert fake_sherpa.built == []  # the recogniser was never built from that file


# ------------------------------------------------------------ (d) all correct


def test_d_with_the_package_and_a_verified_model_the_text_comes_back(
    fake_sherpa: type[_FakeOnlineRecognizer], model_dir: Path
) -> None:
    provider = ps.SherpaOnnxSTTProvider()
    assert provider.status().ok
    result = provider.transcribe(_wav(seconds=1.2), language="tr-TR")
    assert isinstance(result, STTResult)
    assert (result.text, result.provider, result.language) == (
        "alarmı kapat",
        "sherpa-onnx",
        "tr-TR",
    )
    [built] = fake_sherpa.built
    assert built["encoder"] == str(model_dir / "encoder-epoch-1-avg-1-chunk-32-left-128.int8.onnx")
    assert built["tokens"] == str(model_dir / "tokens.txt")
    assert built["decoding_method"] == "modified_beam_search"
    # the second call reuses the loaded recogniser
    provider.transcribe(_wav())
    assert len(fake_sherpa.built) == 1


def test_d_the_whole_file_and_one_second_of_padding_reach_the_stream(
    fake_sherpa: type[_FakeOnlineRecognizer], model_dir: Path
) -> None:
    provider = ps.SherpaOnnxSTTProvider()
    provider.transcribe(_wav(seconds=1.2))
    recognizer = provider._recognizer
    assert recognizer is not None and recognizer.stream is not None
    assert recognizer.stream.samples == int(1.2 * 16_000) + 16_000


def test_d_a_result_object_with_a_text_field_is_read_too(
    fake_sherpa: type[_FakeOnlineRecognizer], model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _FakeRecognizer, "get_result", lambda self, stream: types.SimpleNamespace(text="saat kaç")
    )
    assert ps.SherpaOnnxSTTProvider().transcribe(_wav()).text == "saat kaç"


def test_a_wav_that_is_not_16k_mono_is_refused_not_resampled(
    fake_sherpa: type[_FakeOnlineRecognizer], model_dir: Path
) -> None:
    provider = ps.SherpaOnnxSTTProvider()
    for audio in (_wav(rate=8_000), _wav(channels=2), b"not a wav at all"):
        with pytest.raises(VoiceError) as caught:
            provider.transcribe(audio)
        assert caught.value.error_class is VoiceErrorClass.VALIDATION_ERROR


# --------------------------------------------- (e) available() loads nothing


def test_e_available_never_builds_the_recogniser(
    fake_sherpa: type[_FakeOnlineRecognizer], model_dir: Path
) -> None:
    provider = ps.SherpaOnnxSTTProvider()
    for _ in range(3):
        assert provider.available() is True
    assert fake_sherpa.built == []
    assert provider._recognizer is None


def test_capabilities_say_local_streaming_turkish_and_free() -> None:
    caps = ps.SherpaOnnxSTTProvider().capabilities()
    assert (caps.name, caps.kind, caps.streaming, caps.requires_api_key) == (
        "sherpa-onnx",
        "stt",
        True,
        False,
    )
    assert caps.languages == ("tr-TR",)
    assert caps.cost_metadata["usd"] == 0.0


# ------------------------------------------------------------ the download


class _Body(io.BytesIO):
    def __enter__(self) -> _Body:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_the_download_verifies_each_file_and_leaves_no_half_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = {name: f"fake {name}".encode() for name in ps.MODEL_FILES}
    monkeypatch.setattr(
        ps, "MODEL_FILES", {n: hashlib.sha256(c).hexdigest() for n, c in good.items()}
    )
    served = dict(good)
    seen: list[str] = []

    def opener(url: str, timeout: float) -> _Body:
        name = url.rsplit("/", 1)[-1]
        seen.append(url)
        return _Body(served[name])

    target = tmp_path / "m"
    assert ps.download_model(target, opener=opener) == target
    assert {p.name: p.read_bytes() for p in target.iterdir()} == good
    assert all(ps.MODEL_COMMIT in url for url in seen)

    # a tampered file: refused, nothing half-written beside the good ones
    second = tmp_path / "n"
    served["tokens.txt"] = b"tampered"
    with pytest.raises(VoiceError, match="hash"):
        ps.download_model(second, opener=opener)
    assert "tokens.txt" not in {p.name for p in second.iterdir()}
    assert not any(p.name.endswith(".part") for p in second.iterdir())
