"""Unit tests: the second local Turkish TTS candidate, Antalia 1, measured beside FreyaTTS
(tts-antalia-measure; team/plans/tts-antalia-measure-adr.md).

MEASUREMENT ONLY. ``app.voice.tts_measure`` records which engine a report is for and renders
``docs/evidence/tts-measure-compare.md`` from whichever engine evidence exists; the container
half is ``tools/tts-measure/antalia/synthesize.py``.

Every expected row below is a LITERAL written by hand from the synth_ms / audio_ms / peak the
test chooses - never produced by the code under test.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.voice import tts_measure
from app.voice.stt_compare import OWNER_SENTENCES

REPO = Path(__file__).resolve().parents[4]
ANTALIA_SYNTHESIZE = REPO / "tools" / "tts-measure" / "antalia" / "synthesize.py"

QUALITY_CLAIMS = ("iyi", "kaliteli", "doğal", "güzel", "net ", "anlaşılır", "kötü", "berbat")
LOAD = json.dumps(
    {"event": "load", "load_ms": 4321.0, "peak_rss_mb": 900.0, "cpu": "Test CPU", "threads": 4}
)
OUTSIDE = "C:/Users/owner/AppData/Local/PagentOS/tts-measure"


def _line(index: int, synth_ms: float, audio_ms: float, peak: float | None = None) -> str:
    return json.dumps(
        {
            "index": index,
            "chars": 10 * index,
            "audio_ms": audio_ms,
            "synth_ms": synth_ms,
            "first_audio_ms": synth_ms,
            "streamed": False,
            "peak_rss_mb": 1000.0 + index if peak is None else peak,
        }
    )


def _machine(lines: list[str], label: str, wav_dir: str, indices: range = range(1, 3)) -> Any:
    run = tts_measure.parse_container_output(lines, indices)
    return tts_measure.summarize_machine(
        run,
        label=label,
        threads=4,
        cpus_limit=4.0 if label == "cpx32-bicimi" else None,
        wav_dir=wav_dir,
    )


def _freya() -> dict[str, Any]:
    # ev-pc: RTF 0.8, 1.0 -> pooled 1800/2000 = 0.9, p95 1.0; first audio p95 1000; peak 1002
    # cpx32-bicimi: RTF 1.3, 1.3 -> pooled 1.3; first audio p95 1300; peak 1002
    report = tts_measure.merge_report(
        None,
        _machine([LOAD, _line(1, 800, 1000), _line(2, 1000, 1000)], "ev-pc", f"{OUTSIDE}/ev-pc"),
    )
    return tts_measure.merge_report(
        report,
        _machine(
            [LOAD, _line(1, 1300, 1000), _line(2, 1300, 1000)],
            "cpx32-bicimi",
            f"{OUTSIDE}/cpx32-bicimi",
        ),
    )


def _antalia() -> dict[str, Any]:
    # ev-pc: RTF 0.6, 1.0 -> pooled 1600/2000 = 0.8, p95 1.0; first audio p95 1000; peak 3000
    # cpx32-bicimi: sentence 2 failed; RTF 2.0 -> pooled 2.0; first audio 2000; peak 3000; 1/1
    report = tts_measure.merge_report(
        None,
        _machine(
            [LOAD, _line(1, 600, 1000, peak=3000.0), _line(2, 1000, 1000, peak=2500.0)],
            "ev-pc",
            f"{OUTSIDE}/antalia/ev-pc",
        ),
        engine="antalia",
    )
    return tts_measure.merge_report(
        report,
        _machine(
            [LOAD, _line(1, 2000, 1000, peak=3000.0), json.dumps({"index": 2, "error": "x"})],
            "cpx32-bicimi",
            f"{OUTSIDE}/antalia/cpx32-bicimi",
        ),
        engine="antalia",
    )


def test_engine_is_written_for_antalia_and_a_report_without_it_reads_as_freya() -> None:
    antalia = _antalia()
    assert antalia["model"]["engine"] == "antalia"
    assert antalia["model"]["name"] == "Antalia 1"
    assert antalia["model"]["code_commit"] == "20f9bfeaaefefb3ef723c292fe2bb0306e08823d"
    assert antalia["model"]["weight_revisions"]["cloud0day3/antalia-1"] == (
        "eaec2aad2da8c0db5fc359734470874dae82c603"
    )
    assert tts_measure.report_engine(antalia) == "antalia"
    assert _freya()["model"]["engine"] == "freya"
    # a FreyaTTS report written before this card: no engine field
    old = {"schema_version": "1.0", "model": {"name": "FreyaTTS-small"}, "machines": []}
    assert tts_measure.report_engine(old) == "freya"
    assert tts_measure.evidence_name("antalia") == "tts-antalia-measure"
    assert tts_measure.evidence_name("freya") == "tts-freya-measure"


def test_compare_rows_equal_literals_one_per_engine_and_label() -> None:
    rows = tts_measure.compare_rows({"freya": _freya(), "antalia": _antalia()})
    assert rows == [
        "| freya | ev-pc | gerçek | 0,900 | 1,000 | 1000 | 1002 | 4321 | 2/0 |",
        "| freya | cpx32-bicimi | VEKİL (CPX32) | 1,300 | 1,300 | 1300 | 1002 | 4321 | 2/0 |",
        "| antalia | ev-pc | gerçek | 0,800 | 1,000 | 1000 | 3000 | 4321 | 2/0 |",
        "| antalia | cpx32-bicimi | VEKİL (CPX32) | 2,000 | 2,000 | 2000 | 3000 | 4321 | 1/1 |",
    ]


def test_missing_engine_evidence_is_an_olculmedi_row_never_a_zero(tmp_path: Path) -> None:
    olculmedi = " | ".join(["ölçülmedi"] * 6)
    rows = tts_measure.compare_rows({"freya": _freya(), "antalia": None})
    assert rows[2:] == [
        f"| antalia | ev-pc | - | {olculmedi} |",
        f"| antalia | cpx32-bicimi | - | {olculmedi} |",
    ]
    # from the files: only the FreyaTTS evidence exists
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "tts-freya-measure.json").write_text(json.dumps(_freya()), encoding="utf-8")
    code = tts_measure.main(
        ["compare", "--evidence-dir", str(evidence), "--repo-root", str(tmp_path / "repo")]
    )
    assert code == 0
    text = (evidence / "tts-measure-compare.md").read_text(encoding="utf-8")
    assert f"| antalia | ev-pc | - | {olculmedi} |" in text
    antalia_rows = [line for line in text.splitlines() if line.startswith("| antalia")]
    assert len(antalia_rows) == 2
    for line in antalia_rows:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert "0" not in cells and "0,000" not in cells and "" not in cells


def test_compare_verdict_real_time_from_pooled_rtf_and_no_quality_claim() -> None:
    verdict = tts_measure.compare_verdict_tr({"freya": _freya(), "antalia": _antalia()})
    assert "antalia / ev-pc: gerçek zamana ulaşıldı (havuzlanmış RTF 0,80 < 1)" in verdict
    assert "freya / cpx32-bicimi: gerçek zamana ulaşılamadı (havuzlanmış RTF 1,30 ≥ 1)" in verdict
    assert "Daha küçük bellekte sığan: freya (tepe 1002 MB; antalia 3000 MB)" in verdict
    assert "ses kalitesi burada değerlendirilmez" in verdict.lower()
    lowered = verdict.lower()
    for claim in QUALITY_CLAIMS:
        assert claim not in lowered, claim
    missing = tts_measure.compare_verdict_tr({"freya": _freya(), "antalia": None})
    assert "antalia: ölçülmedi" in missing


def test_compare_markdown_says_cer_not_measured_and_has_no_wav_inside_the_repository(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    text = tts_measure.render_compare({"freya": _freya(), "antalia": _antalia()}, repo)
    assert "CER: ölçülmedi" in text
    assert "cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)" in text
    assert str(repo) not in text
    for number in ("01", "05", "10", "15", "20"):
        assert f"{OUTSIDE}/ev-pc/{number}.wav" in text.replace("\\", "/")
        assert f"{OUTSIDE}/antalia/ev-pc/{number}.wav" in text.replace("\\", "/")
    inside = _freya()
    inside["machines"][0]["wav_dir"] = str(repo / "docs" / "evidence" / "wav")
    with pytest.raises(ValueError, match="repository"):
        tts_measure.render_compare({"freya": inside, "antalia": None}, repo)


def test_sentence_list_is_owner_sentences_by_identity() -> None:
    assert tts_measure.sentences() is OWNER_SENTENCES


def _antalia_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("antalia_synthesize", ANTALIA_SYNTHESIZE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_antalia_synthesize_weight_hash_mismatch_exits_3_before_any_load(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    synthesize = _antalia_module()
    for name in synthesize.WEIGHTS:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"not the pinned bytes")
    called: list[str] = []
    with pytest.raises(SystemExit) as stopped:
        synthesize.synth_main(
            ["--models", str(tmp_path), "--out", str(tmp_path / "out"), "--threads", "1"],
            stdin=iter([json.dumps({"index": 1, "text": "x"})]),
            load=lambda *a, **k: called.append("load"),
        )
    assert stopped.value.code == 3
    assert called == []
    assert not (tmp_path / "out").exists()
    error = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert error["error"] == "weight_hash_mismatch"
    assert sorted(error["files"]) == sorted(synthesize.WEIGHTS)


def test_antalia_synthesize_speaks_the_freya_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    synthesize = _antalia_module()
    assert synthesize.SAMPLE_RATE == 24000
    pinned: dict[str, tuple[str, str]] = {}
    for name in synthesize.WEIGHTS:
        path = tmp_path / "models" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        pinned[name] = ("https://example.invalid/" + name, hashlib.sha256(name.encode()).hexdigest())
    monkeypatch.setattr(synthesize, "WEIGHTS", pinned)
    written: list[str] = []
    monkeypatch.setattr(synthesize, "_write_wav", lambda path, wav: written.append(path.name))
    seen: list[tuple[str, int, int]] = []

    def fake_load(models: Path, threads: int, seed: int) -> Any:
        def speak(text: str, steps: int, seed: int) -> list[float]:
            seen.append((text, steps, seed))
            return [0.0] * 12000  # 500 ms at 24 kHz

        return synthesize.Loaded(speak, threads)

    code = synthesize.synth_main(
        ["--models", str(tmp_path / "models"), "--out", str(tmp_path / "out"), "--threads", "2"],
        stdin=iter([json.dumps({"index": 3, "text": "Merhaba."})]),
        load=fake_load,
    )
    assert code == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert rows[0]["event"] == "load" and rows[0]["threads"] == 2 and "load_ms" in rows[0]
    assert set(rows[1]) == {
        "index",
        "chars",
        "audio_ms",
        "synth_ms",
        "first_audio_ms",
        "streamed",
        "peak_rss_mb",
    }
    assert rows[1]["index"] == 3 and rows[1]["chars"] == 8 and rows[1]["audio_ms"] == 500.0
    assert rows[1]["streamed"] is False and rows[1]["first_audio_ms"] == rows[1]["synth_ms"]
    assert written == ["03.wav"]
    assert seen == [("Merhaba.", 32, 20260803)]
