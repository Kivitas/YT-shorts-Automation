"""Core data models and state machine for AutoShorts.

Defines Pydantic v2 models for Jobs, Candidates, Selections, Edit Decision Lists (EDL),
Video Metadata, Upload Results, and the JobStatus state machine per PLAN.md section 4.3 & 5.5.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Lifecycle state machine for a video clipping job (PLAN.md section 4.3)."""

    QUEUED = "QUEUED"
    METADATA_FETCHED = "METADATA_FETCHED"
    TRANSCRIBED = "TRANSCRIBED"
    WINDOWS_SLICED = "WINDOWS_SLICED"
    SEMIF_EVALUATED = "SEMIF_EVALUATED"
    DOWNLOADING_SECTION = "DOWNLOADING_SECTION"
    SUBTITLES_GENERATED = "SUBTITLES_GENERATED"
    COMPOSITING = "COMPOSITING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    SCHEDULED = "SCHEDULED"
    UPLOADING = "UPLOADING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    CLEANED = "CLEANED"
    FAILED = "FAILED"


# Valid state machine transitions per PLAN.md section 4.3
VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.METADATA_FETCHED, JobStatus.FAILED},
    JobStatus.METADATA_FETCHED: {JobStatus.TRANSCRIBED, JobStatus.FAILED},
    JobStatus.TRANSCRIBED: {JobStatus.WINDOWS_SLICED, JobStatus.FAILED},
    JobStatus.WINDOWS_SLICED: {JobStatus.SEMIF_EVALUATED, JobStatus.FAILED},
    JobStatus.SEMIF_EVALUATED: {JobStatus.DOWNLOADING_SECTION, JobStatus.FAILED},
    JobStatus.DOWNLOADING_SECTION: {JobStatus.SUBTITLES_GENERATED, JobStatus.FAILED},
    JobStatus.SUBTITLES_GENERATED: {JobStatus.COMPOSITING, JobStatus.FAILED},
    JobStatus.COMPOSITING: {JobStatus.READY_FOR_REVIEW, JobStatus.SCHEDULED, JobStatus.FAILED},
    JobStatus.READY_FOR_REVIEW: {JobStatus.SCHEDULED, JobStatus.UPLOADING, JobStatus.FAILED},
    JobStatus.SCHEDULED: {JobStatus.UPLOADING, JobStatus.FAILED},
    JobStatus.UPLOADING: {JobStatus.VERIFYING, JobStatus.FAILED},
    JobStatus.VERIFYING: {JobStatus.COMPLETED, JobStatus.FAILED},
    JobStatus.COMPLETED: {JobStatus.CLEANED, JobStatus.FAILED},
    JobStatus.CLEANED: set(),
    JobStatus.FAILED: {JobStatus.QUEUED},  # Allow retry
}


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    """Return True if transitioning from current to target status is valid."""
    if current == target:
        return True
    return target in VALID_TRANSITIONS.get(current, set())


class HeatmapPoint(BaseModel):
    """A single point/bucket from YouTube's 'Most Replayed' retention heatmap."""

    start_time: float = Field(..., ge=0.0, description="Start time in seconds")
    end_time: float = Field(..., ge=0.0, description="End time in seconds")
    value: float = Field(default=0.0, ge=0.0, le=1.0, description="Retention intensity (0.0 - 1.0)")


class VideoChapter(BaseModel):
    """A chapter marker extracted from YouTube video metadata."""

    title: str = Field(..., description="Chapter title")
    start_time: float = Field(..., ge=0.0, description="Start time in seconds")
    end_time: float = Field(..., ge=0.0, description="End time in seconds")


class SponsorSegment(BaseModel):
    """A sponsor, intro, outro, or self-promo segment from SponsorBlock."""

    category: str = Field(default="sponsor", description="Segment category")
    start_time: float = Field(..., ge=0.0, description="Start time in seconds")
    end_time: float = Field(..., ge=0.0, description="End time in seconds")


class VideoMetadata(BaseModel):
    """Metadata extracted from the source YouTube video without downloading video bytes."""

    title: str = Field(..., description="Video title")
    channel: str = Field(..., description="Channel author / creator name")
    channel_url: str = Field(..., description="URL to author's YouTube channel")
    video_url: str = Field(..., description="Source YouTube watch URL")
    duration: float = Field(..., ge=0.0, description="Total video duration in seconds")
    video_id: str | None = Field(default=None, description="YouTube 11-char ID")
    description: str | None = Field(default=None, description="Original video description")
    heatmap: list[HeatmapPoint] = Field(
        default_factory=list, description="YouTube Most Replayed retention heatmap"
    )
    chapters: list[VideoChapter] = Field(
        default_factory=list, description="Extracted video chapters"
    )
    sponsor_segments: list[SponsorSegment] = Field(
        default_factory=list, description="SponsorBlock skip segments"
    )


class ClipCandidate(BaseModel):
    """A 30-60 second candidate transcript window evaluated by SemIf."""

    start: float = Field(..., ge=0.0, description="Start timestamp in seconds")
    end: float = Field(..., ge=0.0, description="End timestamp in seconds")
    text: str = Field(..., description="Concatenated transcript text within this window")
    score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="SemIf retention score (0.0 - 1.0)"
    )
    reason: str | None = Field(default=None, description="Scoring rationale or topic label")
    heatmap_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="YouTube Most Replayed retention score (0.0 - 1.0)"
    )
    hook_strength: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Opening sentence curiosity hook score (0.0 - 1.0)"
    )
    chapter_title: str | None = Field(
        default=None, description="Overlapping video chapter title if present"
    )

    @property
    def duration(self) -> float:
        """Calculate duration of candidate window in seconds."""
        return max(0.0, self.end - self.start)


CandidateClip = ClipCandidate


class ClipSelection(BaseModel):
    """A chosen candidate segment configured for downloading and rendering."""

    candidate: ClipCandidate = Field(..., description="Underlying candidate window")
    clip_index: int = Field(default=0, ge=0, description="Index of this clip for the source video")
    target_duration: float = Field(default=45.0, ge=10.0, le=60.0)
    background_style: str = Field(default="minecraft", description="Selected background theme")
    hook_text: str = Field(default="", description="First 3-second hook question/card")
    title: str = Field(default="", description="Recommended Shorts title with hashtag")
    title_variants: list[str] = Field(
        default_factory=list,
        description="3 viral title angles (Curiosity, Contrarian, Direct Punch)",
    )
    thumbnail_path: str | None = Field(
        default=None, description="Path to extracted or rendered thumbnail image"
    )
    bgm_path: str | None = Field(default=None, description="Explicit background music path")
    bgm_volume: float | None = Field(
        default=None, description="BGM volume multiplier (0.0 to 1.0)"
    )


SelectedClip = ClipSelection


class JumpCut(BaseModel):
    """Timestamp interval marking dead air or filler speech to excise."""

    cut_start: float = Field(..., ge=0.0, description="Start time of segment to excise")
    cut_end: float = Field(..., ge=0.0, description="End time of segment to excise")
    reason: str = Field(default="", description="Reason for jump cut (e.g., pause, filler)")


class ZoomPunchIn(BaseModel):
    """Camera zoom punch-in synchronized to peak emphasis words."""

    timestamp: float = Field(..., ge=0.0, description="Timestamp for punch-in")
    scale: float = Field(default=1.10, ge=1.05, le=1.25, description="Zoom factor (1.05 - 1.25)")
    duration: float = Field(default=0.8, ge=0.3, le=2.0, description="Punch-in duration in seconds")
    emphasis_word: str = Field(default="", description="Peak word triggering the zoom")


class CaptionCorrection(BaseModel):
    """A vocabulary correction for misheard jargon, names, or slang."""

    original: str = Field(..., description="Misheard word from Whisper")
    corrected: str = Field(..., description="Corrected spelling/jargon")


class EditDecisionList(BaseModel):
    """Symbolic Edit Decision List (EDL) per PLAN.md section 5.5.

    Generated by AI multimodal edit planning; executed deterministically by FFmpeg.
    """

    hook_in_sec: float = Field(default=0.0, ge=0.0, description="Exact trim start in seconds")
    hook_out_sec: float = Field(default=45.0, ge=0.0, description="Exact trim end in seconds")
    jump_cuts: list[JumpCut] = Field(default_factory=list, description="Excised silence intervals")
    zoom_punch_ins: list[ZoomPunchIn] = Field(
        default_factory=list, description="Visual camera punch-ins"
    )
    caption_corrections: list[CaptionCorrection] = Field(
        default_factory=list, description="Vocabulary fixes"
    )
    recommended_thumbnail_timestamp: float = Field(
        default=0.0, ge=0.0, description="Expressive facial timestamp"
    )
    recommended_title: str = Field(default="", max_length=60, description="Shorts title")
    recommended_hook: str = Field(default="", max_length=100, description="Hook text")
    background_style_recommendation: Literal["minecraft", "subway", "auto"] = Field(
        default="minecraft", description="Recommended background"
    )


class UploadResult(BaseModel):
    """Outcome of YouTube video upload and verification."""

    video_id: str = Field(..., description="YouTube 11-char video ID")
    url: str = Field(..., description="Direct link: https://youtube.com/shorts/{video_id}")
    status: str = Field(
        default="uploaded", description="Status (uploaded, published, unlisted, private)"
    )
    verified: bool = Field(
        default=False, description="True once verified via videos.list or Studio DOM"
    )
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    quota_spent: int = Field(default=1, description="API quota units consumed from upload bucket")
    privacy_status: str = Field(default="unlisted", description="Applied privacy status")
    raw_response: dict[str, Any] = Field(default_factory=dict, description="Raw API response data")


class ChannelProfile(BaseModel):
    """Configuration profile for a specific YouTube channel."""

    id: str = "default"
    name: str = "Default Channel"
    handle: str = "@MyChannel"
    token_path: str = "data/token.json"
    edge_profile_dir: str = "data/edge_profile"
    default_privacy_status: str = "public"
    default_category_id: str = "24"
    watermark_text: str | None = None


class Job(BaseModel):
    """Primary pipeline work item tracking end-to-end progress of a video clipping task."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12], description="Unique job identifier")
    source_url: str = Field(..., description="Source YouTube video URL")
    custom_instructions: str | None = Field(
        default=None, description="Per-job operator instructions"
    )
    status: JobStatus = Field(default=JobStatus.QUEUED, description="Current lifecycle stage")
    video_metadata: VideoMetadata | None = Field(
        default=None, description="Extracted video metadata"
    )
    candidate_windows: list[ClipCandidate] = Field(
        default_factory=list, description="Sliding windows"
    )
    selected_clips: list[ClipSelection] = Field(default_factory=list, description="Chosen clips")
    edl: EditDecisionList | None = Field(default=None, description="Generated Edit Decision List")
    raw_video_path: str | None = Field(default=None, description="Path to precision downloaded cut")
    subtitles_path: str | None = Field(default=None, description="Path to generated .ass subtitles")
    bgm_path: str | None = Field(default=None, description="Explicit background music path")
    bgm_volume: float | None = Field(default=None, description="BGM volume multiplier (0.0 to 1.0)")
    viral_master_plan: dict[str, Any] | None = Field(
        default=None, description="Compiled multi-agent plan"
    )
    rhythm_metadata: dict[str, Any] | None = Field(
        default=None, description="Rhythm and beat grid info"
    )
    rendered_video_path: str | None = Field(
        default=None, description="Path to master 1080x1920 MP4"
    )
    preview_video_path: str | None = Field(default=None, description="Path to low-res preview MP4")
    style_preset: str = Field(
        default="neon", description="Subtitle visual preset: neon, minimal, or pill"
    )
    privacy_status: str | None = Field(
        default=None, description="Target YouTube privacy status override"
    )
    layout_mode: str = "split_50_50"
    color_grading: str = "none"
    watermark_text: str | None = None
    watermark_position: str = "top_right"
    progress_bar_position: str = "seam"
    text_transform: str = "uppercase"
    subtitle_position: str = "center"
    hook_banner_style: str = "neon_box"
    enable_emojis: bool = True
    category_id: str = "24"
    default_language: str = "en"
    altered_content: bool = False
    custom_tags: list[str] = Field(default_factory=list)
    auto_pin_comment: bool = False
    link_related_video: bool = True
    publish_at: str | None = None
    channel_profile_id: str = "default"
    crop_focus: str = "center"
    auto_trim_silence: bool = True
    skip_sponsors: bool = True
    scheduled_time: datetime | None = Field(default=None, description="Scheduled upload timestamp")
    upload_result: UploadResult | None = Field(default=None, description="Upload outcome")
    error_message: str | None = Field(default=None, description="Last error message if failed")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def transition_to(self, new_status: JobStatus) -> None:
        """Safely transition this job to a new status, checking validity."""
        from autoshorts.core.exceptions import InvalidStateTransitionError

        if not can_transition(self.status, new_status):
            raise InvalidStateTransitionError(self.status.value, new_status.value)
        self.status = new_status
        self.updated_at = datetime.now(UTC)


__all__ = [
    "VALID_TRANSITIONS",
    "CandidateClip",
    "CaptionCorrection",
    "ChannelProfile",
    "ClipCandidate",
    "ClipSelection",
    "EditDecisionList",
    "HeatmapPoint",
    "Job",
    "JobStatus",
    "JumpCut",
    "SelectedClip",
    "SponsorSegment",
    "UploadResult",
    "VideoChapter",
    "VideoMetadata",
    "ZoomPunchIn",
    "can_transition",
]
