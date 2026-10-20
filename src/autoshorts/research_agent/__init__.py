"""AutoShorts Built-In Viral Research Agent & Autonomous Faceless AI Shorts Studio Engine.

Provides live trending video and podcast discovery, highest payout RPM/CPM matrix,
trending viral songs and 1-click BGM downloader, AI strategy briefs, and
autonomous faceless Short generation with Microsoft Edge Neural TTS.
"""

from __future__ import annotations

from autoshorts.research_agent.models import (
    FacelessScriptResult,
    FacelessShortRequest,
    NichePayoutProfile,
    TrendingShortsAudio,
    TrendingSourceVideo,
    ViralBGMTrack,
    ViralScoreBreakdown,
    ViralStrategyBrief,
)
from autoshorts.research_agent.moneyprinter import (
    VOICE_CATALOG,
    create_faceless_short_job,
    generate_faceless_script,
    synthesize_neural_tts,
)
from autoshorts.research_agent.payout_matrix import (
    calculate_projected_payout,
    get_niche_payout_matrix,
)
from autoshorts.research_agent.research_brief_agent import generate_viral_strategy_brief
from autoshorts.research_agent.song_recommender import (
    download_viral_bgm,
    get_trending_shorts_audio,
    get_viral_bgm_catalog,
)
from autoshorts.research_agent.trending_sources import (
    compute_viral_score,
    discover_trending_sources,
    format_duration,
)

__all__ = [
    "VOICE_CATALOG",
    "FacelessScriptResult",
    "FacelessShortRequest",
    "NichePayoutProfile",
    "TrendingShortsAudio",
    "TrendingSourceVideo",
    "ViralBGMTrack",
    "ViralScoreBreakdown",
    "ViralStrategyBrief",
    "calculate_projected_payout",
    "compute_viral_score",
    "create_faceless_short_job",
    "discover_trending_sources",
    "download_viral_bgm",
    "format_duration",
    "generate_faceless_script",
    "generate_viral_strategy_brief",
    "get_niche_payout_matrix",
    "get_trending_shorts_audio",
    "get_viral_bgm_catalog",
    "synthesize_neural_tts",
]
