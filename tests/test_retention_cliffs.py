"""Tests for forecast retention-cliff pattern interrupts.

1. cliffs_to_interrupts: window filtering, refractory spacing, per-minute cap.
2. optimize_edl_for_retention: no curve keeps the pre-existing output; with a curve,
   forecast interrupts are merged, deduplicated against heuristic punches, and logged.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from autoshorts.analytics.forecasting import RetentionCliff
from autoshorts.brain.specialists.retention_editor import cliffs_to_interrupts
from autoshorts.core.models import EditDecisionList
from autoshorts.edit_planner.workflow_optimizer import optimize_edl_for_retention
from autoshorts.media.ass_styler import TimedWord


def _cliff(t: float) -> RetentionCliff:
    return RetentionCliff(timestamp=t, slope=-0.05, smoothed_retention=0.5)


def _words() -> list[TimedWord]:
    return [
        TimedWord(word="Listen", start=0.2, end=0.6),
        TimedWord(word="to", start=0.65, end=0.8),
        TimedWord(word="this", start=0.85, end=1.1),
        TimedWord(word="SECRET", start=1.15, end=1.6),
        TimedWord(word="which", start=2.8, end=3.1),
        TimedWord(word="earned", start=3.15, end=3.4),
        TimedWord(word="10000", start=3.45, end=3.9),
        TimedWord(word="dollars", start=3.95, end=4.4),
        TimedWord(word="in", start=6.0, end=6.2),
        TimedWord(word="under", start=6.25, end=6.5),
        TimedWord(word="twenty", start=6.55, end=6.8),
        TimedWord(word="four", start=6.85, end=7.1),
        TimedWord(word="hours!", start=7.15, end=7.7),
        TimedWord(word="Do", start=9.0, end=9.2),
        TimedWord(word="not", start=9.25, end=9.4),
        TimedWord(word="make", start=9.45, end=9.7),
        TimedWord(word="this", start=9.75, end=10.0),
        TimedWord(word="MISTAKE", start=10.05, end=10.6),
    ]


def test_cliffs_outside_window_are_dropped() -> None:
    cliffs = [_cliff(2.0), _cliff(12.0), _cliff(40.0), _cliff(5.0)]
    result = cliffs_to_interrupts(cliffs, clip_start=10.0, clip_end=20.0)
    assert result == [2.0]  # only t=12.0, returned relative to clip start


def test_cliffs_accept_plain_numbers() -> None:
    assert cliffs_to_interrupts([3.0, 50.0], clip_start=0.0, clip_end=10.0) == [3.0]


def test_refractory_is_respected() -> None:
    # 2.0, 3.5 fall inside the refractory of 1.0; 10.0 is only 4.4 s after 5.6 and is dropped.
    cliffs = [_cliff(1.0), _cliff(2.0), _cliff(3.5), _cliff(5.6), _cliff(10.0), _cliff(10.2)]
    result = cliffs_to_interrupts(cliffs, clip_start=0.0, clip_end=30.0, refractory=4.5)
    assert result == [1.0, 5.6, 10.2]
    for a, b in zip(result, result[1:], strict=False):
        assert b - a >= 4.5


def test_per_minute_cap_holds() -> None:
    # One cliff every 5 s for 5 minutes: spacing is fine, only the rate cap can bind.
    cliffs = [_cliff(float(i * 5)) for i in range(60)]
    result = cliffs_to_interrupts(cliffs, clip_start=0.0, clip_end=300.0, max_per_minute=4)
    assert len(result) > 0
    for t in result:
        in_window = [u for u in result if t - 60.0 < u <= t]
        assert len(in_window) <= 4


def test_per_minute_cap_limits_dense_cliffs() -> None:
    cliffs = [_cliff(t) for t in (0.0, 4.5, 9.0, 13.5, 18.0, 22.5)]
    result = cliffs_to_interrupts(cliffs, clip_start=0.0, clip_end=60.0, max_per_minute=4)
    assert result == [0.0, 4.5, 9.0, 13.5]


def test_empty_or_invalid_window_returns_empty() -> None:
    assert cliffs_to_interrupts([], clip_start=0.0, clip_end=10.0) == []
    assert cliffs_to_interrupts([_cliff(1.0)], clip_start=5.0, clip_end=5.0) == []
    assert cliffs_to_interrupts([_cliff(1.0)], 0.0, 10.0, max_per_minute=0) == []


def test_no_curve_keeps_old_output() -> None:
    """Without a retention curve the optimizer output matches the pre-change baseline."""
    edl = EditDecisionList(hook_in_sec=0.0, hook_out_sec=15.0)
    old_path, _ = optimize_edl_for_retention(_words(), edl, clip_duration=15.0)
    explicit_none, _ = optimize_edl_for_retention(
        _words(), edl, clip_duration=15.0, retention_curve=None, clip_start=0.0
    )

    assert [(c.cut_start, c.cut_end) for c in old_path.jump_cuts] == pytest.approx(
        [(1.66, 2.74), (4.46, 5.94), (7.76, 8.94), (10.66, 14.94)]
    )
    punches = [(p.timestamp, p.scale, p.duration, p.emphasis_word) for p in old_path.zoom_punch_ins]
    assert [p[3] for p in punches] == ["SECRET", "MISTAKE"]
    assert [p[0] for p in punches] == pytest.approx([1.15, 10.05])
    assert [p[1] for p in punches] == pytest.approx([1.18, 1.18])
    assert [p[2] for p in punches] == pytest.approx([0.9, 1.0])
    assert [(c.cut_start, c.cut_end) for c in explicit_none.jump_cuts] == [
        (c.cut_start, c.cut_end) for c in old_path.jump_cuts
    ]
    assert len(explicit_none.zoom_punch_ins) == len(old_path.zoom_punch_ins)


def test_no_curve_does_not_call_forecasting() -> None:
    edl = EditDecisionList(hook_in_sec=0.0, hook_out_sec=15.0)
    with patch(
        "autoshorts.edit_planner.workflow_optimizer.TimeSeriesForecastingEngine"
    ) as engine_cls:
        optimize_edl_for_retention(_words(), edl, clip_duration=15.0)
    engine_cls.assert_not_called()


def test_curve_merges_forecast_interrupts_and_logs_sources(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Flat retention, then a steep drop starting near t=20 s in source time.
    curve = [(float(t), 1.0 if t < 20 else max(0.2, 1.0 - 0.1 * (t - 20))) for t in range(0, 40)]
    edl = EditDecisionList(hook_in_sec=0.0, hook_out_sec=15.0)

    with caplog.at_level(logging.INFO, logger="autoshorts.edit_planner.workflow_optimizer"):
        out, _ = optimize_edl_for_retention(
            _words(), edl, clip_duration=15.0, retention_curve=curve, clip_start=20.0
        )

    timestamps = sorted(float(p.timestamp) for p in out.zoom_punch_ins)
    # Every pair of punches respects the 4.5 s refractory window.
    for a, b in zip(timestamps, timestamps[1:], strict=False):
        assert b - a >= 4.5 - 1e-9
    # Every punch is inside the clip.
    assert all(0.0 <= t <= 15.0 for t in timestamps)
    # Each interrupt is logged with its source.
    messages = [r.getMessage() for r in caplog.records]
    assert any("source=heuristic" in m for m in messages)
    assert len([m for m in messages if "source=" in m]) == len(out.zoom_punch_ins)
