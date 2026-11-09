"""Pydantic request/response payload models for the AutoShorts dashboard API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JobSubmitRequest(BaseModel):
    links: str = Field(..., description="Newline separated links with optional : instructions")
    custom_instructions: str | None = Field(default=None)
    provider: str | None = Field(default=None)
    background: str | None = Field(default=None)
    dopamine: str | None = Field(default=None)
    upload_mode: str | None = Field(default=None)
    auto_upload: bool = Field(default=False)
    immediate_upload: bool = Field(default=False)
    style_preset: str | None = Field(default=None)
    privacy_status: str | None = Field(default=None)
    # Advanced customization and upload fields
    layout_mode: str | None = Field(default=None)
    color_grading: str | None = Field(default=None)
    watermark_text: str | None = Field(default=None)
    watermark_position: str | None = Field(default=None)
    progress_bar_position: str | None = Field(default=None)
    text_transform: str | None = Field(default=None)
    subtitle_position: str | None = Field(default=None)
    hook_banner_style: str | None = Field(default=None)
    enable_emojis: bool | None = Field(default=None)
    category_id: str | None = Field(default=None)
    altered_content: bool | None = Field(default=None)
    custom_tags: list[str] | str | None = Field(default=None)
    auto_pin_comment: bool | None = Field(default=None)
    pin_comment_text: str | None = Field(default=None)
    link_related_video: bool | None = Field(default=None)
    related_video_url: str | None = Field(default=None)
    publish_at: str | None = Field(default=None)
    channel_profile_id: str | None = Field(default=None)
    crop_focus: str | None = Field(default=None)
    auto_trim_silence: bool | None = Field(default=None)
    skip_sponsors: bool | None = Field(default=None)
    # Render method selection (auto|raea|remotion|ffmpeg) and multi-method batches
    render_method: str | None = Field(default=None)
    render_methods: list[str] | str | None = Field(default=None)
    render_strategy: str | None = Field(default=None)


SubmitJobsRequest = JobSubmitRequest


class StoryJobRequest(BaseModel):
    mood: str | None = Field(default=None)
    topic: str | None = Field(default=None)
    provider: str | None = Field(default=None)


class LongformRequest(BaseModel):
    topic: str
    source_url: str | None = None
    genre: str = "auto"
    minutes: float = Field(default=3.0, ge=0.5, le=15.0)
    provider: str = "api"
    visual_style: str = "kinetic_dark"
    publish: bool = False


class MemeRequest(BaseModel):
    mode: str = "country_meme"  # "country_meme" or "types_of"
    topic: str = ""
    category: str = "countries"
    segment_count: int = Field(default=6, ge=3, le=12)
    aspect_ratio: str = "9:16"  # "9:16" or "16:9"
    visual_style: str = "anime_color"  # "anime_color" or "manga_bw"
    voice: str = "en-US-ChristopherNeural"
    publish: bool = False


class WojakResearchRequest(BaseModel):
    query: str = ""
    category: str | None = None
    aspect_ratio: str = "16:9"
    segment_count: int = Field(default=6, ge=3, le=15)


class WojakImproveRequest(BaseModel):
    topic: str = ""
    scenes: list[dict[str, Any]] = Field(default_factory=list)


class WojakGenerateRequest(BaseModel):
    topic: str = "hospital"
    aspect_ratio: str = "16:9"  # "16:9" or "9:16"
    segment_count: int = Field(default=6, ge=3, le=20)
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    generate_thumbnail: bool = True
    publish: bool = False


class LinkPreviewRequest(BaseModel):
    links_text: str = Field(..., description="Raw text containing one or more video URLs")


class JobPatchRequest(BaseModel):
    clip_title: str | None = Field(default=None, description="Updated clip title")
    hook_text: str | None = Field(default=None, description="Updated hook text")
    background_category: str | None = Field(default=None, description="Updated background category")
    dopamine_level: str | None = Field(default=None, description="Updated dopamine level")
    custom_instructions: str | None = Field(default=None, description="Updated custom instructions")
    # Advanced customization and upload fields
    layout_mode: str | None = Field(default=None)
    style_preset: str | None = Field(default=None)
    color_grading: str | None = Field(default=None)
    watermark_text: str | None = Field(default=None)
    watermark_position: str | None = Field(default=None)
    progress_bar_position: str | None = Field(default=None)
    text_transform: str | None = Field(default=None)
    subtitle_position: str | None = Field(default=None)
    hook_banner_style: str | None = Field(default=None)
    enable_emojis: bool | None = Field(default=None)
    category_id: str | None = Field(default=None)
    altered_content: bool | None = Field(default=None)
    custom_tags: list[str] | str | None = Field(default=None)
    auto_pin_comment: bool | None = Field(default=None)
    pin_comment_text: str | None = Field(default=None)
    link_related_video: bool | None = Field(default=None)
    related_video_url: str | None = Field(default=None)
    publish_at: str | None = Field(default=None)
    channel_profile_id: str | None = Field(default=None)
    privacy_status: str | None = Field(default=None)
    crop_focus: str | None = Field(default=None)
    auto_trim_silence: bool | None = Field(default=None)
    skip_sponsors: bool | None = Field(default=None)


class TestProviderRequest(BaseModel):
    provider: str = Field(..., description="LLM provider name: 'gemini', 'openrouter', or 'local'")
    api_key: str | None = Field(default=None, description="Optional temporary API key")


class SparkImportRequest(BaseModel):
    text: str = Field(..., description="Markdown or text containing Spark clips")


class SchedulerToggleRequest(BaseModel):
    paused: bool | None = None


class BackgroundDownloadRequest(BaseModel):
    categories: list[str] = Field(default_factory=lambda: ["minecraft", "subway"])
    min_clips: int = Field(default=1)


class MusicDownloadPayload(BaseModel):
    track_id: str = Field(..., description="Target track ID")
    custom_url: str | None = Field(default=None, description="Optional custom source URL")


class ResearchBriefPayload(BaseModel):
    niche: str = Field(default="ai_tech", description="Niche identifier")
    custom_query: str | None = Field(default=None, description="Optional search query")


class PipelineHandoffPayload(BaseModel):
    url: str = Field(..., description="YouTube video URL")
    niche: str = Field(default="ai_tech", description="Niche category")
    focus_prompt: str | None = Field(default=None, description="Focus prompt for clipping")
    recommended_studio_profile: str = Field(
        default="hormozi", description="Recommended Studio Profile"
    )
    recommended_background: str = Field(
        default="minecraft", description="Recommended background category"
    )
    auto_upload: bool = Field(default=False, description="Whether to trigger auto-upload")


class MoneyPrinterScriptPayload(BaseModel):
    niche: str = Field(default="ai_tech", description="Target niche")
    topic: str | None = Field(default=None, description="Custom topic prompt")
    language: str = Field(default="en", description="Language code")


class AnalyticsSyncPayload(BaseModel):
    force: bool = Field(default=False, description="Force sync flag")
