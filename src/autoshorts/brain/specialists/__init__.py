"""Specialist agents for the AutoShorts Multi-Agent SuperBrain Swarm."""

from __future__ import annotations

from autoshorts.brain.specialists.hook_critic import HookCriticAgent
from autoshorts.brain.specialists.info_researcher import InfoResearcherAgent
from autoshorts.brain.specialists.music_scout import MusicScoutAgent
from autoshorts.brain.specialists.retention_editor import RetentionEditorAgent
from autoshorts.brain.specialists.viral_seo import ViralSeoAgent

__all__ = [
    "HookCriticAgent",
    "InfoResearcherAgent",
    "MusicScoutAgent",
    "RetentionEditorAgent",
    "ViralSeoAgent",
]
