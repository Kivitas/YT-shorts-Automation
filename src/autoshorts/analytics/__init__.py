"""AutoShorts YouTube Shorts In-App Analytics & Closed-Loop Attribution Hub.

Provides multi-source telemetry synchronization (YouTube Data API v3, Analytics API v2,
yt-dlp zero-quota fallback, and Playwright Edge Studio DOM scraper) and closed-loop
attribution modeling to determine Winning Style Recipes (layout, captions, backgrounds).
"""

from __future__ import annotations

from autoshorts.analytics.attribution import PerformanceAttributionEngine
from autoshorts.analytics.engine import AnalyticsEngine, parse_iso8601_duration
from autoshorts.analytics.models import (
    AnalyticsOverview,
    AnalyticsSnapshot,
    StyleBreakdownItem,
    VideoAnalyticsRecord,
    WinningRecipe,
)

__all__ = [
    "AnalyticsEngine",
    "AnalyticsOverview",
    "AnalyticsSnapshot",
    "PerformanceAttributionEngine",
    "StyleBreakdownItem",
    "VideoAnalyticsRecord",
    "WinningRecipe",
    "parse_iso8601_duration",
]
