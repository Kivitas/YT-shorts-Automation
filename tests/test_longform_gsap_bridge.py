"""Tests for the GSAP timeline bridge and the GSAP-driven camera push-in."""

from __future__ import annotations

import logging
import shutil

import pytest

import autoshorts.longform.drawer as drawer
import autoshorts.longform.gsap_bridge as gsap_bridge
from autoshorts.longform.drawer import PUSH_IN_END, _push_in_zoom

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(autouse=True)
def _clear_caches():
    gsap_bridge._sample_cached.cache_clear()
    drawer._push_in_table.cache_clear()
    drawer._gsap_fallback_warned = False
    yield
    gsap_bridge._sample_cached.cache_clear()
    drawer._push_in_table.cache_clear()
    drawer._gsap_fallback_warned = False


@needs_node
def test_sample_timeline_runs_from_start_value_to_end_value() -> None:
    n = 9
    spec = {
        "duration": 1.0,
        "frames": n,
        "state": {"x": 2.0},
        "tweens": [{"at": 0.0, "duration": 1.0, "vars": {"x": 6.0}, "ease": "none"}],
    }
    values = gsap_bridge.sample_timeline(spec)["x"]
    assert len(values) == n
    assert values[0] == pytest.approx(2.0)
    assert values[-1] == pytest.approx(6.0)
    assert values[n // 2] == pytest.approx(4.0)


@needs_node
def test_sample_timeline_honours_explicit_from_values() -> None:
    spec = {
        "duration": 1.0,
        "frames": 3,
        "state": {"x": 0.0},
        "tweens": [{"at": 0.0, "duration": 1.0, "from": {"x": 10.0}, "vars": {"x": 20.0}}],
    }
    values = gsap_bridge.sample_timeline(spec)["x"]
    assert values[0] == pytest.approx(10.0)
    assert values[-1] == pytest.approx(20.0)


def test_sample_value_clamps_to_list_bounds() -> None:
    values = [1.0, 2.0, 3.0]
    assert gsap_bridge.sample_value(values, 1) == 2.0
    assert gsap_bridge.sample_value(values, -5) == 1.0
    assert gsap_bridge.sample_value(values, 99) == 3.0


@needs_node
def test_push_in_zoom_from_gsap_hits_1_0_and_push_in_end() -> None:
    frame_count = 90
    assert _push_in_zoom(0, frame_count) == pytest.approx(1.0)
    assert _push_in_zoom(frame_count - 1, frame_count) == pytest.approx(PUSH_IN_END)
    assert 1.0 < _push_in_zoom(frame_count // 2, frame_count) < PUSH_IN_END


def test_push_in_zoom_fallback_when_node_missing_logs_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(gsap_bridge.shutil, "which", lambda _name: None)
    caplog.set_level(logging.WARNING, logger=drawer.logger.name)

    frame_counts = (37, 41, 53)
    for frame_count in frame_counts:
        assert _push_in_zoom(0, frame_count) == pytest.approx(1.0)
        assert _push_in_zoom(frame_count - 1, frame_count) == pytest.approx(PUSH_IN_END)
        mid = frame_count // 2
        expected_p = mid / (frame_count - 1)
        expected = 1.0 + (PUSH_IN_END - 1.0) * drawer.motion.ease_in_out_sine(expected_p)
        assert _push_in_zoom(mid, frame_count) == pytest.approx(expected)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "node executable not found" in warnings[0].getMessage()
