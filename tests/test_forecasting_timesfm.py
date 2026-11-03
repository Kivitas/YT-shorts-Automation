"""Tests for the TimesFM tier of TimeSeriesForecastingEngine (fake model injected, no network)."""

from __future__ import annotations

import logging
import sys
from typing import Any

import pytest

from autoshorts.analytics import forecasting
from autoshorts.analytics.forecasting import TimeSeriesForecastingEngine

HORIZON = 48


class FakeTimesFM:
    """Stand-in for a TimesFM 2.5 model with known outputs; records every call."""

    def __init__(self, point: list[float], quantiles: list[list[list[float]]] | None) -> None:
        self._point = point
        self._quantiles = quantiles
        self.calls: list[dict[str, Any]] = []

    def forecast(self, horizon: int, inputs: list[list[float]]) -> tuple[Any, Any]:
        self.calls.append({"horizon": horizon, "inputs": inputs})
        return [self._point], self._quantiles


@pytest.fixture(autouse=True)
def _reset_timesfm_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate module-level TimesFM cache and warn-once state per test."""
    monkeypatch.setattr(forecasting, "_timesfm_model_cache", None)
    monkeypatch.setattr(forecasting, "_timesfm_load_error", None)
    monkeypatch.setattr(forecasting, "_timesfm_warned", False)


def _points(n: int = 10) -> list[tuple[float, float]]:
    """Cumulative views at hours 1..n."""
    return [(float(h), 100.0 * h) for h in range(1, n + 1)]


def _known_outputs() -> tuple[list[float], list[list[list[float]]]]:
    point = [1000.0 + 25.0 * i for i in range(HORIZON)]
    # Quantile channels: 0 = mean, 1..9 = 0.1..0.9 quantiles. Spread is 10% of the point value.
    quant = [
        [[p, p * 0.95, 0, 0, 0, 0, 0, 0, 0, p * 1.05] for p in point],
    ]
    return point, quant


def test_injected_model_drives_timesfm_tier() -> None:
    point, quant = _known_outputs()
    fake = FakeTimesFM(point, quant)
    engine = TimeSeriesForecastingEngine(timesfm_model=fake)

    result = engine.forecast_trajectory(_points(10))

    assert result.tier_used == "timesfm"
    assert len(result.hourly_forecast) == HORIZON
    assert result.hourly_forecast == pytest.approx(point, abs=0.05)
    assert result.predicted_views_48h == pytest.approx(point[-1], abs=0.05)
    assert len(fake.calls) == 1
    assert fake.calls[0]["horizon"] == HORIZON
    assert len(fake.calls[0]["inputs"][0]) == 10  # hourly context from 10 points

    # Relative 10-90 spread is 0.10 * p / p = 0.10 at every step (after the 1.0 floor).
    expected_conf = 1.0 / (1.0 + 0.10)
    assert result.confidence_score == pytest.approx(expected_conf, abs=1e-3)


def test_confidence_keeps_default_without_quantiles() -> None:
    point, _ = _known_outputs()
    engine = TimeSeriesForecastingEngine(timesfm_model=FakeTimesFM(point, None))

    result = engine.forecast_trajectory(_points(10))

    assert result.tier_used == "timesfm"
    assert result.confidence_score == pytest.approx(0.9)


def test_fallback_logs_single_warning_when_timesfm_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A None entry in sys.modules makes `import timesfm` raise ImportError.
    monkeypatch.setitem(sys.modules, "timesfm", None)
    engine = TimeSeriesForecastingEngine()

    with caplog.at_level(logging.WARNING):
        first = engine.forecast_trajectory(_points(10))
        second = engine.forecast_trajectory(_points(10))

    assert first.tier_used != "timesfm"
    assert second.tier_used != "timesfm"
    assert len(first.hourly_forecast) == HORIZON
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "TimesFM" in warnings[0].getMessage()
    assert "not installed" in warnings[0].getMessage()


def test_fallback_when_injected_model_raises(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenModel:
        def forecast(self, horizon: int, inputs: list[list[float]]) -> Any:
            raise RuntimeError("device out of memory")

    engine = TimeSeriesForecastingEngine(timesfm_model=BrokenModel())

    with caplog.at_level(logging.WARNING):
        result = engine.forecast_trajectory(_points(10))

    assert result.tier_used != "timesfm"
    assert len(result.hourly_forecast) == HORIZON
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "device out of memory" in warnings[0].getMessage()


def test_short_context_never_calls_model() -> None:
    point, quant = _known_outputs()
    fake = FakeTimesFM(point, quant)
    engine = TimeSeriesForecastingEngine(timesfm_model=fake)

    result = engine.forecast_trajectory(_points(7))

    assert result.tier_used == "pure_python"
    assert len(result.hourly_forecast) == HORIZON
    assert fake.calls == []
