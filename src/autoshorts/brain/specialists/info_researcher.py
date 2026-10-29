"""Information & Trend Researcher Agent.

Wraps trending_sources discovery and research_brief_agent strategy generation
to deliver structured ResearchIntel for viral synthesis.
"""

from __future__ import annotations

import asyncio
import logging

from autoshorts.brain.schemas import ResearchIntel
from autoshorts.core.config import Settings

logger = logging.getLogger("autoshorts.brain.info_researcher")


class InfoResearcherAgent:
    """Specialist agent that analyzes viral niches, audience psychology, and trending sources."""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config

    async def research(
        self,
        niche: str = "general",
        topic: str | None = None,
        allow_network: bool = True,
    ) -> ResearchIntel:
        """Conduct niche trend and audience research."""
        return await asyncio.to_thread(self._sync_research, niche, topic, allow_network)

    def _sync_research(
        self,
        niche: str,
        topic: str | None,
        allow_network: bool,
    ) -> ResearchIntel:
        from autoshorts.research_agent.research_brief_agent import generate_viral_strategy_brief

        norm_niche = (niche or "general").strip().lower()
        clean_topic = (topic or "").strip()

        if not allow_network:
            # The strategy brief always queries trending sources, so offline runs use the local fallback.
            return self._build_resilient_fallback(norm_niche, clean_topic)

        try:
            brief = generate_viral_strategy_brief(
                niche=norm_niche,
                custom_query=clean_topic or None,
                config=self.config,
            )

            # ViralStrategyBrief exposes one recommended source, flat strategy fields,
            # and a projected_earnings dict (not nested payout/source objects).
            source = brief.recommended_source
            sources: list[str] = [source.url] if source is not None and source.url else []
            top_score = float(source.viral_score) if source is not None else 75.0

            insights: list[str] = []
            if brief.clipping_strategy:
                insights.append(brief.clipping_strategy)
            if brief.reasoning:
                insights.append(brief.reasoning)
            payout = brief.projected_earnings or {}
            if payout.get("total_revenue_usd") is not None and (
                payout.get("effective_blended_rpm") is not None
            ):
                insights.append(
                    f"Projected payout: ${float(payout['total_revenue_usd']):.2f} per "
                    f"{payout.get('projected_views', 500_000)} views with RPM "
                    f"${float(payout['effective_blended_rpm']):.2f}"
                )

            hooks = (
                list(brief.three_hook_variants)
                or self._build_resilient_fallback(norm_niche, clean_topic).viral_angles
            )

            return ResearchIntel(
                niche=norm_niche,
                topic=clean_topic or brief.niche,
                target_audience=brief.target_audience,
                key_insights=insights,
                viral_angles=hooks,
                sources=sources,
                trending_score=top_score,
                projected_views=500_000,
            )
        except Exception as exc:
            logger.warning("ResearchBrief generation fallback due to error: %s", exc)
            return self._build_resilient_fallback(norm_niche, clean_topic)

    def _build_resilient_fallback(self, niche: str, topic: str) -> ResearchIntel:
        topic_display = topic or f"the hidden dynamics of {niche}"
        return ResearchIntel(
            niche=niche,
            topic=topic_display,
            target_audience="High-intent viewers seeking high-signal, engaging insights in short-form format.",
            key_insights=[
                f"High-retention focus on {topic_display}.",
                "Fast pattern interrupt within first 3 seconds.",
                "Direct resolution to curiosity gap before 45-second mark.",
            ],
            viral_angles=[
                f"What nobody tells you about {topic_display}...",
                f"The 1 mistake that destroys your results in {niche}.",
                f"If you only remember one thing about {topic_display}, remember this.",
            ],
            sources=[],
            trending_score=78.0,
            projected_views=500_000,
        )
