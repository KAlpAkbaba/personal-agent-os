"""Unit tests: the sentence Chrome wrote WHILE the owner was reading, as its own table row
(measure-compare-from-core; team/plans/measure-compare-from-core-adr.md).

The Cloud Core's manifest carries ``ready_transcripts`` - engine label -> the sentence that
engine wrote at recording time - and ``browser_engine``. An engine that cannot be handed a
file (``chrome-web-speech``) is scored from those sentences with the same ``score_pair`` and
``intent_changed`` as every other row. Every expected number is counted by hand in the
comment beside it - never computed by the code under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.voice import stt_compare as sc
from tests.unit.test_stt_compare import ScriptedSTT, synthesize_wav

CHROME = "chrome-web-speech"
OFIS_REF = "Ofis bilgisayarımdan hesap makinesini aç."
OFIS_HEARD = "Ofisü bilgisayarında hesap makinesini açın."
CALC_REF = "Hesap makinesini aç."
ISI_REF = "ISI İL"


def _folder(tmp_path: Path, items: list[dict[str, Any]]) -> Path:
    """A recordings folder with one synthesized WAV per item and the manifest as given."""
    folder = tmp_path / "kayitlar"
    folder.mkdir()
    for item in items:
        (folder / item["file"]).write_bytes(synthesize_wav(item["reference"]))
    (folder / sc.MANIFEST_NAME).write_text(
        json.dumps({"language": "tr-TR", "items": items}, ensure_ascii=False), encoding="utf-8"
    )
    return folder


def _item(name: str, reference: str, **extra: Any) -> dict[str, Any]:
    return {"file": name, "reference": reference, "recorded_where": "ev", **extra}


def _chrome_only() -> list[sc.Engine]:
    return [sc.Engine(CHROME, None, reason=sc.REASON_NO_FILE_INPUT)]


def _row(report: dict[str, Any], label: str) -> dict[str, Any]:
    rows = [row for row in report["engines"] if row["label"] == label]
    assert len(rows) == 1, f"{label}: {len(rows)} rows in {[r['label'] for r in report['engines']]}"
    return rows[0]


# ------------------------------------------------------------------ (1) the row


def test_a_ready_transcript_is_scored_as_a_recorded_live_row(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path, [_item("ev-01.wav", OFIS_REF, ready_transcripts={CHROME: OFIS_HEARD})]
    )
    row = _row(sc.run_comparison(folder, _chrome_only()), CHROME)
    assert row["status"] == "RAN"
    assert row["source"] == "recorded_live"
    assert row["reason"] == ""
    # words: ofis->ofisü, bilgisayarımdan->bilgisayarında, aç->açın = 3 of 5.
    # letters: ofis 4 + bilgisayarımdan 15 + hesap 5 + makinesini 10 + aç 2 = 36;
    # edits: +ü (1), m->n and the last n dropped (2), +ın (2) = 5.
    assert (row["word_edits"], row["ref_words"], row["char_edits"], row["ref_chars"]) == (
        3,
        5,
        5,
        36,
    )
    assert row["wer"] == 0.6
    assert row["cer"] == 0.1389  # 5 / 36 = 0.13888...
    # the office computer is lost: a different machine would open the calculator
    assert row["intent_changes"] == 1
    # nothing was timed: Chrome wrote it while the owner read
    assert row["latency_p50_ms"] is None and row["latency_p95_ms"] is None
    assert (row["files_ran"], row["files_failed"]) == (1, 0)


# --------------------------------------------------------- (2) the missing sentence


def test_a_recording_without_a_ready_transcript_is_that_files_error(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        [
            _item("01.wav", OFIS_REF, ready_transcripts={CHROME: OFIS_HEARD}),
            _item("02.wav", CALC_REF, ready_transcripts={CHROME: "hesap makinesini açın"}),
            _item("03.wav", ISI_REF),
        ],
    )
    report = sc.run_comparison(folder, _chrome_only())
    row = _row(report, CHROME)
    assert (row["files_ran"], row["files_failed"]) == (2, 1)
    assert row["errors"] == [{"id": "03.wav", "error_class": "no ready transcript"}]
    assert report["items"][2]["results"][CHROME] == {"error": "no ready transcript"}
    # over the two that carry a sentence: (3 + 1) words of (5 + 3); (5 + 2) letters of (36 + 17).
    assert (row["word_edits"], row["ref_words"], row["char_edits"], row["ref_chars"]) == (
        4,
        8,
        7,
        53,
    )
    assert row["wer"] == 0.5
    assert row["cer"] == 0.1321  # 7 / 53 = 0.13207...
    # (3/5 + 1/3) / 2 = 0.46666...
    assert row["mean_sentence_wer"] == pytest.approx(0.4667, abs=1e-4)


# ----------------------------------------------------- (3) '' against null / absent


def test_an_empty_sentence_is_a_hearing_with_every_word_wrong(tmp_path: Path) -> None:
    folder = _folder(tmp_path, [_item("01.wav", CALC_REF, ready_transcripts={CHROME: ""})])
    row = _row(sc.run_comparison(folder, _chrome_only()), CHROME)
    assert row["status"] == "RAN"
    assert (row["files_ran"], row["files_failed"], row["errors"]) == (1, 0, [])
    # hesap makinesini aç: 3 words, 17 letters, all deleted
    assert (row["word_edits"], row["ref_words"], row["char_edits"], row["ref_chars"]) == (
        3,
        3,
        17,
        17,
    )
    assert row["wer"] == 1.0


def test_an_absent_sentence_is_never_a_perfect_hearing(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        [
            _item("01.wav", CALC_REF, ready_transcripts={CHROME: "Hesap makinesini aç"}),
            _item("02.wav", ISI_REF, ready_transcripts={}),
        ],
    )
    row = _row(sc.run_comparison(folder, _chrome_only()), CHROME)
    assert (row["files_ran"], row["files_failed"]) == (1, 1)
    # only the first file counts: 3 words, nothing wrong; ISI İL's 2 words are not in it
    assert (row["word_edits"], row["ref_words"]) == (0, 3)


# ------------------------------------------------------- (4) where the audio went


def test_the_live_row_is_heard_live_and_never_sent_audio(tmp_path: Path) -> None:
    folder = _folder(tmp_path, [_item("01.wav", OFIS_REF, ready_transcripts={CHROME: OFIS_HEARD})])
    file_engine = ScriptedSTT({OFIS_REF: OFIS_REF})
    report = sc.run_comparison(
        folder,
        [
            sc.Engine("a:file", file_engine, destination="Acme (acme.io)"),
            sc.Engine(CHROME, None, reason=sc.REASON_NO_FILE_INPUT),
        ],
    )
    assert report["audio_sent_to"] == [{"engine": "a:file", "destination": "Acme (acme.io)"}]
    assert [entry["engine"] for entry in report["heard_live_by"]] == [CHROME]
    assert report["heard_live_by"][0]["destination"]
    assert _row(report, "a:file")["source"] == "file"
    summary = "\n".join(report["summary_tr"])
    live = [line for line in report["summary_tr"] if CHROME in line and "kayıt anında" in line]
    assert live, summary
    assert any("Google" in line for line in live)
    assert any("gürültü bastırma" in line for line in report["summary_tr"]), summary
    # numbers and names only: what Chrome wrote is in the report file, never in the summary
    assert "Ofisü" not in summary and "bilgisayarında" not in summary
    assert report["items"][0]["browser_engine"] is None


def test_the_item_row_carries_the_browser_engine(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        [
            _item(
                "01.wav",
                OFIS_REF,
                ready_transcripts={CHROME: OFIS_HEARD},
                browser_engine="Chrome 141 webkitSpeechRecognition",
            )
        ],
    )
    report = sc.run_comparison(folder, _chrome_only())
    assert report["items"][0]["browser_engine"] == "Chrome 141 webkitSpeechRecognition"
    assert report["items"][0]["results"][CHROME]["hypothesis"] == OFIS_HEARD


# --------------------------------------------------- (5) a manifest without the fields

#: What the pre-card code (schema 1.0) produced for these engines on these recordings -
#: held here as literals, so the new code is compared against them and not against itself.
PRE_CARD_ROWS: list[dict[str, Any]] = [
    {
        "label": "a:heard",
        "status": "RAN",
        "reason": "",
        "destination": "A",
        "files_ran": 2,
        "files_failed": 0,
        "wer": 0.5,
        "cer": 0.1321,
        "mean_sentence_wer": 0.4667,
        "word_edits": 4,
        "ref_words": 8,
        "char_edits": 7,
        "ref_chars": 53,
        "intent_changes": 1,
        "latency_p50_ms": 1000.0,
        "latency_p95_ms": 1000.0,
        "errors": [],
    },
    {
        "label": "b:absent",
        "status": "NOT_RUN",
        "reason": "not configured",
        "destination": "B",
        "files_ran": 0,
        "files_failed": 0,
        "wer": None,
        "cer": None,
        "mean_sentence_wer": None,
        "word_edits": 0,
        "ref_words": 0,
        "char_edits": 0,
        "ref_chars": 0,
        "intent_changes": None,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "errors": [],
    },
    {
        "label": CHROME,
        "status": "NOT_RUN",
        "reason": "no file input",
        "destination": "",
        "files_ran": 0,
        "files_failed": 0,
        "wer": None,
        "cer": None,
        "mean_sentence_wer": None,
        "word_edits": 0,
        "ref_words": 0,
        "char_edits": 0,
        "ref_chars": 0,
        "intent_changes": None,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "errors": [],
    },
]


def test_a_manifest_without_the_fields_gives_the_rows_it_gave_before(tmp_path: Path) -> None:
    folder = _folder(tmp_path, [_item("01.wav", OFIS_REF), _item("02.wav", CALC_REF)])
    heard = ScriptedSTT({OFIS_REF: OFIS_HEARD, CALC_REF: "hesap makinesini açın"})
    ticks = iter([0.0, 1.0, 1.0, 2.0])
    report = sc.run_comparison(
        folder,
        [
            sc.Engine("a:heard", heard, destination="A"),
            sc.Engine("b:absent", None, reason=sc.REASON_NOT_CONFIGURED, destination="B"),
            sc.Engine(CHROME, None, reason=sc.REASON_NO_FILE_INPUT),
        ],
        clock=lambda: next(ticks),
    )
    # the one new field, named; everything else exactly as before
    assert [row.get("source") for row in report["engines"]] == ["file", "file", "file"]
    rows = [{k: v for k, v in row.items() if k != "source"} for row in report["engines"]]
    assert rows == PRE_CARD_ROWS
    assert report.get("heard_live_by", []) == []
    assert [entry["engine"] for entry in report["audio_sent_to"]] == ["a:heard"]
    assert not any("kayıt anında" in line for line in report["summary_tr"])


# ------------------------------------------------------------- (6) a bad manifest


@pytest.mark.parametrize(
    "ready",
    [[OFIS_HEARD], {CHROME: None}, {CHROME: 7}, {CHROME: ["a"]}, "Ofisü", {"": "x"}],
    ids=["list", "null-value", "number", "list-value", "string", "empty-label"],
)
def test_ready_transcripts_that_are_not_label_to_sentence_are_refused(
    tmp_path: Path, ready: Any
) -> None:
    folder = _folder(tmp_path, [_item("01.wav", OFIS_REF, ready_transcripts=ready)])
    with pytest.raises(sc.ManifestError, match="ready_transcripts"):
        sc.load_manifest(folder)


def test_a_browser_engine_that_is_not_a_string_is_refused(tmp_path: Path) -> None:
    folder = _folder(tmp_path, [_item("01.wav", OFIS_REF, browser_engine=["chrome"])])
    with pytest.raises(sc.ManifestError, match="browser_engine"):
        sc.load_manifest(folder)


def test_the_two_fields_are_read_into_the_recording(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        [
            _item("01.wav", OFIS_REF, ready_transcripts={CHROME: ""}, browser_engine="Chrome"),
            _item("02.wav", CALC_REF),
        ],
    )
    _, recordings = sc.load_manifest(folder)
    assert dict(recordings[0].ready_transcripts) == {CHROME: ""}
    assert recordings[0].browser_engine == "Chrome"
    assert dict(recordings[1].ready_transcripts) == {}
    assert recordings[1].browser_engine is None
    # every existing caller still builds one with four arguments
    assert sc.Recording("x", folder / "x.wav", "ref", "masa").ready_transcripts == {}


# ----------------------------------------------------------- (7) an unknown label


def test_an_unknown_label_gets_its_own_row_after_the_configured_ones(tmp_path: Path) -> None:
    folder = _folder(
        tmp_path,
        [
            _item(
                "01.wav",
                OFIS_REF,
                ready_transcripts={CHROME: OFIS_HEARD, "edge-web-speech": OFIS_REF},
            ),
            _item("02.wav", CALC_REF, ready_transcripts={"edge-web-speech": "hesap makinesini"}),
        ],
    )
    report = sc.run_comparison(
        folder,
        [sc.Engine("a:none", None, reason=sc.REASON_NOT_CONFIGURED), *_chrome_only()],
    )
    assert [row["label"] for row in report["engines"]] == ["a:none", CHROME, "edge-web-speech"]
    chrome, edge = _row(report, CHROME), _row(report, "edge-web-speech")
    # two engines, two rows: chrome heard 01 only (3 of 5 words), and 02 is its error
    assert (chrome["files_ran"], chrome["files_failed"], chrome["word_edits"]) == (1, 1, 3)
    # edge heard both: 01 perfect, 02 lost "aç" (1 of 3 words) -> 1 of 8
    assert (edge["files_ran"], edge["files_failed"], edge["word_edits"], edge["ref_words"]) == (
        2,
        0,
        1,
        8,
    )
    assert edge["source"] == "recorded_live" and edge["status"] == "RAN"
    assert [entry["engine"] for entry in report["heard_live_by"]] == [CHROME, "edge-web-speech"]
    assert report["audio_sent_to"] == []


# --------------------------------------------------------------- (8) the schema


def test_the_report_schema_is_one_point_one(tmp_path: Path) -> None:
    folder = _folder(tmp_path, [_item("01.wav", OFIS_REF)])
    assert sc.REPORT_SCHEMA_VERSION == "1.1"
    assert sc.run_comparison(folder, _chrome_only())["schema_version"] == "1.1"
