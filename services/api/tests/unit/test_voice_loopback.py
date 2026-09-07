"""Unit tests: the TTS -> STT loopback proxy (app.voice.loopback, VOICE_SPEC §7a).

Everything runs on the deterministic fakes - no network, no key, no device. What is
proven here is the LOOP and its judgement: a perfect loop matches, a recogniser that
loses a content word is degraded, one that loses the sentence is mismatched, a provider
failure is an ``error`` verdict and never a crash, the sampler is deterministic and
category-balanced, the CLI writes the evidence file, and the ledger row it prepares
passes the real ledger route's forbidden-key scan.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.ledger.vocabulary import (
    EVENT_TYPE_VOICE_TTS_LOOPBACK,
    EVENT_TYPES,
    validate_event_type,
)
from app.main import create_app
from app.voice import loopback, loopback_cli
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.providers import (
    FailingTTSProvider,
    FakeSTTProvider,
    FakeTTSProvider,
    ProviderCapabilities,
    STTResult,
    TTSResult,
    drop_last_word,
    lowercase_all,
    synthesize_wav,
    wav_duration_ms,
    wav_info,
)
from tests.identity_support import authenticate, install_identity

TR_CASES = [
    loopback.LoopbackCase("r.1", "Araştırma raporu hazır; iki bulgu bulundu.", "research"),
    loopback.LoopbackCase("a.1", "Alarm 7.30 için kuruldu.", "alarm"),
    loopback.LoopbackCase("e.1", "Gözümü kapattım ancak kaydını doğrulayamadım.", "eye"),
    loopback.LoopbackCase("ev.1", "Kendi kendini geliştirme duraklatıldı.", "evolution"),
]


# ------------------------------------------------------------ normalisation


def test_normalisation_unifies_digits_case_punctuation_and_diacritics() -> None:
    joined, tokens = loopback.normalize_for_comparison("Saat 7.30 için ALARM kuruldu, %17,2!")
    assert joined == "saat yedi otuz icin alarm kuruldu yuzde on yedi virgul iki"
    assert "alarm" in tokens
    folded, _ = loopback.normalize_for_comparison("araştırma  raporu")
    ascii_only, _ = loopback.normalize_for_comparison("arastirma raporu")
    assert folded == ascii_only == "arastirma raporu"


def test_content_words_skip_short_and_stop_words() -> None:
    _, tokens = loopback.normalize_for_comparison("Bunu şimdi araştırma için rapor et")
    assert loopback.content_words(tokens) == ("arastirma", "rapor")


def test_content_check_allows_one_edit_and_glued_words_but_not_a_missing_word() -> None:
    _, expected = loopback.normalize_for_comparison("araştırma raporu hazır on beş")
    # one edit: raporu -> rapору-like slip; glued: onbeş; missing: hazır
    _, transcript = loopback.normalize_for_comparison("araştırma raporo onbeş")
    assert loopback.missing_content_words(expected, transcript) == ["hazir"]


def test_verdict_thresholds() -> None:
    assert loopback.verdict_for(0.0, []) == loopback.VERDICT_MATCHED
    assert loopback.verdict_for(0.35, []) == loopback.VERDICT_MATCHED
    assert loopback.verdict_for(0.1, ["rapor"]) == loopback.VERDICT_DEGRADED
    assert loopback.verdict_for(0.5, []) == loopback.VERDICT_DEGRADED
    assert loopback.verdict_for(0.61, []) == loopback.VERDICT_MISMATCHED


# ------------------------------------------------------------------- the loop


def test_a_perfect_loop_matches_every_case_with_proxy_marks() -> None:
    report = loopback.run_loopback(TR_CASES, FakeTTSProvider(), FakeSTTProvider())
    assert report.totals == {"cases": 4, "matched": 4, "degraded": 0, "mismatched": 0, "error": 0}
    assert report.mean_wer == 0.0
    assert report.summary == "HEALTHY"
    marks = report.marks
    assert marks["audio_generation"]["mark"] == loopback.MARK_PROVEN_PROXY
    assert marks["loopback_semantics"]["mark"] == loopback.MARK_PROVEN_PROXY
    assert "no microphone" in marks["loopback_semantics"]["reason"]
    assert marks["physical_hearing"]["mark"] == loopback.MARK_NOT_CLAIMED
    stats = report.audio_stats
    assert stats["cases_with_audio"] == 4 and stats["formats"] == {"wav": 4}
    assert stats["total_ms"] > 0 and stats["min_ms"] <= stats["mean_ms"] <= stats["max_ms"]
    assert set(report.per_category) == {"research", "alarm", "eye", "evolution"}
    payload = report.to_dict()
    assert payload["providers"] == {
        "tts": "fake-tts", "stt": "fake-stt", "tts_is_fake": True, "stt_is_fake": True
    }
    assert payload["cases"][0]["transcript"] == TR_CASES[0].expected_speech


def test_the_fake_stt_variants_degrade_or_keep_matching_as_designed() -> None:
    dropped = loopback.run_loopback(
        TR_CASES, FakeTTSProvider(), FakeSTTProvider(error_profile=drop_last_word)
    )
    # the last word of each sentence is a content word: degraded, never matched
    assert {c.verdict for c in dropped.cases} == {loopback.VERDICT_DEGRADED}
    assert all(c.missing_content for c in dropped.cases)
    assert dropped.summary == "DEGRADED"

    lowered = loopback.run_loopback(
        TR_CASES, FakeTTSProvider(), FakeSTTProvider(error_profile=lowercase_all)
    )
    # case is not meaning: the Turkish casefold makes both sides identical
    assert {c.verdict for c in lowered.cases} == {loopback.VERDICT_MATCHED}

    gutted = loopback.run_loopback(
        TR_CASES, FakeTTSProvider(), FakeSTTProvider(error_profile=lambda toks: toks[:1])
    )
    assert {c.verdict for c in gutted.cases} == {loopback.VERDICT_MISMATCHED}
    assert gutted.summary == "REGRESSION_FOUND"


def test_a_tts_failure_is_an_error_verdict_that_names_the_stage_and_provider() -> None:
    report = loopback.run_loopback(TR_CASES[:2], FailingTTSProvider(), FakeSTTProvider())
    assert report.totals["error"] == 2
    assert report.cases[0].error.startswith("tts:failing-tts:dependency_unavailable")
    assert report.mean_wer is None
    # a non-fake provider that produced nothing proves nothing
    assert report.marks["audio_generation"]["mark"] == loopback.MARK_NOT_PROVEN
    assert report.summary == "REGRESSION_FOUND"


class _CrashingSTT:
    name = "crashing-stt"

    def capabilities(self) -> ProviderCapabilities:
        return FakeSTTProvider().capabilities()

    def transcribe(self, audio: bytes, *, language: str = "tr-TR") -> STTResult:
        raise RuntimeError("socket closed mid-stream")


def test_a_plain_exception_from_the_stt_is_an_error_verdict_never_a_crash() -> None:
    report = loopback.run_loopback(TR_CASES[:1], FakeTTSProvider(), _CrashingSTT())
    case = report.cases[0]
    assert case.verdict == loopback.VERDICT_ERROR
    assert case.error == "stt:crashing-stt:RuntimeError: socket closed mid-stream"
    assert case.audio_bytes > 0  # the synthesis itself is still recorded


class _RealLookingTTS:
    """A non-fake TTS (the way a keyed adapter looks to the loop) that still emits the
    deterministic WAV, so the PROVEN_AUTOMATED mark can be tested offline."""

    name = "vendor-tts"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name, kind="tts", languages=("tr-TR",), streaming=True,
            long_form_stability="medium", pronunciation_dict=False, voice_selection=True,
            speed_control=True, cost_metadata={"unit": "characters", "usd_per_1m": 15.0},
            output_formats=("wav",), latency_class="low", requires_api_key=True,
        )

    def synthesize(self, text: str, *, voice: str = "default", speed: float = 1.0,
                   fmt: str = "wav") -> TTSResult:
        audio = synthesize_wav(text)
        return TTSResult(audio=audio, audio_format="wav", duration_ms=0, latency_ms=0.0,
                         provider=self.name, voice=voice, char_count=len(text))


def test_a_real_provider_with_audio_for_every_case_is_proven_automated() -> None:
    report = loopback.run_loopback(TR_CASES, _RealLookingTTS(), FakeSTTProvider())
    assert report.tts_is_fake is False and report.stt_is_fake is True
    assert report.marks["audio_generation"]["mark"] == loopback.MARK_PROVEN_AUTOMATED
    assert report.marks["loopback_semantics"]["mark"] == loopback.MARK_PROVEN_PROXY
    assert all(c.audio_ms > 0 for c in report.cases)  # measured from the WAV header


def test_max_cases_truncates_the_run() -> None:
    report = loopback.run_loopback(TR_CASES, FakeTTSProvider(), FakeSTTProvider(), max_cases=2)
    assert [c.case_id for c in report.cases] == ["r.1", "a.1"]


def test_an_empty_speech_is_a_named_error_not_a_crash() -> None:
    report = loopback.run_loopback(
        [loopback.LoopbackCase("x", "   ", "misc")], FakeTTSProvider(), FakeSTTProvider()
    )
    assert report.cases[0].verdict == loopback.VERDICT_ERROR
    assert "validation_error" in report.cases[0].error


# ------------------------------------------------------------------ sampling


def _corpus_rows() -> list[dict]:
    rows = []
    for i in range(30):
        rows.append({"case_id": f"al.{i:02d}", "category": "alarm", "verdict": "correct",
                     "expected_response": "ok", "speech": f"Alarm {i} kuruldu."})
    for i in range(3):
        rows.append({"case_id": f"ev.{i}", "category": "evolution", "verdict": "correct",
                     "expected_response": "refused", "speech": f"Reddedildi {i}."})
    rows.append({"case_id": "op.0", "category": "operator", "verdict": "correct",
                 "expected_response": "ok", "speech": "Operatör hazır."})
    # excluded: a wrong route, a clarification, a running tool, an empty speech, a variant
    rows.append({"case_id": "al.bad", "category": "alarm", "verdict": "wrong_route",
                 "expected_response": "ok", "speech": "Yanlış."})
    rows.append({"case_id": "r.clar", "category": "research", "verdict": "correct",
                 "expected_response": "needs_clarification", "speech": "Hangisi?"})
    rows.append({"case_id": "r.run", "category": "research", "verdict": "correct",
                 "expected_response": "running", "speech": "Başladım."})
    rows.append({"case_id": "st.0", "category": "state", "verdict": "correct",
                 "expected_response": "ok", "speech": ""})
    rows.append({"case_id": "al.00.v1", "category": "alarm", "verdict": "correct",
                 "expected_response": "ok", "speech": "Alarm 0 kuruldu."})
    return rows


def test_corpus_rows_become_speakable_cases_deduplicated_by_speech() -> None:
    cases = loopback.cases_from_corpus_results(_corpus_rows())
    ids = [c.case_id for c in cases]
    assert "al.bad" not in ids and "r.clar" not in ids and "r.run" not in ids
    assert "st.0" not in ids
    assert "al.00" in ids and "al.00.v1" not in ids  # same sentence, synthesised once
    assert "op.0" in ids and "ev.2" in ids
    assert ids == sorted(ids)
    assert len(cases) == 30 + 3 + 1


def test_the_sampler_is_deterministic_and_category_balanced() -> None:
    cases = loopback.cases_from_corpus_results(_corpus_rows())
    first = loopback.sample_balanced(cases, max_cases=12, seed=7)
    again = loopback.sample_balanced(cases, max_cases=12, seed=7)
    assert [c.case_id for c in first] == [c.case_id for c in again]
    assert len(first) == 12
    by_cat = {}
    for c in first:
        by_cat[c.category] = by_cat.get(c.category, 0) + 1
    # the small categories are all in, the big one fills the rest
    assert by_cat == {"alarm": 8, "evolution": 3, "operator": 1}
    other = loopback.sample_balanced(cases, max_cases=12, seed=8)
    assert [c.case_id for c in other] != [c.case_id for c in first]
    assert {c.category for c in other} == {"alarm", "evolution", "operator"}


def test_the_sampler_honours_the_per_category_cap_and_the_budget() -> None:
    cases = loopback.cases_from_corpus_results(_corpus_rows())
    capped = loopback.sample_balanced(cases, max_cases=40, per_category=2, seed=1)
    counts = {}
    for c in capped:
        counts[c.category] = counts.get(c.category, 0) + 1
    assert counts == {"alarm": 2, "evolution": 2, "operator": 1}
    assert loopback.sample_balanced(cases, max_cases=0) == []
    assert len(loopback.sample_balanced(cases, max_cases=1000)) == len(cases)


# ------------------------------------------------------------------ WAV header


def _wav(rate: int, pcm: bytes, *, with_list: bool = False, data_size: int | None = None) -> bytes:
    fmt = struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    body = b"fmt " + fmt
    if with_list:
        body += b"LIST" + struct.pack("<I", 4) + b"INFO"
    body += b"data" + struct.pack("<I", len(pcm) if data_size is None else data_size) + pcm
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def test_wav_duration_reads_the_header_sample_rate_and_survives_a_list_chunk() -> None:
    pcm = b"\x00\x00" * 24000  # one second at 24 kHz
    assert wav_duration_ms(_wav(24000, pcm)) == 1000
    assert wav_duration_ms(_wav(24000, pcm, with_list=True)) == 1000
    assert wav_info(_wav(24000, pcm, with_list=True)) == (24000, 1, 16, 48000)
    # the fakes' 16 kHz WAV reads as before
    assert wav_duration_ms(synthesize_wav("selam")) == wav_duration_ms(
        _wav(16000, synthesize_wav("selam")[44:])
    )


def test_wav_duration_measures_a_streamed_unknown_length_by_the_bytes_present() -> None:
    pcm = b"\x00\x00" * 8000  # half a second at 16 kHz
    assert wav_duration_ms(_wav(16000, pcm, data_size=0)) == 500
    assert wav_duration_ms(_wav(16000, pcm, data_size=0xFFFFFFFF)) == 500
    assert wav_duration_ms(b"not a wav at all") == 0
    assert wav_info(b"RIFF\x00\x00\x00\x00WAVE") is None


# ------------------------------------------------------------------------ CLI


def test_the_cli_writes_a_report_from_a_corpus_report(tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "voice-routing.json"
    corpus.write_text(json.dumps({"results": _corpus_rows()}), encoding="utf-8")
    out = tmp_path / "evidence" / "tts-loopback.json"
    code = loopback_cli.main(
        ["--cases", str(corpus), "--provider", "fake", "--out", str(out), "--max-cases", "6"]
    )
    assert code == loopback_cli.EXIT_OK
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["suite"] == loopback.SUITE
    assert payload["totals"] == {
        "cases": 6, "matched": 6, "degraded": 0, "mismatched": 0, "error": 0
    }
    assert {c["category"] for c in payload["cases"]} == {"alarm", "evolution", "operator"}
    assert payload["marks"]["audio_generation"]["mark"] == loopback.MARK_PROVEN_PROXY
    assert payload["ledger"]["event_type"] == EVENT_TYPE_VOICE_TTS_LOOPBACK
    assert payload["seed"] == loopback.DEFAULT_SEED
    printed = capsys.readouterr().out
    assert "6 cases, 6 matched" in printed and "physical_hearing=NOT_CLAIMED" in printed


def test_the_cli_accepts_a_plain_case_list_and_file_order(tmp_path: Path) -> None:
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps([c.to_dict() for c in TR_CASES] + [{"case_id": "empty", "speech": ""}]),
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    code = loopback_cli.main(
        ["--cases", str(cases), "--out", str(out), "--no-sample", "--max-cases", "3"]
    )
    assert code == loopback_cli.EXIT_OK
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [c["case_id"] for c in payload["cases"]] == ["r.1", "a.1", "e.1"]
    assert payload["seed"] is None


def test_the_cli_refuses_bad_input_and_a_real_provider_without_a_key(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    missing = tmp_path / "missing.json"
    assert loopback_cli.main(["--cases", str(missing), "--out", str(tmp_path / "o.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text('{"nothing": true}', encoding="utf-8")
    assert loopback_cli.main(["--cases", str(bad), "--out", str(tmp_path / "o.json")]) == 2
    good = tmp_path / "good.json"
    good.write_text(json.dumps([TR_CASES[0].to_dict()]), encoding="utf-8")
    monkeypatch.delenv(loopback_cli.KEY_ENV, raising=False)
    code = loopback_cli.main(
        ["--cases", str(good), "--provider", "openai", "--out", str(tmp_path / "o.json")]
    )
    assert code == loopback_cli.EXIT_NO_KEY
    assert not (tmp_path / "o.json").exists()
    err = capsys.readouterr().err
    assert loopback_cli.KEY_ENV in err and "not set" in err


def test_a_real_provider_error_is_a_verdict_not_an_exit(monkeypatch, tmp_path: Path) -> None:
    """With a (fake) key in the environment the OpenAI adapters are built; a transport
    error from them lands in the report as an ``error`` verdict, exit code 0."""
    import app.voice.providers as providers_mod

    def refuse(req, *, timeout_s, provider):
        raise VoiceError(VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{provider}: HTTP 401",
                         provider=provider)

    monkeypatch.setattr(providers_mod, "_send", refuse)
    monkeypatch.setenv(loopback_cli.KEY_ENV, "unit-test-not-a-real-key")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([TR_CASES[0].to_dict()]), encoding="utf-8")
    out = tmp_path / "o.json"
    argv = ["--cases", str(cases), "--provider", "openai", "--out", str(out)]
    assert loopback_cli.main(argv) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["totals"]["error"] == 1
    assert payload["cases"][0]["error"].startswith("tts:openai:dependency_unavailable")
    assert payload["marks"]["audio_generation"]["mark"] == loopback.MARK_NOT_PROVEN
    assert "unit-test-not-a-real-key" not in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------- ledger


def test_the_event_type_is_part_of_the_ledger_vocabulary() -> None:
    assert EVENT_TYPE_VOICE_TTS_LOOPBACK == "voice.tts_loopback"
    assert EVENT_TYPE_VOICE_TTS_LOOPBACK in EVENT_TYPES
    assert validate_event_type(EVENT_TYPE_VOICE_TTS_LOOPBACK) == EVENT_TYPE_VOICE_TTS_LOOPBACK


@pytest.fixture()
def ledger_client() -> TestClient:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(engine)
    PendingBriefingRow.__table__.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    return client


def test_the_prepared_ledger_row_is_accepted_by_the_real_route(ledger_client: TestClient) -> None:
    """What the PowerShell script posts is exactly ``report["ledger"]``; the route's
    forbidden-key scan (no text/transcript/audio-shaped keys) and the vocabulary must
    accept it through the real application object."""
    report = loopback.run_loopback(
        TR_CASES, FakeTTSProvider(), FakeSTTProvider(error_profile=drop_last_word), seed=3
    ).to_dict()
    body = {
        "event_type": report["ledger"]["event_type"],
        "subsystem": "voice",
        "action": "tts_loopback_qualified",
        "factual_summary": report["ledger"]["factual_summary"],
        "source_ref": "tts_loopback:2026-09-07-180000",
        "status": "completed",
        "severity": "info",
        "result": report["summary"],
        "evidence_refs": [
            {"kind": "file", "ref": "docs/evidence/tts-loopback-2026-09-07-180000.json"}
        ],
        "detail_json": report["ledger"]["detail"],
    }
    response = ledger_client.post("/v1/ledger/events", json=body)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["event_type"] == EVENT_TYPE_VOICE_TTS_LOOPBACK
    assert row["detail_json"]["degraded"] == 4 and row["detail_json"]["seed"] == 3
    assert row["detail_json"]["marks"] == {
        "speech_generation": loopback.MARK_PROVEN_PROXY,
        "loopback_semantics": loopback.MARK_PROVEN_PROXY,
        "physical_hearing": loopback.MARK_NOT_CLAIMED,
    }
    assert "geri döngü" in row["factual_summary"]
    # the detail carries counts and ids, never a sentence
    assert "expected_speech" not in json.dumps(row["detail_json"])
    listed = ledger_client.get("/v1/ledger/events?subsystem=voice&limit=5").json()
    assert any(e["event_type"] == EVENT_TYPE_VOICE_TTS_LOOPBACK for e in listed["events"])
