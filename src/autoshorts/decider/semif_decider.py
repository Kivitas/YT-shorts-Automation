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
    "compute_softmax",
    "evaluate_heuristic",
]
