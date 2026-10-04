"""Unit tests: ``app.voice.tts_measure`` - the pure half of the FreyaTTS measurement
(tts-freya-measure; team/plans/tts-freya-measure-adr.md).

Every expected number below is a LITERAL computed by hand from the synth_ms / audio_ms the
test chooses - never produced by the code under test.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.voice import tts_measure
from app.voice.stt_compare import OWNER_SENTENCES

REPO = Path(__file__).resolve().parents[4]
SYNTHESIZE = REPO / "tools" / "tts-measure" / "synthesize.py"

QUALITY_CLAIMS = ("iyi", "kaliteli", "doğal", "güzel", "net ", "anlaşılır", "kötü", "berbat")


def _line(index: int, synth_ms: float, audio_ms: float, **extra: Any) -> str:
    row: dict[str, Any] = {
        "index": index,
        "chars": 10 * index,
        "audio_ms": audio_ms,
        "synth_ms": synth_ms,
        "first_audio_ms": synth_ms,
        "streamed": False,
        "peak_rss_mb": 1000.0 + index,
        "retries": 0,
    }
    row.update(extra)
    return json.dumps(row)


LOAD = json.dumps(
    {"event": "load", "load_ms": 99999.0, "peak_rss_mb": 900.0, "cpu": "Test CPU", "threads": 4}
)

#: synth / audio -> RTF 0.5, 0.9, 1.2, 0.25. Pooled = 4800 / 8000 = 0.6 (the MEAN would be 0.7125).
FOUR = [
    LOAD,
    _line(1, 500, 1000),
    _line(2, 900, 1000),
    _line(3, 2400, 2000),
    _line(4, 1000, 4000),
]


def _machine(lines: list[str], indices: range = range(1, 5), **kwargs: Any) -> dict[str, Any]:
    run = tts_measure.parse_container_output(lines, indices)
    defaults: dict[str, Any] = {
        "label": "ev-pc",
        "threads": 4,
        "cpus_limit": None,
        "wav_dir": "C:/Users/owner/AppData/Local/PagentOS/tts-measure/ev-pc",
    }
    defaults.update(kwargs)
    return tts_measure.summarize_machine(run, **defaults)


def test_sentences_are_owner_sentences_in_order() -> None:
    assert tts_measure.sentences() is OWNER_SENTENCES
    rows = [json.loads(line) for line in tts_measure.input_lines()]
    assert [row["index"] for row in rows] == list(range(1, len(OWNER_SENTENCES) + 1))
    assert tuple(row["text"] for row in rows) == OWNER_SENTENCES
    assert len(rows) == 20


def test_rates_equal_hand_computed_literals() -> None:
    machine = _machine(FOUR)
    assert [row["rtf"] for row in machine["sentences"]] == [0.5, 0.9, 1.2, 0.25]
    assert machine["rtf_pooled"] == 0.6
    assert machine["rtf_p50"] == 0.5
    assert machine["rtf_p95"] == 1.2
    assert machine["first_audio_ms_p50"] == 900.0
    assert machine["first_audio_ms_p95"] == 2400.0
    assert machine["sentences_ok"] == 4
    assert machine["sentences_failed"] == 0
    assert machine["peak_rss_mb"] == 1004.0


def test_malformed_line_is_a_failed_sentence_excluded_from_rates() -> None:
    lines = [LOAD, _line(1, 500, 1000), _line(2, 900, 1000), "{bozuk", _line(4, 1000, 4000)]
    machine = _machine(lines)
    assert machine["sentences_ok"] == 3
    assert machine["sentences_failed"] == 1
    [failure] = machine["failures"]
    assert failure["index"] == 3
    assert "bozuk" in failure["reason"]
    # 2400 / 6000 over the three good ones
    assert machine["rtf_pooled"] == 0.4


def test_line_with_bad_field_is_a_failed_sentence_with_reason() -> None:
    lines = [LOAD, _line(1, 500, 1000), _line(2, "yok", 1000)]
    machine = _machine(lines, range(1, 3))
    [failure] = machine["failures"]
    assert failure["index"] == 2
    assert "synth_ms" in failure["reason"]
    assert machine["rtf_pooled"] == 0.5


def test_missing_index_is_a_failed_sentence_excluded_from_rates() -> None:
    lines = [LOAD, _line(1, 500, 1000), _line(2, 900, 1000), _line(3, 2400, 2000)]
    machine = _machine(lines)
    assert machine["sentences_ok"] == 3
    [failure] = machine["failures"]
    assert failure["index"] == 4
    assert "satırı yok" in failure["reason"]
    # 3800 / 4000
    assert machine["rtf_pooled"] == 0.95


def test_container_error_line_is_a_failed_sentence() -> None:
    lines = [LOAD, _line(1, 500, 1000), json.dumps({"index": 2, "error": "RuntimeError: x"})]
    machine = _machine(lines, range(1, 3))
    [failure] = machine["failures"]
    assert failure == {"index": 2, "reason": "RuntimeError: x"}


def test_load_time_is_not_inside_any_sentence() -> None:
    machine = _machine(FOUR)
    assert machine["load_ms"] == 99999.0
    for row in machine["sentences"]:
        assert row["synth_ms"] < 99999.0
        assert row["first_audio_ms"] < 99999.0
    assert machine["first_audio_ms_p95"] == 2400.0
    assert machine["rtf_pooled"] == 0.6


def test_streamed_false_makes_first_audio_equal_synth_and_md_says_so(tmp_path: Path) -> None:
    lines = [LOAD, _line(1, 500, 1000, first_audio_ms=7, streamed=False)]
    machine = _machine(lines, range(1, 2))
    assert machine["sentences"][0]["first_audio_ms"] == 500.0
    assert machine["streamed"] is False
    report = tts_measure.merge_report(None, machine)
    text = tts_measure.render_markdown(report, tmp_path / "repo")
    assert "streamed: false" in text
    assert "ilk ses gecikmesi = tüm sentez süresi" in text


def test_verdict_says_real_time_from_pooled_rtf_and_claims_nothing_on_quality() -> None:
    fast = _machine([LOAD, _line(1, 800, 1000)], range(1, 2), label="ev-pc")
    slow = _machine([LOAD, _line(1, 1300, 1000)], range(1, 2), label="cpx32-bicimi")
    assert fast["rtf_pooled"] == 0.8
    assert slow["rtf_pooled"] == 1.3
    verdict = tts_measure.verdict_tr([fast, slow])
    assert "ev-pc: gerçek zamana ulaşıldı" in verdict
    assert "cpx32-bicimi: gerçek zamana ulaşılamadı" in verdict
    assert "ses kalitesi burada değerlendirilmez" in verdict.lower()
    lowered = verdict.lower()
    for claim in QUALITY_CLAIMS:
        assert claim not in lowered, claim


def test_merge_keeps_other_labels_and_replaces_a_rerun_label() -> None:
    first = _machine(FOUR, label="ev-pc")
    second = _machine([LOAD, _line(1, 1300, 1000)], range(1, 2), label="cpx32-bicimi")
    report = tts_measure.merge_report(None, first)
    report = tts_measure.merge_report(report, second)
    assert [m["label"] for m in report["machines"]] == ["ev-pc", "cpx32-bicimi"]
    rerun = _machine([LOAD, _line(1, 200, 1000)], range(1, 2), label="ev-pc")
    report = tts_measure.merge_report(json.loads(json.dumps(report)), rerun)
    assert [m["label"] for m in report["machines"]] == ["ev-pc", "cpx32-bicimi"]
    assert report["machines"][0]["rtf_pooled"] == 0.2
    assert report["machines"][1]["rtf_pooled"] == 1.3
    assert report["schema_version"] == "1.0"
    assert report["model"]["code_commit"] == "146d36c1cb6660646be57d31339db4eed9315de3"
    assert report["model"]["weight_revisions"] == {
        "freyavoice/Freya-TTS": "d124e07493615208f58bdd21d432736849ee4230",
        "openbmb/VoxCPM2": "32279effe8c19989596f05d353d1447f51d9e915",
    }
    assert "ev-pc" in report["verdict_tr"] and "cpx32-bicimi" in report["verdict_tr"]


def test_markdown_lists_five_samples_and_no_wav_path_inside_the_repository(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "localappdata" / "PagentOS" / "tts-measure" / "ev-pc"
    machine = _machine(
        [LOAD] + [_line(i, 500, 1000) for i in range(1, 21)], range(1, 21), wav_dir=str(outside)
    )
    text = tts_measure.render_markdown(tts_measure.merge_report(None, machine), repo)
    for number in ("01", "05", "10", "15", "20"):
        assert str(outside / f"{number}.wav") in text
    assert str(repo) not in text
    assert "cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)" in text

    inside = _machine(FOUR, wav_dir=str(repo / "docs" / "evidence" / "wav"))
    with pytest.raises(ValueError, match="repository"):
        tts_measure.render_markdown(tts_measure.merge_report(None, inside), repo)


def test_proxy_label_is_marked_as_proxy(tmp_path: Path) -> None:
    machine = _machine(FOUR, label="cpx32-bicimi", cpus_limit=4.0)
    text = tts_measure.render_markdown(tts_measure.merge_report(None, machine), tmp_path)
    assert machine["proxy_for"] == "CPX32"
    assert "VEKİL" in text


def _synthesize_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tts_measure_synthesize", SYNTHESIZE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthesize_weight_hash_mismatch_exits_before_any_synthesis(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    synthesize = _synthesize_module()
    for name in synthesize.WEIGHTS:
        (tmp_path / name).write_bytes(b"not the pinned bytes")
    called: list[str] = []
    with pytest.raises(SystemExit) as stopped:
        synthesize.synth_main(
            ["--models", str(tmp_path), "--out", str(tmp_path / "out"), "--threads", "1"],
            stdin=iter([json.dumps({"index": 1, "text": "x"})]),
            load=lambda *a, **k: called.append("load"),
        )
    assert stopped.value.code != 0
    assert called == []
    assert not (tmp_path / "out").exists() or list((tmp_path / "out").iterdir()) == []
    error = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert error["error"] == "weight_hash_mismatch"
