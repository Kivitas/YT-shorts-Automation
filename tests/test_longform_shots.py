"""Tests for the longform visual shot planner."""

from __future__ import annotations

import pytest

from autoshorts.longform.shots import Shot, plan_shots, total_coverage


def _dense_words(count: int, step: float = 0.5) -> list[dict]:
    """Words every `step` seconds; every 5th word ends a sentence."""
    words = []
    for i in range(count):
        word = f"w{i}." if (i + 1) % 5 == 0 else f"w{i}"
        start = i * step
        words.append({"word": word, "start": start, "end": start + step * 0.9})
    return words


def test_coverage_equals_duration():
    duration = 37.3
    shots = plan_shots(duration, _dense_words(75))
    assert total_coverage(shots) == pytest.approx(duration)


def test_shots_are_contiguous_and_cover_range():
    duration = 37.3
    shots = plan_shots(duration, _dense_words(75))
    assert shots[0].start == 0.0
    assert shots[-1].end == pytest.approx(duration)
    for prev, nxt in zip(shots, shots[1:], strict=False):
        assert nxt.start == prev.end


def test_no_empty_shot():
    shots = plan_shots(37.3, _dense_words(75))
    assert all(shot.length > 0 for shot in shots)


def test_first_shot_is_wide():
    shots = plan_shots(20.0, _dense_words(40))
    assert shots[0].kind == "wide"


def test_every_third_shot_is_insert():
    shots = plan_shots(37.3, _dense_words(75))
    assert len(shots) >= 3
    assert shots[2].kind == "insert"


def test_empty_words_gives_single_wide_shot():
    shots = plan_shots(12.5, [])
    assert shots == [Shot(0.0, 12.5, "wide")]


@pytest.mark.parametrize("duration", [0.0, -1.0])
def test_non_positive_duration_raises(duration):
    with pytest.raises(ValueError):
        plan_shots(duration, _dense_words(10))


def test_shot_lengths_within_bounds_for_dense_transcript():
    shots = plan_shots(37.3, _dense_words(75), min_shot=2.0, max_shot=4.0)
    for shot in shots:
        assert 2.0 <= shot.length <= 4.0 + 1e-9


def test_short_shot_is_montage():
    # Sentence end at 2.2s splits 4.4s into 2.2s + 2.2s; the second is under 2.5s.
    words = [{"word": "A.", "start": 0.0, "end": 2.2}]
    shots = plan_shots(4.4, words, min_shot=2.0, max_shot=4.0)
    assert [s.kind for s in shots] == ["wide", "montage"]
    assert total_coverage(shots) == pytest.approx(4.4)
