"""AutoShorts Multi-Agent SuperBrain Swarm & Orchestrator.

Provides coordinated specialist agents and DAG orchestration for high-retention
YouTube Shorts synthesis and link repurposing.
"""

from __future__ import annotations

from autoshorts.brain.orchestrator import MasterBrainOrchestrator, PreflightCredentialError
from autoshorts.brain.schemas import (
    AgentRole,
    AudioPlan,
    MasterBrainState,
    ResearchIntel,
    RetentionPlan,
    SEOMetadata,
    ViralMasterPlan,
    ViralReview,
)
from autoshorts.brain.specialists import (
    HookCriticAgent,
    InfoResearcherAgent,
    MusicScoutAgent,
    RetentionEditorAgent,
    ViralSeoAgent,
)

__all__ = [
    "AgentRole",
    "AudioPlan",
    "HookCriticAgent",
    "InfoResearcherAgent",
    "MasterBrainOrchestrator",
    "MasterBrainState",
    "MusicScoutAgent",
    "PreflightCredentialError",
    "ResearchIntel",
    "RetentionEditorAgent",
    "RetentionPlan",
    "SEOMetadata",
    "ViralMasterPlan",
    "ViralReview",
    "ViralSeoAgent",
]
