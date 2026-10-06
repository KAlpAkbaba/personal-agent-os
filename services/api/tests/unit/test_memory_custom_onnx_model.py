"""The closed registry of ONNX models fastembed does not ship (memory-embedding-granite-measure).

IBM Granite Embedding Multilingual R2 is MEASURED beside potion, not adopted: the default
stays ``minishlab/potion-multilingual-128M`` and ``build_embedder`` behaves as before. What
these tests pin:

* The two Granite names (FP32 and the INT8 alias) are Matryoshka-truncatable to the frozen
  256 column with the ``model_id`` scheme ``LocalEmbedder`` already uses; the small 384-wide
  Granite R2 is NOT on the allowlist and is refused.
* A registry name is registered with fastembed ONCE, with the registry's pooling and width;
  a name outside the registry reaches ``TextEmbedding`` with exactly today's kwargs.
* Every pinned file is checked (size and sha256) BEFORE the first embedding; a mismatch is an
  ``EmbeddingProviderError`` naming the file - never its content, never a vector.
* When fastembed cannot load it, the onnxruntime fallback pools exactly as the registry says.

Everything is a fake: no network, no model download, no onnxruntime session.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import sys
import types
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.memory import providers
from app.memory.providers import (
    CUSTOM_ONNX_MODELS,
    DEFAULT_LOCAL_MODEL,
    MRL_TRUNCATABLE_MODELS,
    EmbeddingProviderError,
    LocalEmbedder,
    build_embedder,
)

GRANITE = "ibm-granite/granite-embedding-311m-multilingual-r2"
GRANITE_INT8 = "ibm-granite/granite-embedding-311m-multilingual-r2-int8"
GRANITE_SMALL = "ibm-granite/granite-embedding-97m-multilingual-r2"
SECRET_BYTES = b"tokenizer-content-that-must-never-appear-in-an-error"


class _FakeModel:
    def __init__(self, width: int) -> None:
        self.width = width
        self.calls = 0

    def embed(self, documents: list[str], **_kwargs: Any) -> Iterable[list[float]]:
        for text in documents:
            self.calls += 1
            seed = sum(ord(ch) for ch in text) or 1
            yield [float((seed * (i + 1)) % 7 + 1) for i in range(self.width)]


def _fixed(width: int):
    def factory(model_name: str, cache_dir: str | None) -> _FakeModel:
        return _FakeModel(width)

    return factory


# ------------------------------------------------------------- the allowlist and the width


def test_granite_fp32_768_wide_is_truncated_to_256_and_renormalised():
    embedder = LocalEmbedder(model_name=GRANITE, model_factory=_fixed(768))
    assert embedder.native_dim == 768
    assert embedder.truncated is True
    assert embedder.model_id == "local-ibm-granite/granite-embedding-311m-multilingual-r2@256"
    vector = embedder.embed("Sahip sabahları kahve içmeyi sever")
    assert len(vector) == 256
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-9)


def test_granite_int8_alias_is_truncated_with_its_own_model_id():
    embedder = LocalEmbedder(model_name=GRANITE_INT8, model_factory=_fixed(768))
    assert embedder.truncated is True
    assert (
        embedder.model_id == "local-ibm-granite/granite-embedding-311m-multilingual-r2-int8@256"
    )
    assert len(embedder.embed("Ekranlar 15 dakika sonra kapansın")) == 256


def test_small_granite_384_wide_is_not_on_the_allowlist_and_is_refused():
    assert GRANITE_SMALL not in MRL_TRUNCATABLE_MODELS
    assert GRANITE_SMALL not in CUSTOM_ONNX_MODELS
    with pytest.raises(EmbeddingProviderError) as raised:
        LocalEmbedder(model_name=GRANITE_SMALL, model_factory=_fixed(384))
    assert "allowlist" in str(raised.value)


def test_the_registry_holds_exactly_the_two_granite_names_pinned_from_one_card():
    assert set(CUSTOM_ONNX_MODELS) == {GRANITE, GRANITE_INT8}
    fp32, int8 = CUSTOM_ONNX_MODELS[GRANITE], CUSTOM_ONNX_MODELS[GRANITE_INT8]
    assert fp32.hf_repo == int8.hf_repo == GRANITE
    assert fp32.revision == int8.revision
    assert len(fp32.revision) == 40 and all(c in "0123456789abcdef" for c in fp32.revision)
    assert fp32.model_file == "onnx/model.onnx"
    assert int8.model_file == "onnx/model_quint8_avx2.onnx"
    for spec in (fp32, int8):
        assert spec.pooling == "cls"
        assert spec.native_dim == 768
        assert spec.normalize is True
        paths = [f.path for f in spec.files]
        assert spec.model_file in paths and "tokenizer.json" in paths
        for pinned in spec.files:
            assert len(pinned.sha256) == 64 and pinned.size > 0
        assert spec.name in MRL_TRUNCATABLE_MODELS
    with pytest.raises(TypeError):
        CUSTOM_ONNX_MODELS["x"] = fp32  # type: ignore[index]


def test_the_default_is_still_potion_and_auto_without_a_key_never_loads_a_model():
    assert DEFAULT_LOCAL_MODEL == "minishlab/potion-multilingual-128M"
    loads: list[str] = []

    def recording(model_name: str, cache_dir: str | None) -> _FakeModel:
        loads.append(model_name)
        return _FakeModel(256)

    settings = Settings(
        memory_embedding_provider="auto", openai_api_key="", voice_openai_api_key=""
    )
    _embedder, report = build_embedder(settings, model_factory=recording)
    assert report.active == "deterministic"
    assert loads == []


# ------------------------------------------------------------- the fastembed seam (fakes)


class _FastembedRecorder:
    def __init__(self, *, fail_custom: bool = False) -> None:
        self.added: list[dict[str, Any]] = []
        self.constructed: list[dict[str, Any]] = []
        self.embeds = 0
        self.fail_custom = fail_custom


def _install_fastembed(monkeypatch, recorder: _FastembedRecorder) -> None:
    root = types.ModuleType("fastembed")
    common = types.ModuleType("fastembed.common")
    description = types.ModuleType("fastembed.common.model_description")

    class PoolingType:
        CLS = "CLS"
        MEAN = "MEAN"

    @dataclasses.dataclass
    class ModelSource:
        hf: str | None = None
        url: str | None = None

    class TextEmbedding:
        @classmethod
        def add_custom_model(cls, model: str, **kwargs: Any) -> None:
            recorder.added.append({"model": model, **kwargs})

        def __init__(self, **kwargs: Any) -> None:
            recorder.constructed.append(kwargs)
            if recorder.fail_custom and kwargs.get("model_name") in CUSTOM_ONNX_MODELS:
                raise RuntimeError("fastembed cannot read this graph")

        def embed(self, documents: list[str], **_kwargs: Any) -> Iterable[list[float]]:
            for _ in documents:
                recorder.embeds += 1
                yield [1.0] * 768

    description.PoolingType = PoolingType
    description.ModelSource = ModelSource
    root.TextEmbedding = TextEmbedding
    root.common = common
    common.model_description = description
    monkeypatch.setitem(sys.modules, "fastembed", root)
    monkeypatch.setitem(sys.modules, "fastembed.common", common)
    monkeypatch.setitem(sys.modules, "fastembed.common.model_description", description)
    monkeypatch.setattr(providers, "_REGISTERED_CUSTOM", set())


def _pinned_tree(tmp_path: Path, spec, monkeypatch, *, corrupt: str | None = None) -> list[dict]:
    """Write fake bytes for every file of ``spec``, pin the registry to their hashes, and put
    a fake ``huggingface_hub`` in place that answers the tree. ``corrupt`` names a file whose
    bytes then change after pinning."""
    snapshot = tmp_path / "snapshot"
    files = []
    for pinned in spec.files:
        data = SECRET_BYTES if pinned.path == "tokenizer.json" else pinned.path.encode() * 3
        target = snapshot / pinned.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(
            providers.PinnedFile(pinned.path, hashlib.sha256(data).hexdigest(), len(data))
        )
    if corrupt is not None:
        target = snapshot / corrupt
        data = target.read_bytes()
        target.write_bytes(data[:-1] + bytes([data[-1] ^ 0x01]))  # same size, other hash
    pinned_spec = dataclasses.replace(spec, files=tuple(files))
    registry = dict(CUSTOM_ONNX_MODELS)
    registry[spec.name] = pinned_spec
    monkeypatch.setattr(providers, "CUSTOM_ONNX_MODELS", types.MappingProxyType(registry))
    downloads: list[dict] = []
    hub = types.ModuleType("huggingface_hub")

    def snapshot_download(**kwargs: Any) -> str:
        downloads.append(kwargs)
        return str(snapshot)

    hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    return downloads


def test_a_registry_name_is_registered_once_with_the_registrys_pooling_and_width(
    tmp_path, monkeypatch
):
    recorder = _FastembedRecorder()
    _install_fastembed(monkeypatch, recorder)
    spec = CUSTOM_ONNX_MODELS[GRANITE]
    downloads = _pinned_tree(tmp_path, spec, monkeypatch)
    providers._fastembed_factory(GRANITE, str(tmp_path / "cache"))
    providers._fastembed_factory(GRANITE, str(tmp_path / "cache"))
    assert len(recorder.added) == 1
    added = recorder.added[0]
    assert added["model"] == GRANITE
    assert added["pooling"] == "CLS"
    assert added["dim"] == 768
    assert added["normalization"] is True
    assert added["model_file"] == "onnx/model.onnx"
    assert added["sources"].hf == GRANITE
    assert len(recorder.constructed) == 2
    for kwargs in recorder.constructed:
        assert kwargs["model_name"] == GRANITE
        assert kwargs["cache_dir"] == str(tmp_path / "cache")
        assert kwargs["specific_model_path"] == str(tmp_path / "snapshot")
    # The download is at the PINNED revision into the cache the caller named.
    assert downloads[0]["revision"] == spec.revision
    assert downloads[0]["repo_id"] == GRANITE
    assert downloads[0]["cache_dir"] == str(tmp_path / "cache")
    assert sorted(downloads[0]["allow_patterns"]) == sorted(f.path for f in spec.files)


def test_a_name_outside_the_registry_reaches_textembedding_exactly_as_today(monkeypatch):
    recorder = _FastembedRecorder()
    _install_fastembed(monkeypatch, recorder)
    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = lambda **_k: pytest.fail("potion must not take the pinned path")
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    providers._fastembed_factory(DEFAULT_LOCAL_MODEL, "C:/cache")
    providers._fastembed_factory(DEFAULT_LOCAL_MODEL, None)
    assert recorder.added == []
    assert recorder.constructed == [
        {"model_name": DEFAULT_LOCAL_MODEL, "cache_dir": "C:/cache"},
        {"model_name": DEFAULT_LOCAL_MODEL},
    ]


def test_threads_reach_textembedding_only_when_given(monkeypatch):
    recorder = _FastembedRecorder()
    _install_fastembed(monkeypatch, recorder)
    providers._fastembed_factory(DEFAULT_LOCAL_MODEL, None, threads=4)
    assert recorder.constructed == [{"model_name": DEFAULT_LOCAL_MODEL, "threads": 4}]


@pytest.mark.parametrize("corrupt", ["tokenizer.json", "onnx/model.onnx", "config.json"])
def test_a_hash_mismatch_refuses_before_any_embedding_and_names_only_the_file(
    tmp_path, monkeypatch, corrupt
):
    recorder = _FastembedRecorder()
    _install_fastembed(monkeypatch, recorder)
    _pinned_tree(tmp_path, CUSTOM_ONNX_MODELS[GRANITE], monkeypatch, corrupt=corrupt)
    with pytest.raises(EmbeddingProviderError) as raised:
        LocalEmbedder(model_name=GRANITE, cache_dir=str(tmp_path / "cache"))
    message = str(raised.value)
    assert corrupt in message
    assert "sha256" in message
    assert SECRET_BYTES.decode() not in message
    assert "[" not in message  # no vector, no list of numbers
    assert recorder.constructed == []  # nothing was loaded ...
    assert recorder.embeds == 0  # ... and nothing was embedded


def test_a_size_mismatch_is_refused_the_same_way(tmp_path, monkeypatch):
    recorder = _FastembedRecorder()
    _install_fastembed(monkeypatch, recorder)
    _pinned_tree(tmp_path, CUSTOM_ONNX_MODELS[GRANITE_INT8], monkeypatch)
    (tmp_path / "snapshot" / "tokenizer.json").write_bytes(SECRET_BYTES + b"!")
    with pytest.raises(EmbeddingProviderError) as raised:
        providers._fastembed_factory(GRANITE_INT8, None)
    assert "tokenizer.json" in str(raised.value)
    assert SECRET_BYTES.decode() not in str(raised.value)
    assert recorder.constructed == [] and recorder.embeds == 0


# ------------------------------------------------------------- the onnxruntime fallback


class _Encoding:
    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        self.attention_mask = [1] * len(ids)


def _install_onnxruntime(monkeypatch, hidden: list[list[list[float]]]) -> dict[str, Any]:
    seen: dict[str, Any] = {"feeds": []}
    ort = types.ModuleType("onnxruntime")

    class SessionOptions:
        intra_op_num_threads = 0

    class _Input:
        def __init__(self, name: str) -> None:
            self.name = name

    class InferenceSession:
        def __init__(self, path: str, sess_options=None, providers=None) -> None:
            seen["path"] = path
            seen["providers"] = providers
            seen["threads"] = sess_options.intra_op_num_threads
            seen["runs"] = 0

        def get_inputs(self):
            return [_Input("input_ids"), _Input("attention_mask")]

        def run(self, output_names, feed):
            seen["runs"] += 1
            seen["feeds"].append({k: v.tolist() for k, v in feed.items()})
            return [hidden]

    ort.SessionOptions = SessionOptions
    ort.InferenceSession = InferenceSession
    tok = types.ModuleType("tokenizers")

    class Tokenizer:
        @staticmethod
        def from_file(path: str) -> Tokenizer:
            seen["tokenizer"] = path
            return Tokenizer()

        def encode_batch(self, texts: list[str]) -> list[_Encoding]:
            return [_Encoding([2, 5]) if t == "iki" else _Encoding([2]) for t in texts]

    tok.Tokenizer = Tokenizer
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "tokenizers", tok)
    return seen


#: A hand-built batch: "iki" is two tokens, "bir" one token plus one pad position whose
#: hidden state (9, 9) must never reach a mean.
HIDDEN = [[[1.0, 2.0], [3.0, 4.0]], [[5.0, 6.0], [9.0, 9.0]]]


@pytest.mark.parametrize(
    ("pooling", "normalize", "expected"),
    [
        ("cls", False, [[1.0, 2.0], [5.0, 6.0]]),
        ("mean", False, [[2.0, 3.0], [5.0, 6.0]]),
        ("cls", True, [[0.4472135955, 0.894427191], [0.6401843997, 0.7682212796]]),
    ],
)
def test_when_fastembed_cannot_load_it_the_onnxruntime_fallback_pools_as_the_registry_says(
    tmp_path, monkeypatch, pooling, normalize, expected
):
    recorder = _FastembedRecorder(fail_custom=True)
    _install_fastembed(monkeypatch, recorder)
    seen = _install_onnxruntime(monkeypatch, HIDDEN)
    spec = dataclasses.replace(CUSTOM_ONNX_MODELS[GRANITE], pooling=pooling, normalize=normalize)
    registry = dict(CUSTOM_ONNX_MODELS)
    registry[GRANITE] = spec
    monkeypatch.setattr(providers, "CUSTOM_ONNX_MODELS", types.MappingProxyType(registry))
    _pinned_tree(tmp_path, spec, monkeypatch)
    model = providers._fastembed_factory(GRANITE, None, threads=3)
    assert type(model).__name__ == "_OnnxRuntimeModel"
    vectors = [list(v) for v in model.embed(["iki", "bir"])]
    assert vectors == [pytest.approx(row, abs=1e-9) for row in expected]
    assert seen["providers"] == ["CPUExecutionProvider"]
    assert seen["threads"] == 3
    assert seen["path"] == str(tmp_path / "snapshot" / "onnx" / "model.onnx")
    assert seen["feeds"] == [{"input_ids": [[2, 5], [2, 0]], "attention_mask": [[1, 1], [1, 0]]}]


def test_the_fallback_hash_check_also_runs_before_the_session(tmp_path, monkeypatch):
    recorder = _FastembedRecorder(fail_custom=True)
    _install_fastembed(monkeypatch, recorder)
    seen = _install_onnxruntime(monkeypatch, HIDDEN)
    _pinned_tree(tmp_path, CUSTOM_ONNX_MODELS[GRANITE], monkeypatch, corrupt="onnx/model.onnx")
    with pytest.raises(EmbeddingProviderError) as raised:
        providers._fastembed_factory(GRANITE, None)
    assert "onnx/model.onnx" in str(raised.value)
    assert "path" not in seen and "runs" not in seen
