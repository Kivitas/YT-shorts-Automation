"""Pydantic schemas and data contracts for Multi-Agent SuperBrain Swarm.

Defines the core data models, agent roles, review protocols, SEO contracts,
audio plans, retention guardrails, and master orchestration state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class AgentRole(str, Enum):
    """Specialist agent roles participating in the SuperBrain swarm."""

    ORCHESTRATOR = "orchestrator"
    INFO_RESEARCHER = "info_researcher"
    HOOK_CRITIC = "hook_critic"
    MUSIC_SCOUT = "music_scout"
    VIRAL_SEO = "viral_seo"
    RETENTION_EDITOR = "retention_editor"


class ViralReview(BaseModel):
    """Hook critic evaluation review and calibration verdict."""

    hook_text: str = Field(..., description="Evaluated hook text or transcript opening sentence")
    score: float = Field(..., ge=0.0, le=100.0, description="Calibrated viral score (0-100)")
    verdict: Literal["approved", "fast_patch", "rejected"] = Field(
        ..., description="Calibration decision: approved (>=80), fast_patch (70-79), rejected (<70)"
    )
    critique: str = Field(default="", description="Reasoning and diagnostic feedback")
    patched_hook: str | None = Field(
        default=None,
        description="Fast-patched high-retention hook variant if verdict is fast_patch",
    )
    attempt: int = Field(default=1, ge=1, le=5, description="Evaluation iteration attempt")
    mode: Literal["mode_a", "mode_b"] = Field(
        default="mode_b", description="Mode B (generative) or Mode A (video link repurposing)"
    )
    suggested_shift_seconds: float = Field(
        default=0.0, description="Suggested window shift (+/- 5.0s) for Mode A repurposing"
    )
    candidate_index: int = Field(
        default=0, ge=0, description="Index of selected candidate clip window"
    )
    is_fallback: bool = Field(
        default=False, description="True if selected via argmax fallback after exhausted attempts"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class SEOMetadata(BaseModel):
    """Viral SEO package separating description hashtags from YouTube API search tags."""

    primary_title: str = Field(
        ..., max_length=100, description="Primary recommended video title (<100 chars)"
    )
    titles: list[str] = Field(
        default_factory=list, description="3 A/B title candidates (<100 chars each)"
    )
    description_hashtags: list[str] = Field(
        default_factory=list,
        description="3-5 hashtags starting with # for YouTube video description",
    )
    search_tags: list[str] = Field(
        default_factory=list,
        description="Clean keywords without # for YouTube API search tags (joined length <= 500 chars)",
    )
    category_id: str = Field(
        default="28", description="YouTube category ID (default 28: Science & Technology)"
    )

    @field_validator("titles")
    @classmethod
    def validate_titles(cls, v: list[str]) -> list[str]:
        cleaned = []
        for t in v:
            t_str = t.strip()
            if len(t_str) > 100:
                t_str = t_str[:97] + "..."
            cleaned.append(t_str)
        return cleaned

    @field_validator("description_hashtags")
    @classmethod
    def validate_hashtags(cls, v: list[str]) -> list[str]:
        cleaned = []
        for tag in v:
            t = tag.strip()
            if not t:
                continue
            if not t.startswith("#"):
                t = f"#{t}"
            cleaned.append(t)
        # Ensure 3-5 tags if possible
        return cleaned[:5]

    @field_validator("search_tags")
    @classmethod
    def validate_search_tags(cls, v: list[str]) -> list[str]:
        cleaned = []
        total_len = 0
        for tag in v:
            # Clean keywords without #
            t = tag.strip().lstrip("#")
            if not t:
                continue
            # Comma separator cost
            cost = len(t) + (1 if cleaned else 0)
            if total_len + cost > 500:
                break
            cleaned.append(t)
            total_len += cost
        return cleaned


class AudioPlan(BaseModel):
    """Rhythm and music scout decision plan matching speech cadence."""

    bgm_track_id: str | None = Field(default=None, description="Catalog track identifier")
    bgm_title: str = Field(default="", description="Resolved BGM track title")
    bgm_path: str | None = Field(
        default=None, description="Resolved absolute or relative path to music file"
    )
    tempo_bpm: int = Field(default=120, ge=40, le=240, description="Music tempo in BPM")
    target_bpm: int = Field(
        default=120, ge=40, le=240, description="Target BPM derived from speech WPS"
    )
    words_per_second: float = Field(
        default=0.0, ge=0.0, description="Calculated speech cadence (WPS)"
    )
    mood: str = Field(default="energetic", description="Matched musical mood")
    volume: float = Field(
        default=0.18, ge=0.0, le=1.0, description="Sidechain ducking base music volume"
    )
    ducking_release_ms: int = Field(
        default=250, description="Sidechain compressor release duration in ms"
    )
    confidence: float = Field(default=0.85, ge=0.0, le=1.0, description="Rhythm confidence score")


class RetentionPlan(BaseModel):
    """Retention editor guardrails and adaptive silence trimming plan."""

    adaptive_silence_thresh_db: float = Field(
        default=-32.0, description="Adaptive silence trimming threshold in dB (speech-dependent)"
    )
    speaker_zoom_center: Literal["left", "right", "center"] = Field(
        default="center", description="Optical speaker framing crop center"
    )
    pattern_interrupt_enabled: bool = Field(
        default=True, description="Enable preemptive pattern interrupts at retention cliffs"
    )
    max_interrupts: int = Field(
        default=4, ge=0, le=8, description="Maximum pattern interrupts allowed per 60s Short"
    )
    min_cooldown_seconds: float = Field(
        default=4.5, ge=1.0, description="Minimum cooldown between consecutive pattern interrupts"
    )
    cliff_slope_threshold: float = Field(
        default=-0.035, description="Retention derivative cliff threshold (dRetention/dt)"
    )
    jump_cuts_enabled: bool = Field(
        default=True, description="Enable word-guarded jump cut dead-air excision"
    )


class ResearchIntel(BaseModel):
    """Contextual research and trend intelligence."""

    niche: str = Field(default="general", description="Target content niche")
    topic: str = Field(default="", description="Core subject matter or viral trend")
    target_audience: str = Field(
        default="", description="Audience demographic & psychographic profile"
    )
    key_insights: list[str] = Field(
        default_factory=list, description="High-leverage talking points or facts"
    )
    viral_angles: list[str] = Field(
        default_factory=list, description="Hook angles and emotional triggers"
    )
    sources: list[str] = Field(
        default_factory=list, description="Reference video URLs or research sources"
    )
    trending_score: float = Field(
        default=0.0, ge=0.0, le=100.0, description="Viral trend velocity score"
    )
    projected_views: int = Field(default=500_000, description="Projected 48-hour view horizon")


class ViralMasterPlan(BaseModel):
    """Master production blueprint compiled by the SuperBrain orchestrator."""

    plan_id: str = Field(default_factory=lambda: str(uuid4()), description="Unique plan UUID")
    mode: Literal["mode_a", "mode_b"] = Field(
        default="mode_b", description="Mode A: link repurposing, Mode B: generative synthesis"
    )
    niche: str = Field(default="general")
    topic: str = Field(default="")
    source_url: str | None = Field(default=None, description="Source video link if Mode A")
    script_text: str = Field(default="", description="Approved script text or slice transcript")
    research: ResearchIntel | None = None
    hook_review: ViralReview | None = None
    seo: SEOMetadata | None = None
    audio: AudioPlan | None = None
    retention: RetentionPlan | None = None
    status: Literal["pending", "processing", "completed", "failed"] = Field(default="pending")
    quality_score: float = Field(default=0.0, ge=0.0, le=100.0)
    selected_window: tuple[float, float] | None = Field(
        default=None, description="Start and end timestamps in seconds (Mode A)"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MasterBrainState(BaseModel):
    """Execution state machine for the SuperBrain orchestrator."""

    plan_id: str
    mode: Literal["mode_a", "mode_b"] = "mode_b"
    phase: Literal[
        "phase1_context",
        "phase2_quality_gate",
        "phase3_swarm",
        "completed",
        "failed",
    ] = "phase1_context"
    master_plan: ViralMasterPlan
    history: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    is_credential_verified: bool = False
