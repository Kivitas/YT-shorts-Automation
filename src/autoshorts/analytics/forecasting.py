"""Time-Series Forecasting Engine for YouTube Shorts Telemetry & Retention.

Provides:
- 3-tier fallback architecture:
    Tier 1: TimesFM foundation model (if timesfm is installed).
    Tier 2: NumPy/SciPy parametric curve fitting (if installed).
    Tier 3: Pure Python 3.11+ standard library (math, statistics) with ZERO heavy packages.
- 5-point smoothed retention cliff detector with >= 4.5s cooldown and <= 4 interrupts/Short.
- Early view velocity 48h trajectory forecasting and viral breakout prediction.
"""

from __future__ import annotations

import bisect
import importlib.util
import logging
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Check tier availability safely without hard crashes
_HAS_NUMPY_SCIPY = (
    importlib.util.find_spec("numpy") is not None and importlib.util.find_spec("scipy") is not None
)

# TimesFM 2.5 (200M params) settings. The package is optional (extra: autoshorts[forecast]);
# it is imported lazily and never at module import time.
_TIMESFM_MODEL_ID = "google/timesfm-2.5-200m-pytorch"
_TIMESFM_MIN_POINTS = 8  # shorter histories stay on the pure-Python tier
_TIMESFM_HORIZON = 48  # the 48-hour view horizon
_TIMESFM_MAX_CONTEXT = 1024
_timesfm_model_cache: Any | None = None
_timesfm_load_error: str | None = None  # cached so a failed load is not retried per call
_timesfm_warned = False  # the fallback warning is logged once per process


def _load_timesfm_model() -> Any:
    """Return the cached TimesFM 2.5 model, loading it on first use.

    Raises RuntimeError with the reason when timesfm is absent or the model cannot load.
    """
    global _timesfm_model_cache, _timesfm_load_error
    if _timesfm_model_cache is not None:
        return _timesfm_model_cache
    if _timesfm_load_error is not None:
        raise RuntimeError(_timesfm_load_error)
    try:
        import timesfm
    except ImportError as e:
        _timesfm_load_error = f"timesfm package not installed ({e})"
        raise RuntimeError(_timesfm_load_error) from e
    try:
        model_cls = getattr(timesfm, "TimesFM_2p5_200M_torch", None)
        config_cls = getattr(timesfm, "ForecastConfig", None)
        if model_cls is None or config_cls is None:
            raise AttributeError("installed timesfm lacks TimesFM_2p5_200M_torch/ForecastConfig")
        model = model_cls.from_pretrained(_TIMESFM_MODEL_ID)
        model.compile(
            config_cls(
                max_context=_TIMESFM_MAX_CONTEXT,
                max_horizon=_TIMESFM_HORIZON,
                normalize_inputs=True,
                use_continuous_quantile_head=True,
            )
        )
    except Exception as e:
        _timesfm_load_error = f"TimesFM model load failed ({type(e).__name__}: {e})"
        raise RuntimeError(_timesfm_load_error) from e
    _timesfm_model_cache = model
    return model


def _warn_timesfm_fallback(reason: str) -> None:
    """Log one warning naming the reason TimesFM was skipped; later calls log at debug."""
    global _timesfm_warned
    if _timesfm_warned:
        logger.debug("TimesFM unavailable again (%s); using fallback tier.", reason)
        return
    _timesfm_warned = True
    logger.warning(
        "TimesFM tier unavailable (%s); falling back to NumPy/SciPy or pure Python.", reason
    )


def _interpolate_cumulative(ts: Sequence[float], ys: Sequence[float], x: float) -> float:
    """Linear interpolation over sorted timestamps, clamped at both ends."""
    if x <= ts[0]:
        return ys[0]
    if x >= ts[-1]:
        return ys[-1]
    i = bisect.bisect_right(ts, x)
    t_lo, t_hi = ts[i - 1], ts[i]
    if t_hi == t_lo:
        return ys[i]
    w = (x - t_lo) / (t_hi - t_lo)
    return ys[i - 1] + w * (ys[i] - ys[i - 1])


def _hourly_series(points: Sequence[tuple[float, float]]) -> list[float]:
    """Resample (hour, cumulative_views) points onto whole-hour grid points."""
    pts = sorted(points, key=lambda p: p[0])
    ts = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    first, last = math.ceil(ts[0]), math.floor(ts[-1])
    return [_interpolate_cumulative(ts, ys, float(h)) for h in range(first, last + 1)]


@dataclass
class RetentionCliff:
    """Detected audience drop cliff triggering a pattern interrupt."""

    timestamp: float
    slope: float
    smoothed_retention: float
    action: str = "pattern_interrupt"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ForecastResult:
    """48-hour trajectory forecast and viral breakout telemetry."""

    predicted_views_48h: float
    is_viral_breakout: bool
    hourly_forecast: list[float]  # 48 hourly projection values
    confidence_score: float
    tier_used: Literal["timesfm", "numpy_scipy", "pure_python"]
    metrics: dict[str, Any] = field(default_factory=dict)


class TimeSeriesForecastingEngine:
    """Unified YouTube Shorts time-series forecasting and retention intelligence engine."""

    def __init__(
        self,
        cliff_threshold: float = -0.035,  # -3.5% retention drop per second
        cliff_cooldown_sec: float = 4.5,  # >= 4.5s between interrupts
        max_interrupts_per_short: int = 4,  # <= 4 pattern interrupts per 60s Short
        force_tier: Literal["timesfm", "numpy_scipy", "pure_python"] | None = None,
        *,
        timesfm_model: Any | None = None,
    ) -> None:
        self.cliff_threshold = float(cliff_threshold)
        self.cliff_cooldown_sec = float(cliff_cooldown_sec)
        self.max_interrupts = int(max_interrupts_per_short)
        self.force_tier = force_tier
        # Optional injected model (anything with forecast(horizon=, inputs=)); bypasses lazy loading.
        self._timesfm_model = timesfm_model

    @property
    def active_tier(self) -> Literal["timesfm", "numpy_scipy", "pure_python"]:
        """Top forecasting tier to attempt.

        TimesFM is always attempted first unless force_tier overrides it. If it is unavailable,
        forecast_trajectory logs one warning and falls back to NumPy/SciPy, then pure Python.
        The tier actually used is reported in ForecastResult.tier_used.
        """
        if self.force_tier:
            return self.force_tier
        return "timesfm"

    def get_active_tier(self) -> Literal["timesfm", "numpy_scipy", "pure_python"]:
        """Getter for active forecasting tier."""
        return self.active_tier

    # -------------------------------------------------------------------------
    # Retention Cliff Detection
    # -------------------------------------------------------------------------

    def detect_retention_cliffs(
        self,
        retention_data: Sequence[float | tuple[float, float] | dict[str, float]],
        timestamps: Sequence[float] | None = None,
        duration: float = 60.0,
    ) -> list[RetentionCliff]:
        """Detect steep audience drop cliffs using 5-point moving average smoothing.

        Invariants:
        1. Applies 5-point moving average smoothing on retention curve R(t).
        2. Flags a cliff only if slope \nabla R(t) <= -0.035 / sec.
        3. Enforces >= 4.5s refractory cooldown between consecutive pattern interrupts.
        4. Enforces hard ceiling of <= 4 pattern interrupts per 60s Short.
        """
        # Parse inputs into aligned (t, r) pairs
        parsed_t: list[float] = []
        parsed_r: list[float] = []

        if retention_data and isinstance(retention_data[0], tuple):
            for t_val, r_val in retention_data:  # type: ignore[misc]
                parsed_t.append(float(t_val))
                parsed_r.append(float(r_val))
        elif retention_data and isinstance(retention_data[0], dict):
            for item in retention_data:  # type: ignore[misc]
                t_val = item.get("timestamp", item.get("t", item.get("time", 0.0)))
                r_val = item.get("retention", item.get("r", item.get("value", 0.0)))
                parsed_t.append(float(t_val))
                parsed_r.append(float(r_val))
        else:
            raw_r = [float(v) for v in retention_data]  # type: ignore[arg-type]
            if timestamps is not None and len(timestamps) == len(raw_r):
                parsed_t = [float(t) for t in timestamps]
            else:
                n = len(raw_r)
                dt = duration / max(1, n - 1) if n > 1 else 1.0
                parsed_t = [i * dt for i in range(n)]
            parsed_r = raw_r

        n = len(parsed_r)
        if n < 5:
            # Need at least 5 points for 5-point smoothing
            return []

        # Normalize retention if represented on 0..100 scale instead of 0..1
        max_r = max(parsed_r)
        scale = 0.01 if max_r > 1.5 else 1.0
        normalized_r = [val * scale for val in parsed_r]

        # 1. 5-point moving average smoothing
        smoothed_r: list[float] = []
        for i in range(n):
            window = normalized_r[max(0, i - 2) : min(n, i + 3)]
            smoothed_r.append(sum(window) / len(window))

        # 2. Slope calculation and cooldown-enforced cliff detection
        cliffs: list[RetentionCliff] = []
        last_cliff_t = -999.0

        for i in range(1, n):
            dt = parsed_t[i] - parsed_t[i - 1]
            if dt <= 0.0:
                continue

            slope = (smoothed_r[i] - smoothed_r[i - 1]) / dt

            if slope <= self.cliff_threshold:
                curr_t = parsed_t[i]
                if (curr_t - last_cliff_t) >= self.cliff_cooldown_sec:
                    if len(cliffs) < self.max_interrupts:
                        cliffs.append(
                            RetentionCliff(
                                timestamp=round(curr_t, 2),
                                slope=round(slope, 4),
                                smoothed_retention=round(smoothed_r[i], 4),
                                action="pattern_interrupt",
                                details={
                                    "dt_sec": round(dt, 3),
                                    "raw_retention": round(normalized_r[i], 4),
                                },
                            )
                        )
                        last_cliff_t = curr_t

        return cliffs

    # -------------------------------------------------------------------------
    # 48-Hour Early View Velocity Trajectory Forecasting
    # -------------------------------------------------------------------------

    def forecast_trajectory(
        self,
        telemetry: Any,
        elapsed_hours: float | None = None,
    ) -> ForecastResult:
        """Forecast 48-hour view trajectory using the 3-tier fallback architecture."""
        tier = self.active_tier

        # Extract normalized history: list of (hour, cumulative_views)
        points = self._normalize_telemetry(telemetry, elapsed_hours=elapsed_hours)

        if tier == "timesfm":
            try:
                return self._forecast_timesfm(points)
            except Exception as e:
                _warn_timesfm_fallback(str(e))

        if (tier in ("timesfm", "numpy_scipy")) and _HAS_NUMPY_SCIPY:
            try:
                return self._forecast_numpy_scipy(points)
            except Exception as e:
                logger.warning("NumPy/SciPy tier failed (%s); falling back to Tier 3.", e)

        return self._forecast_pure_python(points)

    def _normalize_telemetry(
        self,
        telemetry: Any,
        elapsed_hours: float | None = None,
    ) -> list[tuple[float, float]]:
        """Normalize varied telemetry structures into sorted (hours, views) pairs."""
        points: list[tuple[float, float]] = []

        if telemetry is None:
            return []

        # Handle VideoAnalyticsRecord
        if hasattr(telemetry, "history") and hasattr(telemetry, "views"):
            history = getattr(telemetry, "history", [])
            if history and len(history) >= 2:
                try:
                    t0 = datetime.fromisoformat(history[0].timestamp)
                    for snap in history:
                        t = datetime.fromisoformat(snap.timestamp)
                        h = max(0.01, (t - t0).total_seconds() / 3600.0)
                        points.append((h, float(snap.views)))
                except Exception:
                    pass
            if not points:
                # Use current views and velocity
                v = float(getattr(telemetry, "views", 0.0))
                vel = float(getattr(telemetry, "velocity_views_per_hour", 0.0))
                h = elapsed_hours or 2.0
                if vel > 0:
                    points = [(max(0.1, h - 1.0), max(0.0, v - vel)), (h, v)]
                else:
                    points = [(h, v)]
            return sorted(points, key=lambda x: x[0])

        # Handle list of (hour, view) tuples
        if isinstance(telemetry, Sequence) and telemetry and isinstance(telemetry[0], tuple):
            for item in telemetry:
                points.append((float(item[0]), float(item[1])))
            return sorted(points, key=lambda x: x[0])

        # Handle list of numeric views (assumed 1h, 2h, 3h...)
        if isinstance(telemetry, Sequence) and telemetry and isinstance(telemetry[0], (int, float)):
            for idx, val in enumerate(telemetry, start=1):
                points.append((float(idx), float(val)))
            return sorted(points, key=lambda x: x[0])

        # Single numeric view
        if isinstance(telemetry, (int, float)):
            h = elapsed_hours or 1.0
            return [(float(h), float(telemetry))]

        return []

    def _forecast_pure_python(
        self,
        points: list[tuple[float, float]],
    ) -> ForecastResult:
        """Pure Python standard library (Tier 3) analytical growth regression."""
        if not points:
            return ForecastResult(
                predicted_views_48h=0.0,
                is_viral_breakout=False,
                hourly_forecast=[0.0] * 48,
                confidence_score=0.0,
                tier_used="pure_python",
                metrics={"reason": "empty_telemetry"},
            )

        t_vals = [p[0] for p in points]
        y_vals = [p[1] for p in points]
        last_views = y_vals[-1]
        last_t = t_vals[-1]

        if all(y <= 0.0 for y in y_vals):
            return ForecastResult(
                predicted_views_48h=0.0,
                is_viral_breakout=False,
                hourly_forecast=[0.0] * 48,
                confidence_score=1.0,
                tier_used="pure_python",
                metrics={"reason": "all_zero_views"},
            )

        # Multi-point log-linear regression: ln(y) = ln(a) + b * ln(t)
        if len(points) >= 2 and all(t > 0.0 for t in t_vals) and all(y > 0.0 for y in y_vals):
            try:
                ln_t = [math.log(t) for t in t_vals]
                ln_y = [math.log(y) for y in y_vals]
                b, ln_a = statistics.linear_regression(ln_t, ln_y)
                a = math.exp(ln_a)

                # Stabilize exponent b against runaway growth
                b_clamped = max(0.2, min(b, 1.85))

                hourly: list[float] = []
                for h in range(1, 49):
                    proj = a * (h**b_clamped)
                    # Cumulative views cannot decrease below observed
                    val = max(proj, last_views if h >= last_t else 0.0)
                    hourly.append(round(val, 1))

                pred_48 = hourly[47]
                is_breakout = (pred_48 >= 10000.0) or (b >= 1.25 and pred_48 >= 3000.0)

                return ForecastResult(
                    predicted_views_48h=round(pred_48, 1),
                    is_viral_breakout=is_breakout,
                    hourly_forecast=hourly,
                    confidence_score=0.85,
                    tier_used="pure_python",
                    metrics={
                        "growth_exponent_b": round(b, 3),
                        "initial_scale_a": round(a, 2),
                        "observed_points": len(points),
                    },
                )
            except Exception as e:
                logger.debug("Pure Python log regression fallback: %s", e)

        # Single-point analytical expansion with sublinear decay (gamma = 0.82)
        gamma = 0.82
        hourly = []
        for h in range(1, 49):
            scale = (h / max(0.1, last_t)) ** gamma
            val = last_views * scale
            hourly.append(round(max(val, last_views if h >= last_t else 0.0), 1))

        pred_48 = hourly[47]
        is_breakout = pred_48 >= 10000.0

        return ForecastResult(
            predicted_views_48h=round(pred_48, 1),
            is_viral_breakout=is_breakout,
            hourly_forecast=hourly,
            confidence_score=0.70,
            tier_used="pure_python",
            metrics={
                "model": "single_point_sublinear_expansion",
                "decay_gamma": gamma,
                "last_views": last_views,
            },
        )

    def _forecast_numpy_scipy(
        self,
        points: list[tuple[float, float]],
    ) -> ForecastResult:
        """NumPy/SciPy parametric curve fitting (Tier 2)."""
        import numpy as np
        from scipy.optimize import curve_fit

        if not points:
            return self._forecast_pure_python(points)

        t_arr = np.array([p[0] for p in points], dtype=float)
        y_arr = np.array([p[1] for p in points], dtype=float)

        if len(points) < 3 or np.all(y_arr <= 0):
            return self._forecast_pure_python(points)

        # Logistic or power law fitting
        def power_law(t: np.ndarray, a: float, b: float) -> np.ndarray:
            return a * (t**b)

        popt, _ = curve_fit(power_law, t_arr, y_arr, p0=[y_arr[0], 0.8], maxfev=1000)
        a, b = popt
        b_safe = max(0.2, min(b, 1.85))

        h_arr = np.arange(1, 49, dtype=float)
        proj = a * (h_arr**b_safe)
        proj = np.maximum(proj, y_arr[-1])
        hourly = [round(float(v), 1) for v in proj]
        pred_48 = hourly[47]
        is_breakout = (pred_48 >= 10000.0) or (b >= 1.25 and pred_48 >= 3000.0)

        return ForecastResult(
            predicted_views_48h=round(pred_48, 1),
            is_viral_breakout=is_breakout,
            hourly_forecast=hourly,
            confidence_score=0.90,
            tier_used="numpy_scipy",
            metrics={"fit_a": round(float(a), 2), "fit_b": round(float(b), 3)},
        )

    def _forecast_timesfm(
        self,
        points: list[tuple[float, float]],
    ) -> ForecastResult:
        """TimesFM 2.5 zero-shot time-series forecaster (Tier 1).

        Raises when the model is unavailable or the call fails; forecast_trajectory catches that
        and falls back to Tier 2/3.
        """
        # Short-context path: fewer than 8 points stays on the pure-Python tier and never
        # loads or calls the model.
        if len(points) < _TIMESFM_MIN_POINTS:
            return self._forecast_pure_python(points)

        series = _hourly_series(points)
        if len(series) < _TIMESFM_MIN_POINTS:
            # Points span too little whole-hour coverage to form a usable context.
            return self._forecast_pure_python(points)
        series = series[-_TIMESFM_MAX_CONTEXT:]

        model = self._timesfm_model if self._timesfm_model is not None else _load_timesfm_model()
        point_fc, quant_fc = model.forecast(horizon=_TIMESFM_HORIZON, inputs=[series])

        point = [float(v) for v in point_fc[0][:_TIMESFM_HORIZON]]
        if len(point) < _TIMESFM_HORIZON:
            raise ValueError(f"TimesFM returned {len(point)} points, need {_TIMESFM_HORIZON}")
        hourly = [round(v, 1) for v in point]
        pred_48 = hourly[-1]

        # Confidence from the 10th-90th percentile spread. Quantile channel 0 is the mean,
        # channels 1..9 are the 0.1..0.9 quantiles. Keep 0.9 if the quantiles are unusable.
        confidence = 0.9
        if quant_fc is not None:
            try:
                spreads: list[float] = []
                for h in range(_TIMESFM_HORIZON):
                    row = quant_fc[0][h]
                    if len(row) < 10:
                        raise ValueError("quantile head has fewer than 10 channels")
                    lo, hi = float(row[1]), float(row[9])
                    spreads.append(max(0.0, hi - lo) / max(abs(point[h]), 1.0))
                confidence = 1.0 / (1.0 + sum(spreads) / len(spreads))
            except (IndexError, TypeError, ValueError) as e:
                logger.debug("TimesFM quantile spread unusable (%s); keeping confidence 0.9.", e)
        confidence = max(0.0, min(1.0, confidence))

        return ForecastResult(
            predicted_views_48h=round(pred_48, 1),
            is_viral_breakout=pred_48 >= 10000.0,
            hourly_forecast=hourly,
            confidence_score=round(confidence, 4),
            tier_used="timesfm",
            metrics={
                "model": _TIMESFM_MODEL_ID,
                "context_hours": len(series),
                "horizon_hours": _TIMESFM_HORIZON,
                "injected_model": self._timesfm_model is not None,
            },
        )
