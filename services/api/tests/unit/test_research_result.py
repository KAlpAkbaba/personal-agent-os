"""ResearchResult / ResearchDiagnostics / spoken_result (M18.2 DEFECT 2, ADR-0067).

The invariant this module protects: the owner-facing narration built from a
research run's validated report never contains the pipeline's own diagnostics
(counts of pages discovered/rejected, interstitials, dedup, candidates) — those
numbers exist, and are correct, but they belong to ``ResearchDiagnostics`` and the
technical/explicit-question path, never to what is spoken by default.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.research.evidence import STATEMENT_LABEL_SOURCE_FACT, EvidenceRecord
from app.research.policy import derive_mode_from_utterance
from app.research.report import (
    Finding,
    ReportStats,
    ReportWindow,
    ResearchReport,
    SourceItem,
)
from app.research.result import (
    BROADER_RUN_OFFER_TR,
    REASON_INSUFFICIENT_EVIDENCE,
    REASON_INSUFFICIENT_FINDINGS,
    ResearchDiagnostics,
    ResearchResult,
    build_insufficient_terminal_payload,
    build_tool_terminal_payload,
    spoken_result,
)

NOW = datetime(2026, 9, 6, tzinfo=UTC)

#: Every word that must never appear in a spoken conclusion — the pipeline's own
#: diagnostics vocabulary (M18.2 DEFECT 2's actual reported symptom).
FORBIDDEN_WORDS = ("elendi", "eledi", "interstitial", "tekrar", "dedup", "aday")


def _assert_no_diagnostics_leak(text: str) -> None:
    lowered = text.lower()
    for word in FORBIDDEN_WORDS:
        assert word not in lowered, f"{word!r} leaked into spoken_result: {text!r}"
    # "N sayfa" (a digit immediately followed by "sayfa") is the shape the reported
    # defect actually took ("28 uygun olmayan sayfayı eledi").
    for i, ch in enumerate(text):
        if ch.isdigit():
            rest = text[i:].lstrip("0123456789")
            assert not rest.lstrip().startswith("sayfa"), f"digit+sayfa leaked: {text!r}"


def _evidence(eid: str, url: str, title: str, excerpt: str = "kaynak metni") -> EvidenceRecord:
    return EvidenceRecord(
        id=eid,
        url=url,
        title=title,
        excerpt=excerpt,
        fetched_at=NOW,
        extraction_method="dom_text",
        source_class="news",
        publisher="Kaynak Yayın",
    )


def _real_report(
    topic: str = "OpenAI, Anthropic ve Google karşılaştırması",
    *,
    findings_count: int = 3,
    rejected: int = 28,
    discovered: int = 240,
) -> dict:
    """A validated report shaped exactly like the pipeline actually produces one
    (app.research.synthesis.DeterministicSynthesisProvider), stats included — the
    same place a real run's diagnostics genuinely live."""
    findings = tuple(
        Finding(
            id=f"f{i + 1}",
            title=f"Bulgu Başlığı {i + 1}",
            summary=f"Kaynak: Yayın {i + 1} — Bulgu Başlığı {i + 1} (2026-09-05).",
            why_it_matters=(
                f"Bu bilgi doğrulanmış kaynaktan geldi ve konuyla doğrudan ilgili {i + 1}."
            ),
            importance=5 - i,
            label=STATEMENT_LABEL_SOURCE_FACT,
            evidence_ids=(f"e{i + 1}",),
        )
        for i in range(findings_count)
    )
    sources = tuple(
        SourceItem.from_evidence(
            f"e{i + 1}",
            _evidence(f"e{i + 1}", f"https://kaynak{i + 1}.example.com/x", f"Kaynak {i + 1}"),
        )
        for i in range(findings_count)
    )
    report = ResearchReport(
        task_id="11111111-1111-1111-1111-111111111111",
        topic=topic,
        window=ReportWindow(start=NOW.isoformat(), end=NOW.isoformat(), label="son 3 gün"),
        generated_at=NOW.isoformat(),
        synthesis_provider="deterministic",
        executive_summary=(
            f"'{topic}' konusunda son 3 gün kapsamında {discovered} kaynak incelendi."
        ),
        findings=findings,
        sources=sources,
        stats=ReportStats(
            discovered=discovered,
            fetched=33,
            evidence=findings_count,
            rejected=rejected,
            rejected_by_reason={"interstitial": 11, "off_topic": 9, "duplicate_event": 8},
            deduplicated=28,
            quarantined=2,
            injection_dropped=1,
        ),
    )
    return report.as_dict()


# ------------------------------------------------------------------ from_report_json


def test_from_report_json_reads_findings_never_counts() -> None:
    report = _real_report()
    result = ResearchResult.from_report_json(report)
    assert result.topic == "OpenAI, Anthropic ve Google karşılaştırması"
    assert len(result.findings) == 3
    assert result.findings[0].finding == "Bulgu Başlığı 1"
    assert result.findings[0].why_it_matters.startswith("Bu bilgi doğrulanmış")
    assert result.findings[0].sources == ("e1",)
    assert not result.insufficient
    # Carried through for the record, not for speech — see spoken_result tests below.
    assert "240" in result.executive_summary


def test_from_report_json_reads_sources_by_host() -> None:
    report = _real_report()
    result = ResearchResult.from_report_json(report)
    assert len(result.sources) == 3
    assert result.sources[0].url_host == "kaynak1.example.com"
    assert result.sources[0].ref == "e1"


def test_from_report_json_skips_malformed_findings_without_raising() -> None:
    report = {"topic": "x", "findings": [{"title": ""}, "not-a-dict", {"title": "Geçerli"}]}
    result = ResearchResult.from_report_json(report)
    assert [f.finding for f in result.findings] == ["Geçerli"]


def test_from_report_json_empty_report_is_no_findings_not_a_crash() -> None:
    result = ResearchResult.from_report_json(None)
    assert result.findings == ()
    assert result.topic == ""
    assert not result.insufficient


# ------------------------------------------------------------------ spoken_result determinism


def test_spoken_result_is_deterministic() -> None:
    report = _real_report()
    result = ResearchResult.from_report_json(report)
    assert spoken_result(result) == spoken_result(ResearchResult.from_report_json(report))


def test_spoken_result_names_the_topic_and_up_to_three_findings() -> None:
    report = _real_report(findings_count=3)
    result = ResearchResult.from_report_json(report)
    speech = spoken_result(result)
    assert speech.startswith("Efendim, 'OpenAI, Anthropic ve Google karşılaştırması'")
    assert "Birincisi, Bulgu Başlığı 1." in speech
    assert "İkincisi, Bulgu Başlığı 2." in speech
    assert "Üçüncüsü, Bulgu Başlığı 3." in speech
    assert "Bu önemli çünkü" in speech
    assert speech.endswith("İstersen diğer bulguları veya kaynakları da anlatabilirim.")


def test_spoken_result_caps_at_three_findings_and_offers_more() -> None:
    report = _real_report(findings_count=5)
    result = ResearchResult.from_report_json(report)
    speech = spoken_result(result)
    assert "Birincisi" in speech and "İkincisi" in speech and "Üçüncüsü" in speech
    assert "Bulgu Başlığı 4" not in speech and "Bulgu Başlığı 5" not in speech
    assert speech.endswith("İstersen diğer bulguları veya kaynakları da anlatabilirim.")


def test_spoken_result_never_leaks_diagnostics() -> None:
    """The exact invariant M18.2 DEFECT 2 broke: none of the pipeline's own
    diagnostics vocabulary or counts may appear in the spoken narration, no matter
    how many pages were discovered, fetched or rejected building the report."""
    report = _real_report(rejected=28, discovered=240)
    result = ResearchResult.from_report_json(report)
    speech = spoken_result(result)
    _assert_no_diagnostics_leak(speech)


def test_spoken_result_no_findings_says_so_without_diagnostics() -> None:
    result = ResearchResult(topic="boş konu")
    speech = spoken_result(result)
    assert "bulgu çıkaramadım" in speech
    _assert_no_diagnostics_leak(speech)


# ------------------------------------------------------------------ insufficient evidence


def test_insufficient_findings_wording_is_honest_and_concise() -> None:
    result = ResearchResult.insufficient_evidence(reason=REASON_INSUFFICIENT_FINDINGS)
    speech = spoken_result(result)
    assert speech.startswith("Bu konuda yeterli doğrulanmış kaynak bulamadım")
    assert "bulgu çıkaramadım" in speech
    _assert_no_diagnostics_leak(speech)


def test_insufficient_evidence_wording_names_the_pages_not_the_findings() -> None:
    result = ResearchResult.insufficient_evidence(reason=REASON_INSUFFICIENT_EVIDENCE)
    speech = spoken_result(result)
    assert "geçerli kanıt elde edemedim" in speech
    _assert_no_diagnostics_leak(speech)


def test_insufficient_with_unknown_reason_still_answers_honestly() -> None:
    result = ResearchResult.insufficient_evidence(reason=None)
    speech = spoken_result(result)
    assert speech == "Bu konuda yeterli doğrulanmış kaynak bulamadım."


# ------------------------------------------------------------------ ResearchDiagnostics


def test_diagnostics_reads_the_report_stats_never_fabricating() -> None:
    report = _real_report(rejected=28, discovered=240)
    diag = ResearchDiagnostics.from_report_json(report)
    assert diag.discovered_count == 240
    assert diag.fetched_count == 33
    assert diag.rejected_pages == 28
    assert diag.rejected_by_reason == {"interstitial": 11, "off_topic": 9, "duplicate_event": 8}
    assert diag.quarantined_pages == 2
    assert diag.refused_pages == 1
    assert diag.dedup_stats == {"deduplicated": 28}
    assert diag.synthesis_provider == "deterministic"
    # Never fabricated: nothing the pipeline did not record stays honestly empty.
    assert diag.timings == {}
    assert diag.provider_errors == ()


def test_diagnostics_carries_the_fast_path_fields(monkeypatch) -> None:
    """M18.2 (ADR-0068): mode, budget, elapsed time, wave count and the
    challenge/cooldown counters are TECHNICAL-only telemetry, exactly like every
    other field ResearchDiagnostics carries - never spoken by spoken_result."""
    report = _real_report()
    report["stats"].update(
        {
            "mode": "quick",
            "budget_s": 120.0,
            "elapsed_s": 87.5,
            "waves": 2,
            "challenged_pages": 3,
            "cooled_domains": 1,
        }
    )
    diag = ResearchDiagnostics.from_report_json(report)
    assert diag.mode == "quick"
    assert diag.budget_s == 120.0
    assert diag.elapsed_s == 87.5
    assert diag.waves == 2
    assert diag.challenged_pages == 3
    assert diag.cooled_domains == 1

    result = ResearchResult.from_report_json(report)
    spoken = spoken_result(result)
    for banned in ("quick", "120", "87.5", "challenged", "cooled"):
        assert banned not in spoken.lower()


def test_diagnostics_of_a_missing_report_is_all_zero_not_fabricated() -> None:
    diag = ResearchDiagnostics.from_report_json(None)
    assert diag.discovered_count == 0
    assert diag.fetched_count == 0
    assert diag.rejected_pages == 0
    assert diag.rejected_by_reason == {}
    assert diag.quarantined_pages == 0
    assert diag.refused_pages == 0
    assert diag.dedup_stats == {"deduplicated": 0}
    assert diag.synthesis_provider == ""
    assert diag.mode == ""
    assert diag.budget_s == 0.0
    assert diag.elapsed_s == 0.0
    assert diag.waves == 0
    assert diag.challenged_pages == 0
    assert diag.cooled_domains == 0


# ------------------------------------------------------------------ tool terminal payload


def test_tool_terminal_payload_schema_and_no_diagnostics_leak_in_spoken_result() -> None:
    report = _real_report()
    payload = build_tool_terminal_payload(report)
    assert set(payload) == {
        "spoken_result",
        "speech",
        "executive_summary",
        "findings",
        "source_summary",
        # ADR-0074: the one owner-facing bit that says this answer rests on fewer
        # sources than a full report needs. False for an ordinary report.
        "thin",
        "diagnostics",
    }
    assert payload["thin"] is False
    # M18.2 follow-up to ADR-0067: "speech" mirrors "spoken_result" verbatim, so the
    # persona's generic "read speech verbatim" instruction and session_activity's
    # speech_head see the same sentence a research-specific reader gets from
    # "spoken_result".
    assert payload["speech"] == payload["spoken_result"]
    _assert_no_diagnostics_leak(payload["spoken_result"])
    assert payload["findings"][0]["finding"] == "Bulgu Başlığı 1"
    assert payload["source_summary"][0]["ref"] == "e1"
    assert payload["diagnostics"]["discovered_count"] == 240
    assert payload["diagnostics"]["rejected_pages"] == 28


def test_insufficient_terminal_payload_has_no_findings_and_honest_diagnostics() -> None:
    payload = build_insufficient_terminal_payload(reason=REASON_INSUFFICIENT_FINDINGS)
    assert payload["findings"] == []
    assert payload["source_summary"] == []
    assert payload["thin"] is False
    assert payload["diagnostics"] == ResearchDiagnostics().as_dict()
    assert payload["speech"] == payload["spoken_result"]
    _assert_no_diagnostics_leak(payload["spoken_result"])


# ------------------------------------------------- the thin answer (ADR-0074)


def _thin_report(findings_count: int = 2) -> dict:
    report = _real_report(findings_count=findings_count, discovered=103, rejected=8)
    report["thin"] = True
    report["executive_summary"] = (
        f"Kısa araştırma bütçesinde yalnızca {findings_count} kaynak doğrulanabildi; "
        "bulgular sınırlı."
    )
    report["stats"]["thin"] = True
    report["stats"]["thin_reasons"] = ["evidence_thin", "cooled_domains"]
    return report


def test_a_thin_answer_is_spoken_as_thin_and_offers_a_broader_run() -> None:
    """ADR-0074: the owner heard "yeterli doğrulanmış kaynak bulamadım" for a run that
    HAD findings. A thin answer speaks its findings, says plainly how little it stands
    on, and offers to go wider — without a single count or crawler word."""
    result = ResearchResult.from_report_json(_thin_report())
    assert result.thin is True
    assert result.insufficient is False

    spoken = spoken_result(result)
    _assert_no_diagnostics_leak(spoken)
    # It says it is thin ...
    assert "sınırlı" in spoken
    assert "yalnızca" in spoken
    # ... it still says what it found ...
    assert "Birincisi" in spoken
    assert "Bulgu Başlığı 1" in spoken
    # ... and it offers the broader run, in the words the mode derivation understands.
    assert spoken.endswith(BROADER_RUN_OFFER_TR)
    assert derive_mode_from_utterance(BROADER_RUN_OFFER_TR) == "standard"
    # Never a number: the thinness is STATED, never counted. (The findings themselves
    # are the report's own text and may carry whatever digits the sources did; the
    # sentence this module composes may not.)
    conclusion = spoken.split("Birincisi")[0]
    assert not any(ch.isdigit() for ch in conclusion)
    assert not any(ch.isdigit() for ch in BROADER_RUN_OFFER_TR)


def test_a_thin_answer_never_reads_as_a_failure() -> None:
    """The two must not be confused: `insufficient` says "no defensible answer",
    `thin` says "a real answer, from few sources"."""
    thin = spoken_result(ResearchResult.from_report_json(_thin_report()))
    failed = spoken_result(
        ResearchResult.insufficient_evidence(topic="x", reason=REASON_INSUFFICIENT_FINDINGS)
    )
    assert thin != failed
    assert "bulamadım" not in thin
    assert "çıkaramadım" not in thin


def test_a_thin_answer_with_no_findings_is_still_the_no_findings_sentence() -> None:
    """Defensive: `thin` never invents a finding-shaped narration out of nothing."""
    report = _thin_report()
    report["findings"] = []
    spoken = spoken_result(ResearchResult.from_report_json(report))
    assert "çıkaramadım" in spoken


def test_thin_terminal_payload_flags_thinness_and_keeps_the_reasons_in_diagnostics() -> None:
    payload = build_tool_terminal_payload(_thin_report())
    assert payload["thin"] is True
    assert payload["diagnostics"]["thin"] is True
    assert payload["diagnostics"]["thin_reasons"] == ["evidence_thin", "cooled_domains"]
    # The reason codes are technical vocabulary and stay out of the speech.
    for code in payload["diagnostics"]["thin_reasons"]:
        assert code not in payload["spoken_result"]
    assert payload["speech"] == payload["spoken_result"]
    _assert_no_diagnostics_leak(payload["spoken_result"])


def test_an_ordinary_report_is_not_thin() -> None:
    result = ResearchResult.from_report_json(_real_report())
    assert result.thin is False
    assert spoken_result(result).endswith(
        "İstersen diğer bulguları veya kaynakları da anlatabilirim."
    )
