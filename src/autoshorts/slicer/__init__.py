"""Window slicer and candidate generation package for AutoShorts."""

from autoshorts.slicer.window_generator import (
    compute_hook_curiosity_score,
    compute_window_heatmap_score,
    generate_candidate_windows,
    overlaps_sponsor_segment,
    select_non_overlapping_candidates,
    slice_transcript_windows,
)

__all__ = [
    "compute_hook_curiosity_score",
    "compute_window_heatmap_score",
    "generate_candidate_windows",
    "overlaps_sponsor_segment",
    "select_non_overlapping_candidates",
    "slice_transcript_windows",
]
