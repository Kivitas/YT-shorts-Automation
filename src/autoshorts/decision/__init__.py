"""Decision layer for AutoShorts: SemIf logit evaluation, sliding windows, and action tables.

Exports:
- slice_transcript_windows: Slices transcript into 30-60s candidate windows with sentence boundary snapping.
- select_non_overlapping_candidates: Selects highest-scoring non-overlapping windows.
- build_semif_prompt: Builds structured State / Question / Options prompt.
- compute_softmax: Numerically stable softmax probability calculation.
- evaluate_heuristic: Rule-based fallback evaluator (WPM, questions, exclamations, hook keywords).
- SEMIF_ACTION_TABLE: State machine mapping SemIf choices to pipeline actions.
- SemIfDecider: Full Decider protocol implementation.
"""

from __future__ import annotations

from autoshorts.decision.orchestrator import SemIfDecider
from autoshorts.decision.semif_engine import (
    HOOK_KEYWORDS,
    SEMIF_ACTION_TABLE,
    build_semif_prompt,
    compute_softmax,
    evaluate_heuristic,
)
from autoshorts.decision.window_slicer import (
    select_non_overlapping_candidates,
    slice_transcript_windows,
)

__all__ = [
    "HOOK_KEYWORDS",
    "SEMIF_ACTION_TABLE",
    "SemIfDecider",
    "build_semif_prompt",
    "compute_softmax",
    "evaluate_heuristic",
    "select_non_overlapping_candidates",
    "slice_transcript_windows",
]
