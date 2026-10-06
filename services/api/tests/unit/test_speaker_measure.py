"""Unit tests: ``app.voice.speaker_measure`` (speaker-engine-measure; measurement only).

The pure half of the speaker-engine measurement: EER, DER, the bands of ``app.voice.speaker``,
the splice plan, the evidence schema and the Turkish page. Every expected number is a literal
worked out by hand in the comment beside it - never computed by the code under test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.voice import speaker
from app.voice import speaker_measure as sm

REPO = Path(sm.__file__).resolve().parents[4]
FORBIDDEN_KEYS = ("vector", "embedding", "audio", "transcript")


# ------------------------------------------------------------------------- EER


def test_eer_hand_written_lists_is_the_crossing_point_literal() -> None:
    # same 0.9 0.8 0.6 0.55 (4); different 0.1 0.15 0.2 0.3 0.58 0.62 0.65 0.7 (8).
    # threshold 0.55: FRR 0/4, FAR 4/8 -> |d| .5; 0.58: FRR 1/4, FAR 4/8 -> .25;
    # 0.6: FRR 1/4, FAR 3/8 -> .125; 0.62: FRR 2/4, FAR 3/8 -> .125 (tie: the lower wins);
    # 0.8: FRR 2/4, FAR 0 -> .5. The crossing is at 0.6: (25 + 37.5) / 2 = 31.25 %.
    # (The minimum error, (FRR + FAR) / 2, is 25 % at 0.55 and 0.8 - NOT the EER.)
    same = [0.9, 0.8, 0.6, 0.55]
    diff = [0.1, 0.15, 0.2, 0.3, 0.58, 0.62, 0.65, 0.7]
    result = sm.eer(same, diff)
    assert result.eer_pct == 31.25
    assert result.threshold == 0.6


def test_eer_is_zero_for_perfectly_separated_lists() -> None:
    result = sm.eer([0.9, 0.8, 0.85], [0.1, 0.2, 0.3])
    assert result.eer_pct == 0.0
    assert result.threshold == 0.8


def test_eer_is_fifty_percent_for_identical_lists() -> None:
    # 0.5: FRR 0, FAR 1; 0.6: FRR .5, FAR .5 -> 50 %
    result = sm.eer([0.5, 0.6], [0.5, 0.6])
    assert result.eer_pct == 50.0
    assert result.threshold == 0.6


def test_eer_needs_both_lists() -> None:
    with pytest.raises(ValueError):
        sm.eer([0.5], [])


# ------------------------------------------------------------------------- DER


def _seg(start: float, end: float, who: str) -> sm.Segment:
    return sm.Segment(start, end, who)


REFERENCE = [_seg(0.0, 5.0, "sahip"), _seg(5.0, 10.0, "konuk")]


def test_der_exact_match_is_zero() -> None:
    result = sm.der(REFERENCE, [_seg(0.0, 5.0, "0"), _seg(5.0, 10.0, "1")], collar_s=0.0)
    assert result.der_pct == 0.0
    assert result.scored_s == 10.0


def test_der_one_swapped_label_is_zero_because_both_mappings_are_tried() -> None:
    # the hypothesis calls the owner "1" and the guest "0": the other mapping is exact
    result = sm.der(REFERENCE, [_seg(0.0, 5.0, "1"), _seg(5.0, 10.0, "0")], collar_s=0.0)
    assert result.der_pct == 0.0
    assert result.mapping == {"1": "sahip", "0": "konuk"}


def test_der_one_second_missed_on_ten_seconds_of_speech_is_ten_percent() -> None:
    result = sm.der(REFERENCE, [_seg(0.0, 5.0, "a"), _seg(5.0, 9.0, "b")], collar_s=0.0)
    assert result.der_pct == 10.0
    assert result.missed_s == 1.0
    assert (result.false_alarm_s, result.confusion_s) == (0.0, 0.0)


def test_der_collar_is_applied_around_every_reference_boundary() -> None:
    # the hypothesis puts the turn 0.2 s late: 2 % with no collar, 0 % with 0.25 s
    late = [_seg(0.0, 5.2, "a"), _seg(5.2, 10.0, "b")]
    assert sm.der(REFERENCE, late, collar_s=0.0).der_pct == 2.0
    collared = sm.der(REFERENCE, late, collar_s=0.25)
    assert collared.der_pct == 0.0
    # scored: 10 - 0.25 (at 0) - 0.5 (at 5) - 0.25 (at 10) = 9 s
    assert collared.scored_s == 9.0
    # the 1 s miss under the collar: 9.0..9.75 is scored, 9.75..10 is not -> 0.75 / 9
    missed = sm.der(REFERENCE, [_seg(0.0, 5.0, "a"), _seg(5.0, 9.0, "b")], collar_s=0.25)
    assert missed.der_pct == pytest.approx(8.3333, abs=1e-4)


def test_der_default_collar_is_a_quarter_second() -> None:
    assert sm.COLLAR_S == 0.25


# ----------------------------------------------------------------------- bands


def test_band_thresholds_are_speaker_py_by_identity() -> None:
    assert sm.SpeakerThresholds is speaker.SpeakerThresholds
    # verify_speaker: OWNER iff score >= 0.75, NOT_OWNER iff score <= 0.45
    bands = sm.band_counts([0.9, 0.75, 0.74, 0.5], [0.1, 0.45, 0.46, 0.8])
    assert (bands.owner_accept, bands.not_owner_max) == (0.75, 0.45)
    assert (bands.same_below_accept, bands.same_total) == (2, 4)  # 0.74, 0.5
    assert (bands.diff_above_reject, bands.diff_total) == (2, 4)  # 0.46, 0.8
    # strictly between 0.45 and 0.75: 0.74, 0.5, 0.46 -> 3 of 8
    assert bands.uncertain_share == 0.375


def test_a_monkeypatched_owner_accept_moves_the_count(monkeypatch: pytest.MonkeyPatch) -> None:
    init: Any = speaker.SpeakerThresholds.__init__
    monkeypatch.setattr(init, "__defaults__", (0.95, 0.45, 0.10))
    bands = sm.band_counts([0.9, 0.75, 0.74, 0.5], [0.1])
    assert bands.owner_accept == 0.95
    assert bands.same_below_accept == 4


# ------------------------------------------------------------- RTF, percentiles


def test_rtf_and_nearest_rank_percentiles() -> None:
    assert sm.rtf(500.0, 2000.0) == 0.25
    assert sm.rtf(1.0, 0.0) is None
    values = [float(v) for v in range(1, 21)]  # 1..20
    assert sm.percentile(values, 95) == 19.0  # ceil(0.95 * 20) = 19th
    assert sm.percentile(values, 50) == 10.0
    assert sm.percentile([], 95) is None


# ----------------------------------------------------------------- splice plan


def test_the_splice_plan_is_deterministic_and_inside_its_bounds() -> None:
    first = sm.splice_plan(70.0, 120.0, seed=sm.SPLICE_SEED)
    second = sm.splice_plan(70.0, 120.0, seed=sm.SPLICE_SEED)
    assert first == second
    assert len(first) >= 10
    assert sm.splice_plan(70.0, 120.0, seed=sm.SPLICE_SEED + 1) != first
    used = {"sahip": 0.0, "konuk": 0.0}
    clock = 0.0
    for index, segment in enumerate(first):
        assert segment.speaker == ("sahip" if index % 2 == 0 else "konuk")
        assert 2.0 <= segment.duration_s <= 8.0
        assert 0.3 <= segment.gap_after_s <= 1.0
        assert segment.start_s == pytest.approx(clock)
        assert segment.source_start_s == pytest.approx(used[segment.speaker])
        used[segment.speaker] += segment.duration_s
        clock = segment.start_s + segment.duration_s + segment.gap_after_s
    assert used["sahip"] <= 70.0
    assert used["konuk"] <= 120.0
    reference = sm.reference_from_plan(first)
    assert [r.speaker for r in reference] == [s.speaker for s in first]
    assert reference[1].start_s == pytest.approx(first[0].duration_s + first[0].gap_after_s)


# ---------------------------------------------------------- evidence and page


def _walk_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [k for k in value] + [k2 for v in value.values() for k2 in _walk_keys(v)]
    if isinstance(value, list):
        return [k for v in value for k in _walk_keys(v)]
    return []


def _ran_model(model: str, eer: float) -> dict[str, Any]:
    return sm.model_row(
        model,
        same=[0.9, 0.8, 0.7, 0.85],
        diff=[0.1, 0.2, 0.5, eer],
        embed_runs=[(400.0, 4000.0), (500.0, 4000.0)],
        load_ms=900.0,
        peak_rss_mb=512.0,
    )


def _report(tmp_path: Path, *, consent: bool = True) -> dict[str, Any]:
    models = [
        _ran_model(sm.MODEL_IDS[0], 0.3),
        sm.left_out_row(sm.MODEL_IDS[1], "STOP: lisans"),
        sm.left_out_row(sm.MODEL_IDS[2], "kurulu değil"),
    ]
    if not consent:
        models[0] = sm.model_row(
            sm.MODEL_IDS[0],
            same=[0.9, 0.8],
            diff=None,
            embed_runs=[(400.0, 4000.0)],
            load_ms=900.0,
            peak_rss_mb=512.0,
        )
    shape = sm.build_shape(
        label="ev-pc",
        threads=8,
        cpus_limit="",
        cpu="Fake CPU",
        image="pagentos-speaker-measure:abc",
        consent=consent,
        synthetic=True,
        inputs={"sahip_cumle": 20, "sahip_uzun_s": None, "konuk_s": 90.0, "konusma_s": None},
        models=models,
        diarization=[],
        listen_dir=str(tmp_path / "listen" / "ev-pc"),
    )
    return sm.merge_report(None, shape)


def test_the_evidence_schema_has_a_version_and_no_forbidden_key(tmp_path: Path) -> None:
    assert sm.SCHEMA_VERSION
    report = _report(tmp_path)
    assert report["schema_version"] == sm.SCHEMA_VERSION
    for keys in (_walk_keys(sm.EVIDENCE_SCHEMA), _walk_keys(report)):
        assert keys
        for key in keys:
            assert not any(word in key.lower() for word in FORBIDDEN_KEYS), key


def test_a_left_out_model_renders_as_a_reason_row_never_zeros(tmp_path: Path) -> None:
    text = sm.render_tr(_report(tmp_path))
    [stop_row] = [line for line in text.splitlines() if line.startswith(f"{sm.MODEL_IDS[1]} |")]
    assert "STOP: lisans" in stop_row
    cells = [cell.strip() for cell in stop_row.split("|")[1:]]
    assert all(cell not in {"0", "0,0", "0,00", "0/0", "%0"} for cell in cells), stop_row
    [missing_row] = [line for line in text.splitlines() if line.startswith(f"{sm.MODEL_IDS[2]} |")]
    assert "kurulu değil" in missing_row


def test_without_consent_the_guest_columns_say_so(tmp_path: Path) -> None:
    text = sm.render_tr(_report(tmp_path, consent=False))
    [row] = [line for line in text.splitlines() if line.startswith(f"{sm.MODEL_IDS[0]} |")]
    assert "rızasız: ölçülmedi" in row


def test_verdict_negates_adoption_and_claims_no_quality(tmp_path: Path) -> None:
    report = _report(tmp_path)
    verdict = report["verdict_tr"]
    lowered = verdict.lower()
    assert "benimseme" in lowered
    assert "verilmez" in lowered or "değildir" in lowered
    assert "tek bir kişi" in lowered
    for claim in ("iyi", "yeterli", "öneri", "kullanılmalı", "benimsensin", "başarılı"):
        assert claim not in lowered.split(), claim
    assert "benimseme kararı burada verilmez" in lowered


def test_render_tr_names_no_path_inside_the_repository(tmp_path: Path) -> None:
    report = _report(tmp_path)
    text = sm.render_tr(report)
    assert str(REPO) not in text
    assert "Silindi" in text
    inside = _report(tmp_path)
    inside["shapes"][0]["listen_dir"] = str(REPO / "docs" / "evidence" / "listen")
    with pytest.raises(ValueError):
        sm.render_tr(inside)


def test_the_page_has_the_real_cpx32_not_run_line_and_the_shape_tables(tmp_path: Path) -> None:
    text = sm.render_tr(_report(tmp_path))
    assert "cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)" in text
    assert "## ev-pc" in text
    assert "sentetik ses" in text
