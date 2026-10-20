"""Unit tests for Editing Pacing Analyzer, Kinetic Word Enricher & EDL Snapshot/Rollback Engine.

Tests:
1. PacingAuditReport schema and validation.
2. analyze_edit_rhythm on slow/silent vs fast/dynamic word streams.
3. optimize_edl_for_retention:
   - Dead-air pauses excised via JumpCuts.
   - ZoomPunch keyframes injected on high-impact / emphasis words.
   - No static interval > 8 seconds.
   - BgmDuck generated for rapid speech bursts.
   - Retention pacing score improvement verified.
4. Workflow snapshot persistence: save, list, and restore round-trip.
5. Edge cases: empty words, missing directories, grade boundaries.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
from autoshorts.edit_planner.workflow_optimizer import (
    BgmDuck,
    PacingAuditReport,
    analyze_edit_rhythm,
    detect_rapid_speech_bursts,
    list_workflow_snapshots,
    optimize_edl_for_retention,
    restore_workflow_snapshot,
    save_workflow_snapshot,
)
from autoshorts.media.ass_styler import TimedWord


def test_pacing_audit_report_schema():
    """Verify PacingAuditReport field constraints and validation."""
    report = PacingAuditReport(
        clip_duration_sec=30.0,
        effective_duration_sec=25.0,
        word_count=75,
        words_per_minute=180.0,
        silence_ratio=0.08,
        visual_events_per_10s=2.4,
        hook_word_density_first_3s=2.33,
        retention_pacing_score=94.5,
        pacing_grade="A+",
        recommendations=["Optimal retention pacing: rapid delivery, tight silences."],
    )

    assert report.clip_duration_sec == 30.0
    assert report.effective_duration_sec == 25.0
    assert report.word_count == 75
    assert report.words_per_minute == 180.0
    assert report.silence_ratio == 0.08
    assert report.visual_events_per_10s == 2.4
    assert report.hook_word_density_first_3s == 2.33
    assert report.retention_pacing_score == 94.5
    assert report.pacing_grade == "A+"
    assert len(report.recommendations) == 1

    # Score out of bounds must fail
    with pytest.raises(ValidationError):
        PacingAuditReport(
            clip_duration_sec=30.0,
            effective_duration_sec=25.0,
            word_count=10,
            words_per_minute=20.0,
            silence_ratio=0.5,
            visual_events_per_10s=0.0,
            hook_word_density_first_3s=0.5,
            retention_pacing_score=105.0,  # Invalid: > 100
            pacing_grade="A",
        )


def test_analyze_edit_rhythm_slow_silent_stream():
    """Verify analyze_edit_rhythm detects low WPM, massive silence, and poor pacing grade."""
    slow_words = [
        TimedWord(word="Wait", start=1.5, end=2.0),
        TimedWord(word="what", start=5.0, end=5.5),
        TimedWord(word="happened", start=9.0, end=9.8),
    ]
    edl = EditDecisionList(hook_in_sec=0.0, hook_out_sec=20.0)

    report = analyze_edit_rhythm(slow_words, edl, clip_duration=20.0)

    assert report.clip_duration_sec == 20.0
    assert report.effective_duration_sec == 20.0
    assert report.word_count == 3
    assert report.words_per_minute < 60.0
    assert report.silence_ratio > 0.80  # More than 80% silence
    assert report.visual_events_per_10s == 0.0
    assert report.hook_word_density_first_3s < 1.0
    assert report.retention_pacing_score < 40.0
    assert report.pacing_grade == "D"

    # Must provide concrete recommendations
    rec_text = " ".join(report.recommendations)
    assert "High dead-air ratio" in rec_text
    assert "Auto-Cut Silence" in rec_text
    assert "Low visual movement" in rec_text
    assert "Slow spoken pace" in rec_text


def test_analyze_edit_rhythm_fast_dynamic_stream():
    """Verify analyze_edit_rhythm gives A+ for high WPM, tight silence, and dense visual events."""
    words: list[TimedWord] = []
    # Generate 55 tightly spaced words over 18 seconds (~183 WPM)
    # First 3s has 7 words (~2.33 words/s hook density)
    cur_t = 0.1
    for i in range(55):
        w_text = "word" if i % 6 != 0 else ("MASSIVE" if i % 12 == 0 else "truth")
        words.append(TimedWord(word=w_text, start=round(cur_t, 2), end=round(cur_t + 0.28, 2)))
        cur_t += 0.32

    # Clip is 20s. Add 1 jump cut excising the 18.0 - 19.5 end pause
    # and 4 zoom punches -> 5 visual events / 18.5s = ~2.7 events / 10s
    edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=20.0,
        jump_cuts=[JumpCut(cut_start=18.0, cut_end=19.5, reason="silence")],
        zoom_punch_ins=[
            ZoomPunchIn(timestamp=2.0, scale=1.16, duration=0.6, emphasis_word="MASSIVE"),
            ZoomPunchIn(timestamp=6.0, scale=1.15, duration=0.6, emphasis_word="truth"),
            ZoomPunchIn(timestamp=10.0, scale=1.16, duration=0.6, emphasis_word="MASSIVE"),
            ZoomPunchIn(timestamp=14.0, scale=1.15, duration=0.6, emphasis_word="truth"),
        ],
    )

    report = analyze_edit_rhythm(words, edl, clip_duration=20.0)

    assert 150.0 <= report.words_per_minute <= 210.0
    assert report.silence_ratio < 0.25
    assert report.visual_events_per_10s >= 2.0
    assert report.hook_word_density_first_3s >= 2.0
    assert report.retention_pacing_score >= 80.0
    assert report.pacing_grade in ("A+", "A")


def test_analyze_edit_rhythm_dict_words():
    """Verify analyze_edit_rhythm supports dict representation of TimedWord."""
    raw_dicts = [
        {"word": "First", "start": 0.2, "end": 0.6},
        {"word": "Second", "start": 0.7, "end": 1.1},
        {"word": "Third", "start": 1.2, "end": 1.7},
    ]
    edl = EditDecisionList(hook_in_sec=0.0, hook_out_sec=5.0)

    report = analyze_edit_rhythm(raw_dicts, edl, clip_duration=5.0)
    assert report.word_count == 3
    assert report.clip_duration_sec == 5.0
    assert report.words_per_minute > 0.0


def test_analyze_edit_rhythm_empty_words():
    """Verify analyze_edit_rhythm gracefully handles empty word sequence."""
    edl = EditDecisionList(hook_in_sec=0.0, hook_out_sec=10.0)
    report = analyze_edit_rhythm([], edl, clip_duration=10.0)

    assert report.word_count == 0
    assert report.words_per_minute == 0.0
    assert report.silence_ratio == 1.0
    assert report.retention_pacing_score < 30.0
    assert report.pacing_grade == "D"


def test_optimize_edl_for_retention_dead_air_and_score_improvement():
    """Verify optimize_edl_for_retention detects dead-air pauses and boosts pacing score."""
    words = [
        TimedWord(word="Listen", start=0.2, end=0.6),
        TimedWord(word="to", start=0.65, end=0.8),
        TimedWord(word="this", start=0.85, end=1.1),
        TimedWord(word="SECRET", start=1.15, end=1.6),
        # 1.2s dead air gap (1.6s to 2.8s)
        TimedWord(word="which", start=2.8, end=3.1),
        TimedWord(word="earned", start=3.15, end=3.4),
        TimedWord(word="10000", start=3.45, end=3.9),
        TimedWord(word="dollars", start=3.95, end=4.4),
        # 1.6s dead air gap (4.4s to 6.0s)
        TimedWord(word="in", start=6.0, end=6.2),
        TimedWord(word="under", start=6.25, end=6.5),
        TimedWord(word="twenty", start=6.55, end=6.8),
        TimedWord(word="four", start=6.85, end=7.1),
        TimedWord(word="hours!", start=7.15, end=7.7),
        # 1.3s dead air gap (7.7s to 9.0s)
        TimedWord(word="Do", start=9.0, end=9.2),
        TimedWord(word="not", start=9.25, end=9.4),
        TimedWord(word="make", start=9.45, end=9.7),
        TimedWord(word="this", start=9.75, end=10.0),
        TimedWord(word="MISTAKE", start=10.05, end=10.6),
        # Trailing silence 10.6s to 15.0s (4.4s)
    ]

    initial_edl = EditDecisionList(hook_in_sec=0.0, hook_out_sec=15.0)

    # Pre-audit
    before_report = analyze_edit_rhythm(words, initial_edl, clip_duration=15.0)
    assert len(initial_edl.jump_cuts) == 0
    assert len(initial_edl.zoom_punch_ins) == 0
    assert before_report.retention_pacing_score < 40.0

    # Optimize
    optimized_edl, after_report = optimize_edl_for_retention(
        words=words,
        edl=initial_edl,
        clip_duration=15.0,
        min_silence_gap=0.45,
        target_zooms_per_15s=2,
    )

    # 1. Verify dead-air jump cuts were created
    assert len(optimized_edl.jump_cuts) >= 2
    for cut in optimized_edl.jump_cuts:
        assert cut.cut_end > cut.cut_start
        assert cut.cut_start >= 0.0
        assert cut.cut_end <= 15.0

    # 2. Verify zoom punches were placed on emphasis words
    assert len(optimized_edl.zoom_punch_ins) >= 2
    punch_words = [p.emphasis_word for p in optimized_edl.zoom_punch_ins]
    assert any("SECRET" in pw or "10000" in pw or "MISTAKE" in pw for pw in punch_words)

    # 3. Verify retention pacing score improved significantly
    assert after_report.retention_pacing_score > before_report.retention_pacing_score
    assert after_report.silence_ratio < before_report.silence_ratio
    assert after_report.visual_events_per_10s > before_report.visual_events_per_10s
    assert after_report.pacing_grade in ("A+", "A", "B")


def test_optimize_edl_preserves_existing_valid_cuts_and_punches():
    """Verify optimize_edl_for_retention retains valid cuts/punches and doesn't duplicate them."""
    existing_cut = JumpCut(cut_start=2.0, cut_end=3.0, reason="manual_cut")
    existing_punch = ZoomPunchIn(timestamp=5.0, scale=1.18, duration=0.8, emphasis_word="MANUAL")
    edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=15.0,
        jump_cuts=[existing_cut],
        zoom_punch_ins=[existing_punch],
    )

    words = [
        TimedWord(word="Hello", start=0.5, end=1.0),
        TimedWord(word="MANUAL", start=5.0, end=5.5),
        TimedWord(word="World", start=5.6, end=6.0),
        # Gap: 6.0 to 8.0 (2.0s)
        TimedWord(word="FINAL", start=8.0, end=8.5),
    ]

    opt_edl, _report = optimize_edl_for_retention(words, edl, clip_duration=15.0)

    # Existing cut must be preserved
    assert any(c.cut_start == 2.0 and c.cut_end == 3.0 for c in opt_edl.jump_cuts)
    # Existing punch must be preserved
    assert any(p.timestamp == 5.0 for p in opt_edl.zoom_punch_ins)
    # New cut added for 6.0 to 8.0 gap
    assert any(c.cut_start >= 5.9 and c.cut_end <= 8.1 for c in opt_edl.jump_cuts)


def test_optimize_edl_no_static_over_8_seconds():
    """Verify optimize_edl_for_retention guarantees no static segment exceeds 8 seconds."""
    words: list[TimedWord] = []
    t = 0.5
    for i in range(30):
        words.append(TimedWord(word=f"item{i}", start=round(t, 2), end=round(t + 0.5, 2)))
        t += 0.8  # 0.3s gap (below min_silence_gap=0.45)

    edl = EditDecisionList(hook_in_sec=0.0, hook_out_sec=25.0)
    opt_edl, _report = optimize_edl_for_retention(words, edl, clip_duration=25.0)

    assert len(opt_edl.zoom_punch_ins) >= 3

    timestamps = [0.0] + [p.timestamp for p in opt_edl.zoom_punch_ins] + [25.0]
    timestamps.sort()

    for idx in range(len(timestamps) - 1):
        gap = timestamps[idx + 1] - timestamps[idx]
        assert gap <= 8.5, f"Static gap {gap:.2f}s exceeded 8.5s maximum at idx {idx}"


def test_detect_rapid_speech_bursts_and_bgm_duck():
    """Verify rapid speech bursts are detected and produce BgmDuck segments."""
    burst_words = [
        TimedWord(word="Listen", start=1.0, end=1.2),
        TimedWord(word="very", start=1.22, end=1.4),
        TimedWord(word="carefully", start=1.42, end=1.65),
        TimedWord(word="right", start=1.67, end=1.85),
        TimedWord(word="now", start=1.87, end=2.05),
        TimedWord(word="friends", start=2.07, end=2.2),
    ]

    ducks = detect_rapid_speech_bursts(burst_words, clip_duration=10.0, min_wpm=175.0)
    assert len(ducks) >= 1
    d = ducks[0]
    assert d.start_sec <= 1.0
    assert d.end_sec >= 2.2
    assert d.duck_volume_db == -12.0
    assert d.reason == "rapid_speech_burst"
    assert d.start == d.start_sec
    assert d.end == d.end_sec


def test_workflow_snapshot_roundtrip(tmp_path: Path):
    """Verify save_workflow_snapshot, list_workflow_snapshots, and restore_workflow_snapshot."""
    edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=40.0,
        jump_cuts=[JumpCut(cut_start=10.0, cut_end=12.0, reason="pause")],
        zoom_punch_ins=[ZoomPunchIn(timestamp=5.0, scale=1.18, duration=0.8, emphasis_word="BOOM")],
    )
    edl.bgm_ducks = [BgmDuck(start_sec=4.0, end_sec=6.0, duck_volume_db=-12.0)]
    words = [
        TimedWord(word="Hello", start=1.0, end=1.4),
        TimedWord(word="BOOM", start=5.0, end=5.4),
    ]

    # 1. Save first snapshot
    snap1 = save_workflow_snapshot(
        work_dir=tmp_path,
        job_id="job_test_1",
        edl=edl,
        words=words,
        label="initial",
    )
    assert "snapshot_id" in snap1
    assert snap1["label"] == "initial"
    snap1_id = snap1["snapshot_id"]

    # 2. Save second snapshot with a modification
    edl_modified = edl.model_copy(
        update={
            "jump_cuts": [
                JumpCut(cut_start=10.0, cut_end=12.0, reason="pause"),
                JumpCut(cut_start=20.0, cut_end=22.0, reason="second_pause"),
            ]
        }
    )
    snap2 = save_workflow_snapshot(
        work_dir=tmp_path,
        job_id="job_test_1",
        edl=edl_modified,
        words=words,
        label="after_second_cut",
    )
    snap2_id = snap2["snapshot_id"]

    # 3. List snapshots
    snapshots = list_workflow_snapshots(tmp_path)
    assert len(snapshots) == 2
    ids = [s["snapshot_id"] for s in snapshots]
    assert snap2_id in ids
    assert snap1_id in ids
    assert ids[0] == snap2_id

    # 4. Restore first snapshot (1-click rollback)
    restored1 = restore_workflow_snapshot(tmp_path, snap1_id)
    assert restored1["snapshot_id"] == snap1_id
    assert restored1["label"] == "initial"
    restored_edl1 = restored1["edl"]
    assert isinstance(restored_edl1, EditDecisionList)
    assert len(restored_edl1.jump_cuts) == 1
    assert restored_edl1.jump_cuts[0].cut_start == 10.0
    assert len(restored_edl1.zoom_punch_ins) == 1
    assert restored_edl1.zoom_punch_ins[0].emphasis_word == "BOOM"
    assert len(restored1["words"]) == 2
    assert isinstance(restored1["words"][0], TimedWord)
    assert restored1["words"][0].word == "Hello"
    assert len(restored1["bgm_ducks"]) == 1
    assert restored1["bgm_ducks"][0].start_sec == 4.0

    # 5. Restore second snapshot
    restored2 = restore_workflow_snapshot(tmp_path, snap2_id)
    assert len(restored2["edl"].jump_cuts) == 2

    # 6. Non-existent snapshot ID raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        restore_workflow_snapshot(tmp_path, "snap_non_existent_999999")


def test_list_workflow_snapshots_non_existent_dir(tmp_path: Path):
    """Verify list_workflow_snapshots returns empty list if snapshots directory does not exist."""
    non_existent = tmp_path / "does_not_exist"
    assert list_workflow_snapshots(non_existent) == []
