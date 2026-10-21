"""SemIf Decider with Multi-Signal Viral Clipping Fusion."""

from __future__ import annotations

from autoshorts.core.models import (
    CandidateClip,
    ClipCandidate,
    ClipSelection,
    SelectedClip,
)
from autoshorts.decision.orchestrator import (
    SemIfDecider,
    _fuse_candidate_score,
)
from autoshorts.decision.semif_engine import (
    HOOK_KEYWORDS,
    SEMIF_ACTION_TABLE,
    build_semif_prompt,
    compute_softmax,
    evaluate_heuristic,
)
from autoshorts.decision.window_slicer import (
    classify_semantic_hook,
    cluster_heatmap_spikes,
    compute_hook_curiosity_score,
    compute_narrative_completeness_score,
)

__all__ = [
    "HOOK_KEYWORDS",
    "SEMIF_ACTION_TABLE",
    "CandidateClip",
    "ClipCandidate",
    "ClipSelection",
    "SelectedClip",
    "SemIfDecider",
    "_fuse_candidate_score",
    "build_semif_prompt",
    "classify_semantic_hook",
    "cluster_heatmap_spikes",
    "compute_hook_curiosity_score",
    "compute_narrative_completeness_score",
    "compute_softmax",
    "evaluate_heuristic",
]
