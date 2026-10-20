"""Pydantic v2 models for YouTube Shorts Analytics and Closed-Loop Attribution.

Covers individual video telemetry, time-series snapshots, dimension performance
breakdowns (layout, caption style, background), and actionable Winning Recipes.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AnalyticsSnapshot(BaseModel):
    """Historical telemetry snapshot for time-series charts."""

    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    avg_view_duration_sec: float = 0.0
    avg_view_percentage: float = 0.0
    estimated_revenue_usd: float = 0.0


class VideoAnalyticsRecord(BaseModel):
    """Normalized analytics record for an uploaded YouTube Short."""

    video_id: str
    job_id: str = ""
    title: str = ""
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    avg_view_duration_sec: float = 0.0
    avg_view_percentage: float = 0.0
    viewed_vs_swiped_pct: float = 0.0
    shown_in_feed: int = 0
    subscribers_gained: int = 0
    estimated_revenue_usd: float = 0.0
    rpm_usd: float = 0.0
    source_tier: str = "ytdlp"  # 'data_api', 'analytics_api', 'ytdlp', 'studio'
    published_at: str | None = None
    fetched_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    history_json: str = "[]"
    history: list[AnalyticsSnapshot] = Field(default_factory=list)

    @property
    def engagement_rate(self) -> float:
        """Engagement rate percentage: (likes + comments + shares) / views * 100."""
        if self.views <= 0:
            return 0.0
        return round(((self.likes + self.comments + self.shares) / self.views) * 100.0, 2)

    @property
    def svs_score(self) -> float:
        """Shorts Viral Score (SVS, 0-100).

        Composite metric calibrating:
        - Audience Retention % (avg_view_percentage, ~35% weight)
        - Hook / Swipe Hold Rate (viewed_vs_swiped_pct, ~25% weight)
        - Engagement Rate (likes + comments + shares / views, ~20% weight)
        - Reach & Velocity (log-scaled views, ~20% weight)
        """
        # 1. Reach & Volume factor (0 - 20 pts)
        # 10k views -> ~16 pts, 100k views -> 20 pts
        vol_score = min(math.log10(max(1, self.views)) / 5.0, 1.0) * 20.0

        # 2. Engagement rate factor (0 - 20 pts)
        # 6% engagement is strong, 8%+ is top-tier
        eng_score = min(self.engagement_rate / 8.0, 1.0) * 20.0

        # 3. Retention factor (0 - 35 pts)
        retention = self.avg_view_percentage
        if retention > 0:
            ret_score = min(retention / 100.0, 1.25) * 28.0
        else:
            # Dynamic estimate when retention is not yet populated
            ret_score = min(eng_score * 1.4, 28.0)

        # 4. Viewed vs Swiped Away factor (0 - 25 pts)
        if self.viewed_vs_swiped_pct > 0:
            swipe_score = min(self.viewed_vs_swiped_pct / 80.0, 1.0) * 25.0
        else:
            swipe_score = (vol_score * 0.45) + (eng_score * 0.55)

        total = vol_score + eng_score + ret_score + swipe_score
        return round(max(0.0, min(100.0, total)), 1)


class StyleBreakdownItem(BaseModel):
    """Aggregated performance metrics for a specific style attribute."""

    key: str
    dimension: str
    sample_count: int = 0
    mean_views: float = 0.0
    mean_likes: float = 0.0
    mean_retention_pct: float = 0.0
    mean_viewed_pct: float = 0.0
    svs_score: float = 0.0


class WinningRecipe(BaseModel):
    """Actionable winning style recipe derived from closed-loop attribution."""

    layout_mode: str = "split_50_50"
    style_preset: str = "hormozi"
    background_category: str = "subway"
    color_grading: str = "none"
    confidence: str = "default"  # 'high', 'medium', 'preliminary', 'default'
    sample_size: int = 0
    avg_retention_pct: float = 0.0
    avg_viewed_pct: float = 0.0
    avg_views: float = 0.0
    uplift_summary: str = ""
    rationale: str = ""
    source_job_id: str | None = None


class AnalyticsOverview(BaseModel):
    """Channel-wide aggregate analytics overview for dashboard KPI cards."""

    total_videos: int = 0
    total_views: int = 0
    total_likes: int = 0
    total_comments: int = 0
    total_shares: int = 0
    avg_retention_pct: float = 0.0
    avg_viewed_vs_swiped_pct: float = 0.0
    total_estimated_revenue_usd: float = 0.0
    channel_rpm_usd: float = 0.18
    top_video: dict[str, Any] | None = None
    winning_recipe: WinningRecipe = Field(default_factory=WinningRecipe)
    last_synced_at: str | None = None


__all__ = [
    "AnalyticsOverview",
    "AnalyticsSnapshot",
    "StyleBreakdownItem",
    "VideoAnalyticsRecord",
    "WinningRecipe",
]
