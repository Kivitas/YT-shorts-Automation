"""Heatmap-aware, sponsor-free, and chapter-aligned candidate window generator."""

from __future__ import annotations

from autoshorts.core.models import (
    CandidateClip,
    ClipCandidate,
    HeatmapPoint,
    SponsorSegment,
    VideoChapter,
)
from autoshorts.decision.window_slicer import (
    compute_hook_curiosity_score,
    compute_window_heatmap_score,
    generate_candidate_windows,
    overlaps_sponsor_segment,
    select_non_overlapping_candidates,
    slice_transcript_windows,
)

__all__ = [
    "CandidateClip",
    "ClipCandidate",
    "HeatmapPoint",
    "SponsorSegment",
    "VideoChapter",
    "compute_hook_curiosity_score",
    "compute_window_heatmap_score",
    "generate_candidate_windows",
    "overlaps_sponsor_segment",
    "select_non_overlapping_candidates",
    "slice_transcript_windows",
]
