"""Unit tests: the STT comparison harness (stt-engines-measure, measurement only).

Every expected number here is counted by hand in the comment beside it - never computed by
the code under test - so a wrong normalisation or a wrong pooling cannot agree with itself.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.voice import stt_compare as sc
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    FakeSTTProvider,
    FasterWhisperSTTProvider,
    ProviderCapabilities,
    STTResult,
    synthesize_wav,
)

OFIS_REF = "Ofis bilgisayarımdan hesap makinesini aç"
OFIS_HEARD = "Ofisü bilgisayarında hesap makinesini açın"
OFIS_PUNCT = "ofis bilgisayarımdan, hesap makinesini aç!"


class ScriptedSTT:
    """An engine that answers each recording with the transcript the test gave it."""

    name = "scripted"

    def __init__(self, answers: dict[str, str | Exception]) -> None:
        self._answers = {synthesize_wav(ref): out for ref, out in answers.items()}
        self.calls: list[bytes] = []

    def capabilities(self) -> ProviderCapabilities:
        return FakeSTTProvider().capabilities()

    def transcribe(self, audio: bytes, *, language: str = "tr-TR") -> STTResult:
        self.calls.append(audio)
        out = self._answers[audio]
        if isinstance(out, Exception):
            raise out
        return STTResult(
            text=out,
            confidence=1.0,
            word_timings=(),
            latency_ms=0.0,
            provider=self.name,
            language=language,
        )


def _folder(tmp_path: Path, references: list[str], *, where: str = "masa") -> Path:
    """A recordings folder: one WAV per reference (its bytes derived from the reference,
    which is how ScriptedSTT recognises it) and the manifest that names them."""
    folder = tmp_path / "kayitlar"
    folder.mkdir()
    items = []
    for index, reference in enumerate(references, start=1):
        name = f"{index:02d}.wav"
        (folder / name).write_bytes(synthesize_wav(reference))
        items.append({"file": name, "reference": reference, "recorded_where": where})
    (folder / sc.MANIFEST_NAME).write_text(
        json.dumps({"language": "tr-TR", "items": items}, ensure_ascii=False), encoding="utf-8"
    )
    return folder


def _tree(root: Path) -> dict[str, bytes]:
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return {str(p.relative_to(root)): p.read_bytes() for p in files}


def _row(report: dict[str, Any], label: str) -> dict[str, Any]:
    rows = [row for row in report["engines"] if row["label"] == label]
    assert len(rows) == 1, f"{label}: {len(rows)} rows in {[r['label'] for r in report['engines']]}"
    return rows[0]


# ------------------------------------------------------------- normalisation


def test_normalisation_keeps_the_turkish_i_pairs_apart() -> None:
    # I -> ı and İ -> i: "ISI İL" is "ısı il", never "isi il".
    assert sc.normalize_for_compare("ISI İL!") == "ısı il"
    assert sc.normalize_for_compare("Iğdır'ın hava durumu nasıl?") == "ığdırın hava durumu nasıl"


def test_normalisation_folds_punctuation_and_case_and_leaves_numbers_as_spoken() -> None:
    assert sc.normalize_for_compare("  Saat 7'de,  yedi kişi…  ") == "saat 7de yedi kişi"
    assert sc.normalize_for_compare("Son e-postayı OKU.") == "son e postayı oku"


# ----------------------------------------------------------- WER / CER pairs


def test_upper_case_turkish_i_is_no_error() -> None:
    score = sc.score_pair("ISI İL", "ısı il")
    # 2 reference words, 5 reference letters (ı-s-ı + i-l), nothing to edit.
    assert (score.ref_words, score.word_edits, score.ref_chars, score.char_edits) == (2, 0, 5, 0)


def test_a_dotless_i_heard_as_dotted_is_an_error() -> None:
    score = sc.score_pair("ısı", "isi")
    # one word wrong; two of its three letters wrong.
    assert (score.ref_words, score.word_edits, score.ref_chars, score.char_edits) == (1, 1, 3, 2)


def test_an_inserted_suffix_is_one_word_and_two_letters() -> None:
    score = sc.score_pair("Hesap makinesini aç.", "hesap makinesini açın")
    # words: aç -> açın (1 of 3). letters: hesap 5 + makinesini 10 + aç 2 = 17; "ın" inserted = 2.
    assert (score.ref_words, score.word_edits, score.ref_chars, score.char_edits) == (3, 1, 17, 2)
    assert score.wer == pytest.approx(1 / 3)
    assert score.cer == pytest.approx(2 / 17)


def test_the_ofis_sentence_by_hand() -> None:
    score = sc.score_pair(OFIS_REF, OFIS_HEARD)
    # words: ofis->ofisü, bilgisayarımdan->bilgisayarında, aç->açın = 3 of 5.
    # letters: ofis 4 + bilgisayarımdan 15 + hesap 5 + makinesini 10 + aç 2 = 36;
    # edits: +ü (1), m->n and the last n dropped (2), +ın (2) = 5.
    assert (score.ref_words, score.word_edits, score.ref_chars, score.char_edits) == (5, 3, 36, 5)


# ------------------------------------------------------------- intent change


def test_intent_changes_when_the_named_device_is_lost() -> None:
    assert sc.intent_changed(OFIS_REF, OFIS_HEARD) is True


def test_intent_does_not_change_for_punctuation_and_case() -> None:
    assert sc.intent_changed(OFIS_REF, OFIS_PUNCT) is False


def test_intent_changes_when_remember_is_heard_as_forget() -> None:
    assert sc.intent_changed("Bunu unutma", "Bunu unut") is True


def test_a_misheard_word_that_changes_no_action_is_not_an_intent_change() -> None:
    # still the calculator, still no device named: a word error, not a different action
    assert sc.intent_changed("Hesap makinesini aç.", "hesap makinesini açın") is False
    # the minutes a snooze carries are part of the action
    assert sc.intent_changed("Alarmı on dakika ertele", "Alarmı beş dakika ertele") is True


# ------------------------------------------------------------------ the table


def test_table_pools_the_rates_and_counts_intent_changes(tmp_path: Path) -> None:
    refs = ["ISI İL", "Hesap makinesini aç.", OFIS_REF]
    folder = _folder(tmp_path, refs)
    heard = ScriptedSTT(
        {"ISI İL": "ısı il", "Hesap makinesini aç.": "hesap makinesini açın", OFIS_REF: OFIS_HEARD}
    )
    punct = ScriptedSTT(
        {"ISI İL": "Isı il.", "Hesap makinesini aç.": "Hesap makinesini aç", OFIS_REF: OFIS_PUNCT}
    )
    report = sc.run_comparison(
        folder, [sc.Engine("a:heard", heard, destination="A"), sc.Engine("b:punct", punct)]
    )

    a = _row(report, "a:heard")
    assert a["status"] == sc.STATUS_RAN
    assert (a["files_ran"], a["files_failed"]) == (3, 0)
    # pooled: (0 + 1 + 3) word edits over (2 + 3 + 5) words; (0 + 2 + 5) letters over (5 + 17 + 36).
    assert (a["word_edits"], a["ref_words"], a["char_edits"], a["ref_chars"]) == (4, 10, 7, 58)
    assert a["wer"] == pytest.approx(0.4)
    assert a["cer"] == pytest.approx(7 / 58, abs=1e-4)
    # the mean of the per-sentence rates is a different number: (0 + 1/3 + 3/5) / 3.
    assert a["mean_sentence_wer"] == pytest.approx(0.3111, abs=1e-4)
    # only the Ofis sentence loses its device; "aç" -> "açın" still opens the calculator.
    assert a["intent_changes"] == 1

    b = _row(report, "b:punct")
    assert (b["word_edits"], b["char_edits"], b["intent_changes"]) == (0, 0, 0)
    assert b["wer"] == 0.0


def test_an_engine_without_a_credential_is_a_row_and_the_run_completes(tmp_path: Path) -> None:
    folder = _folder(tmp_path, ["ISI İL"])
    report = sc.run_comparison(
        folder,
        [
            sc.Engine("a:ran", ScriptedSTT({"ISI İL": "ısı il"})),
            sc.Engine("soniox:stt-rt-v5", None, reason=sc.REASON_NOT_CONFIGURED),
        ],
    )
    assert [row["label"] for row in report["engines"]] == ["a:ran", "soniox:stt-rt-v5"]
    missing = _row(report, "soniox:stt-rt-v5")
    assert missing["status"] == sc.STATUS_NOT_RUN
    assert missing["reason"] == "not configured"
    assert missing["wer"] is None
    assert any("soniox:stt-rt-v5" in line and "NOT_RUN" in line for line in report["summary_tr"])


def test_two_models_of_one_vendor_are_two_rows(tmp_path: Path) -> None:
    folder = _folder(tmp_path, ["Hesap makinesini aç."])
    good = ScriptedSTT({"Hesap makinesini aç.": "hesap makinesini aç"})
    bad = ScriptedSTT({"Hesap makinesini aç.": "hesap makinesini açın"})
    assert good.name == bad.name  # the two OpenAI models share one provider name too
    report = sc.run_comparison(folder, [sc.Engine("v:one", good), sc.Engine("v:two", bad)])
    assert _row(report, "v:one")["word_edits"] == 0
    assert _row(report, "v:two")["word_edits"] == 1


def test_a_duplicate_engine_label_is_refused(tmp_path: Path) -> None:
    folder = _folder(tmp_path, ["ISI İL"])
    engine = ScriptedSTT({"ISI İL": "ısı il"})
    with pytest.raises(ValueError, match="label"):
        sc.run_comparison(folder, [sc.Engine("same", engine), sc.Engine("same", engine)])


def test_one_failing_file_keeps_the_row_and_both_counts(tmp_path: Path) -> None:
    refs = ["ISI İL", "Hesap makinesini aç.", OFIS_REF]
    folder = _folder(tmp_path, refs)
    engine = ScriptedSTT(
        {
            "ISI İL": "ısı il",
            "Hesap makinesini aç.": VoiceError(VoiceErrorClass.TIMEOUT, "slow", provider="x"),
            OFIS_REF: OFIS_HEARD,
        }
    )
    row = _row(sc.run_comparison(folder, [sc.Engine("a", engine)]), "a")
    assert row["status"] == sc.STATUS_RAN
    assert (row["files_ran"], row["files_failed"]) == (2, 1)
    # the rates are over the two files that ran: 3 edits over 2 + 5 words.
    assert (row["word_edits"], row["ref_words"]) == (3, 7)
    assert row["errors"] == [{"id": "02.wav", "error_class": "timeout"}]


def test_an_engine_that_fails_every_file_is_failed_not_zero(tmp_path: Path) -> None:
    folder = _folder(tmp_path, ["ISI İL"])
    engine = ScriptedSTT({"ISI İL": VoiceError(VoiceErrorClass.DEPENDENCY_UNAVAILABLE, "down")})
    row = _row(sc.run_comparison(folder, [sc.Engine("a", engine)]), "a")
    assert row["status"] == sc.STATUS_FAILED
    assert row["wer"] is None and row["files_failed"] == 1


def test_latency_percentiles_come_from_the_injected_clock(tmp_path: Path) -> None:
    refs = ["bir", "iki", "üç"]
    folder = _folder(tmp_path, refs)
    engine = ScriptedSTT({ref: ref for ref in refs})
    ticks: Iterator[float] = iter([0.0, 0.1, 1.0, 1.2, 2.0, 2.3])
    row = _row(sc.run_comparison(folder, [sc.Engine("a", engine)], clock=lambda: next(ticks)), "a")
    # 100, 200, 300 ms: the median is 200 and the 95th percentile (nearest rank) is 300.
    assert row["latency_p50_ms"] == pytest.approx(200.0)
    assert row["latency_p95_ms"] == pytest.approx(300.0)


def test_the_ten_worst_sentences_are_side_by_side(tmp_path: Path) -> None:
    refs = [f"cümle {word} aç" for word in "a b c d e f g h i j k l".split()]
    folder = _folder(tmp_path, refs)
    # engine A gets the first eleven wrong (the very first one worst of all), B none.
    wrong: dict[str, str | Exception] = {ref: "cümle aç" for ref in refs[:11]}
    wrong[refs[0]] = "bambaşka"
    wrong[refs[11]] = refs[11]
    report = sc.run_comparison(
        folder,
        [sc.Engine("A", ScriptedSTT(wrong)), sc.Engine("B", ScriptedSTT({r: r for r in refs}))],
    )
    worst = report["worst"]["A"]
    assert len(worst) == 10
    assert worst[0]["id"] == "01.wav" and worst[0]["word_edits"] == 3
    assert worst[0]["reference"] == refs[0]
    assert worst[0]["hypotheses"] == {"A": "bambaşka", "B": refs[0]}
    assert "12.wav" not in [entry["id"] for entry in worst]
    # an engine with nothing wrong has no worst sentences to show
    assert report["worst"]["B"] == []


def test_the_summary_names_who_received_the_audio(tmp_path: Path) -> None:
    folder = _folder(tmp_path, ["ISI İL"])
    report = sc.run_comparison(
        folder,
        [
            sc.Engine("a:ran", ScriptedSTT({"ISI İL": "ısı il"}), destination="Acme (acme.io)"),
            sc.Engine("b:absent", None, reason=sc.REASON_NOT_CONFIGURED, destination="Beta"),
        ],
    )
    assert report["audio_sent_to"] == [{"engine": "a:ran", "destination": "Acme (acme.io)"}]
    summary = "\n".join(report["summary_tr"])
    assert "Acme (acme.io)" in summary
    assert "Beta" not in summary


# ------------------------------------------------------------------ the files


def test_a_container_that_is_not_wav_is_reported_and_sent_nowhere(tmp_path: Path) -> None:
    folder = _folder(tmp_path, ["ISI İL"])
    (folder / "02.m4a").write_bytes(b"\x00\x00\x00\x20ftypM4A not a wav")
    manifest = json.loads((folder / sc.MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["items"].append({"file": "02.m4a", "reference": "iki", "recorded_where": "masa"})
    manifest["items"].append({"file": "03.wav", "reference": "üç", "recorded_where": "masa"})
    (folder / sc.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    engine = ScriptedSTT({"ISI İL": "ısı il"})
    report = sc.run_comparison(folder, [sc.Engine("a", engine)])
    assert report["recordings"]["usable"] == 1
    assert report["recordings"]["skipped"] == [
        {"id": "02.m4a", "reason": "unsupported container (WAV only)"},
        {"id": "03.wav", "reason": "file not found"},
    ]
    assert len(engine.calls) == 1
    assert _row(report, "a")["files_ran"] == 1
    # one line per reason in the owner's summary, not one per file
    assert "Atlanan 1 kayıt - WAV değil (yalnız WAV ölçülür): 02.m4a" in report["summary_tr"]
    assert "Atlanan 1 kayıt - dosya yok: 03.wav" in report["summary_tr"]


def test_a_manifest_file_outside_the_folder_is_refused(tmp_path: Path) -> None:
    folder = _folder(tmp_path, ["ISI İL"])
    (tmp_path / "secret.wav").write_bytes(synthesize_wav("gizli"))
    (folder / sc.MANIFEST_NAME).write_text(
        json.dumps({"items": [{"file": "../secret.wav", "reference": "gizli"}]}), encoding="utf-8"
    )
    with pytest.raises(sc.ManifestError, match="outside"):
        sc.load_manifest(folder)


def test_a_manifest_item_without_a_reference_is_refused(tmp_path: Path) -> None:
    folder = _folder(tmp_path, ["ISI İL"])
    (folder / sc.MANIFEST_NAME).write_text(
        json.dumps({"items": [{"file": "01.wav", "reference": "  "}]}), encoding="utf-8"
    )
    with pytest.raises(sc.ManifestError, match="reference"):
        sc.load_manifest(folder)


def test_the_template_holds_twenty_sentences_and_never_replaces_a_manifest(tmp_path: Path) -> None:
    folder = tmp_path / "bos"
    folder.mkdir()
    path = sc.write_manifest_template(folder)
    assert path == folder / sc.TEMPLATE_NAME
    template = json.loads(path.read_text(encoding="utf-8"))
    assert len(template["items"]) == 20
    assert template["items"][0] == {
        "file": "01.wav",
        "reference": "Ofis bilgisayarımdan hesap makinesini aç.",
        "recorded_where": "masa",
    }
    assert template["items"][9]["reference"] == "Iğdır'ın hava durumu nasıl?"
    assert not (folder / sc.MANIFEST_NAME).exists()


# -------------------------------------------------------------------- the CLI


def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (sc.OPENAI_KEY_ENV, sc.SONIOX_KEY_ENV, sc.AZURE_KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(FasterWhisperSTTProvider, "available", lambda self: False)


def test_configured_engines_lists_every_engine_with_or_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_keys(monkeypatch)
    engines = sc.configured_engines({})
    assert [(e.label, e.provider is None, e.reason) for e in engines] == [
        ("openai:gpt-4o-transcribe", True, "not configured"),
        ("openai:whisper-1", True, "not configured"),
        ("soniox:stt-rt-v5", True, "not configured"),
        ("azure:tr-TR", True, "not configured"),
        ("faster-whisper:large-v3-turbo", True, "not installed"),
        ("chrome-web-speech", True, "no file input"),
    ]
    with_keys = sc.configured_engines({sc.OPENAI_KEY_ENV: "sk-x", sc.SONIOX_KEY_ENV: " k "})
    assert [e.label for e in with_keys if e.provider is not None] == [
        "openai:gpt-4o-transcribe",
        "openai:whisper-1",
        "soniox:stt-rt-v5",
    ]


def test_cli_with_no_credentials_writes_the_table_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_keys(monkeypatch)
    folder = _folder(tmp_path, ["ISI İL"])
    out = tmp_path / "out" / "stt-compare.json"
    out.parent.mkdir()
    assert sc.main(["--folder", str(folder), "--out", str(out)]) == sc.EXIT_OK
    report = json.loads(out.read_text(encoding="utf-8"))
    assert {row["status"] for row in report["engines"]} == {sc.STATUS_NOT_RUN}
    assert len(report["engines"]) == 6
    assert report["audio_sent_to"] == []
    assert "NOT_RUN" in capsys.readouterr().out


def test_cli_engine_filter_keeps_the_unselected_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_keys(monkeypatch)
    folder = _folder(tmp_path, ["ISI İL"])
    out = tmp_path / "r.json"
    code = sc.main(["--folder", str(folder), "--out", str(out), "--engines", "openai:whisper-1"])
    assert code == sc.EXIT_OK
    report = json.loads(out.read_text(encoding="utf-8"))
    assert _row(report, "openai:whisper-1")["reason"] == "not configured"
    assert _row(report, "soniox:stt-rt-v5")["reason"] == "not selected"
    assert (
        sc.main(["--folder", str(folder), "--out", str(out), "--engines", "nope"])
        == sc.EXIT_BAD_INPUT
    )


def test_cli_without_a_manifest_is_bad_input_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_keys(monkeypatch)
    folder = tmp_path / "bos"
    folder.mkdir()
    out = tmp_path / "r.json"
    assert sc.main(["--folder", str(folder), "--out", str(out)]) == sc.EXIT_BAD_INPUT
    assert not out.exists()
    assert sc.main(["--folder", str(folder), "--write-template"]) == sc.EXIT_OK
    assert (folder / sc.TEMPLATE_NAME).exists()


def test_the_run_writes_only_the_output_file_and_prints_no_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _folder(tmp_path, [OFIS_REF])
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    engine = ScriptedSTT({OFIS_REF: OFIS_HEARD})
    monkeypatch.setattr(
        sc, "configured_engines", lambda env, **_kw: [sc.Engine("a", engine, destination="A")]
    )
    before = _tree(tmp_path)
    out = tmp_path / "r.json"
    assert sc.main(["--folder", str(folder), "--out", str(out)]) == sc.EXIT_OK
    after = _tree(tmp_path)
    assert set(after) - set(before) == {"r.json"}
    assert {name: data for name, data in after.items() if name != "r.json"} == before
    printed = capsys.readouterr()
    assert "Ofisü" not in printed.out + printed.err
    assert "hesap makinesini" not in printed.out + printed.err
    # the transcript is in the one file the caller named, and nowhere else
    assert "Ofisü" in out.read_text(encoding="utf-8")
