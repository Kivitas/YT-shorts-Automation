"""Pydantic v2 data models for the Viral Research Agent & Autonomous Faceless AI Shorts Studio Engine.

Covers trending sources, viral scoring, niche payout intelligence, viral BGM catalogs,
AI strategy briefs, and faceless short generation requests/results.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ViralScoreBreakdown(BaseModel):
    """Component breakdown of the 0-100 Clip Viral Potential Score."""

    velocity_score: float = Field(
        default=0.0, ge=0.0, le=40.0, description="Velocity score based on views per hour (0-40)"
    )
    duration_score: float = Field(
        default=0.0, ge=0.0, le=25.0, description="Duration sweet spot score (0-25)"
    )
    engagement_score: float = Field(
        default=0.0, ge=0.0, le=20.0, description="Likes/views engagement ratio score (0-20)"
    )
    freshness_score: float = Field(
        default=0.0, ge=0.0, le=15.0, description="Upload recency score (0-15)"
    )
    total_score: float = Field(
        default=0.0, ge=0.0, le=100.0, description="Composite Clip Viral Potential Score (0-100)"
    )


class TrendingSourceVideo(BaseModel):
    """Discovered long-form video or podcast evaluated for viral Shorts clipping potential."""

    video_id: str = Field(..., description="YouTube 11-char video ID")
    url: str = Field(
        ..., description="Direct YouTube watch URL (https://www.youtube.com/watch?v=...)"
    )
    title: str = Field(..., description="Video title")
    channel: str = Field(..., description="Channel / creator name")
    channel_url: str = Field(default="https://youtube.com", description="Channel URL")
    duration_seconds: float = Field(default=0.0, ge=0.0, description="Duration in seconds")
    duration_formatted: str = Field(default="00:00", description="Formatted duration (e.g. 42:15)")
    view_count: int = Field(default=0, ge=0, description="Total views")
    like_count: int = Field(default=0, ge=0, description="Total likes")
    hours_since_upload: float = Field(
        default=0.0, ge=0.0, description="Hours elapsed since publish"
    )
    views_per_hour: float = Field(
        default=0.0, ge=0.0, description="Calculated velocity (views/hour)"
    )
    thumbnail_url: str = Field(default="", description="Video thumbnail URL")
    viral_score: float = Field(default=0.0, ge=0.0, le=100.0, description="Viral score (0-100)")
    score_breakdown: ViralScoreBreakdown | None = Field(
        default=None, description="Detailed score components"
    )
    suggested_focus_prompt: str = Field(
        default="", description="Recommended SemIf / operator clipping focus prompt"
    )
    recommended_studio_profile: str = Field(
        default="hormozi", description="Recommended Studio Profile (hormozi, podcast_70_30, etc.)"
    )
    niche_category: str = Field(default="ai_tech", description="Niche category tag")


class NichePayoutProfile(BaseModel):
    """Monetization and competition profile for a YouTube Shorts niche."""

    niche_id: str = Field(..., description="Unique niche ID (e.g. finance, ai_tech)")
    display_name: str = Field(..., description="Human-readable title")
    shorts_rpm_range: tuple[float, float] = Field(
        ..., description="Direct Shorts ad revenue RPM range ($/1k views)"
    )
    funnel_cpm_range: tuple[float, float] = Field(
        ..., description="Related-video long-form funnel CPM range ($/1k views)"
    )
    avg_shorts_rpm: float = Field(..., description="Average Shorts ad RPM")
    avg_funnel_cpm: float = Field(..., description="Average Funnel CPM")
    effective_blended_rpm: float = Field(
        ..., description="Effective blended RPM considering direct ads + funnel conversions"
    )
    competition_level: str = Field(
        ..., description="Competition level: Low, Medium, High, or Very High"
    )
    saturation_score: float = Field(
        default=50.0, ge=0.0, le=100.0, description="Market saturation percentage (0-100)"
    )
    best_upload_hours_pt: list[str] = Field(
        default_factory=list, description="Optimal upload hours in US Pacific Time (PT)"
    )
    recommended_studio_preset: str = Field(
        default="hormozi", description="Recommended subtitle / visual preset"
    )
    recommended_dopamine_level: str = Field(
        default="medium", description="Recommended dopamine intensity level"
    )
    hook_angles: list[str] = Field(
        default_factory=list, description="High-converting hook angles and headline templates"
    )
    description: str = Field(
        default="", description="Niche overview and monetization characteristics"
    )


class ViralBGMTrack(BaseModel):
    """Curated royalty-free background music track optimized for voiceover ducking."""

    track_id: str = Field(..., description="Unique track identifier")
    title: str = Field(..., description="Track title")
    artist: str = Field(..., description="Artist / Style attribution")
    genre: str = Field(..., description="Genre (Phonk, Synthwave, Lo-Fi, Percussion, etc.)")
    bpm: int = Field(..., description="Beats per minute")
    mood: str = Field(..., description="Mood / emotional tone")
    best_fit_niches: list[str] = Field(default_factory=list, description="Recommended niches")
    source_url: str = Field(..., description="Source download URL or procedural generator key")
    license_type: str = Field(default="Royalty-Free / CC0", description="License type")
    is_installed: bool = Field(default=False, description="True if present in assets/music/")
    local_path: str | None = Field(default=None, description="Absolute local path if installed")


class TrendingShortsAudio(BaseModel):
    """Popular commercial or viral YouTube Shorts sound trend."""

    sound_id: str = Field(..., description="Unique sound identifier")
    title: str = Field(..., description="Song / sound title")
    artist: str = Field(..., description="Artist or creator")
    bpm: int = Field(..., description="Beats per minute")
    vibe: str = Field(..., description="Vibe description")
    trending_rank: int = Field(..., ge=1, description="Rank on current Shorts trending charts")
    best_fit_niche: str = Field(..., description="Primary niche fit")
    youtube_shorts_sound_url: str = Field(
        ..., description="Direct YouTube search or sound use link"
    )
    usage_tip: str = Field(
        default="", description="Practical tip on editing and pacing to this audio"
    )


class ViralStrategyBrief(BaseModel):
    """Complete AI-generated viral strategy brief for a specific niche and source."""

    brief_id: str = Field(..., description="Unique brief ID")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    niche: str = Field(..., description="Target niche")
    target_audience: str = Field(..., description="Target audience psychology and demographics")
    recommended_source: TrendingSourceVideo | None = Field(
        default=None, description="Top recommended video source"
    )
    clipping_strategy: str = Field(
        ..., description="Strategic direction for clipping or content creation"
    )
    three_hook_variants: list[str] = Field(
        default_factory=list, description="3 high-retention hook options"
    )
    recommended_studio_profile: str = Field(
        default="hormozi", description="Recommended Studio Profile"
    )
    recommended_background_theme: str = Field(
        default="minecraft", description="Recommended background theme"
    )
    recommended_bgm: str = Field(..., description="Recommended background track")
    projected_earnings: dict[str, Any] = Field(
        default_factory=dict, description="Projected earnings breakdown"
    )
    reasoning: str = Field(default="", description="Detailed rationale behind the strategy")


class FacelessShortRequest(BaseModel):
    """Request payload to generate a faceless short via Autonomous Faceless AI Shorts Studio."""

    niche: str = Field(
        default="ai_tech", description="Target niche: ai_tech, finance, business, science, etc."
    )
    topic: str | None = Field(
        default=None, description="Optional custom topic or prompt; auto-generated if omitted"
    )
    language: str = Field(default="en", description="Language code: en, es, hi, de, fr, pt, ja")
    voice: str = Field(
        default="en-US-ChristopherNeural", description="Microsoft Edge Neural TTS voice ID"
    )
    style_preset: str = Field(
        default="hormozi",
        description="Subtitle visual preset: hormozi, mrbeast, neon, pill, minimal, cyberpunk, cinematic",
    )
    layout_mode: str = Field(
        default="split_50_50",
        description="Compositor layout: split_50_50, 70_30, blur_bg, full_crop, pip",
    )
    background_category: str = Field(
        default="minecraft", description="Background video category: minecraft, subway, stock, etc."
    )
    duration_target_sec: int = Field(
        default=40, ge=15, le=60, description="Target duration in seconds"
    )
    auto_upload: bool = Field(
        default=False, description="Whether to automatically queue for upload upon completion"
    )


class FacelessScriptResult(BaseModel):
    """Generated script and metadata for a faceless Short."""

    topic: str = Field(..., description="The chosen topic")
    hook: str = Field(..., description="Opening 3-second hook")
    script_sentences: list[str] = Field(
        default_factory=list, description="Individual sentences for pacing"
    )
    full_script: str = Field(..., description="Complete voiceover script")
    title: str = Field(..., description="SEO-optimized Shorts title")
    description: str = Field(..., description="Full YouTube Shorts description with hashtags")
    hashtags: list[str] = Field(default_factory=list, description="Relevant hashtags")
    search_keywords: list[str] = Field(
        default_factory=list, description="Search terms for visual matching"
    )


__all__ = [
    "FacelessScriptResult",
    "FacelessShortRequest",
    "NichePayoutProfile",
    "TrendingShortsAudio",
    "TrendingSourceVideo",
    "ViralBGMTrack",
    "ViralScoreBreakdown",
    "ViralStrategyBrief",
]
