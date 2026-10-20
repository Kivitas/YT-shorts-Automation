"""AI Viral Strategy Copilot.

Synthesizes trending video discovery, highest payout monetization matrix,
and high-retention audio pairing into an actionable, cohesive strategy brief
using the configured LLMProvider or a deterministic expert fallback.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from autoshorts.core.config import Settings, load_config
from autoshorts.research_agent.models import ViralStrategyBrief
from autoshorts.research_agent.payout_matrix import (
    calculate_projected_payout,
    get_niche_payout_matrix,
)
from autoshorts.research_agent.song_recommender import get_viral_bgm_catalog
from autoshorts.research_agent.trending_sources import discover_trending_sources

logger = logging.getLogger("autoshorts.research_agent.brief")


def generate_viral_strategy_brief(
    niche: str = "ai_tech",
    custom_query: str | None = None,
    config: Settings | None = None,
) -> ViralStrategyBrief:
    """Generate an AI-driven viral clipping and faceless strategy brief."""
    norm_niche = niche.lower().replace("-", "_").replace(" ", "_")

    # 1. Discover top trending sources
    sources = discover_trending_sources(niche=norm_niche, custom_query=custom_query, limit=5)
    top_source = sources[0] if sources else None

    # 2. Get payout profile & projected revenue
    profiles = {p.niche_id: p for p in get_niche_payout_matrix()}
    profile = profiles.get(norm_niche) or next(iter(profiles.values()))
    payout_data = calculate_projected_payout(
        profile.niche_id, projected_views=500_000, related_video_ctr_pct=2.5
    )

    # 3. Match best BGM track
    bgm_list = get_viral_bgm_catalog()
    best_bgm = None
    for track in bgm_list:
        if profile.niche_id in track.best_fit_niches:
            best_bgm = track
            break
    if not best_bgm and bgm_list:
        best_bgm = bgm_list[0]
    bgm_title = (
        f"{best_bgm.title} ({best_bgm.genre} - {best_bgm.bpm} BPM)"
        if best_bgm
        else "Drift Phonk (138 BPM)"
    )

    # Defaults for deterministic fallback
    audience_fallbacks = {
        "finance": "Ambitious 20-35 year olds looking to build real wealth, escape the 9-to-5 rat race, and avoid financial mistakes.",
        "ai_tech": "Tech professionals, developers, and creators hungry for high-leverage AI productivity tools and future career safety.",
        "business": "Entrepreneurs, side-hustlers, and creators aiming to build profitable scalable modern businesses.",
        "health": "Health-conscious biohackers and high performers optimizing sleep, cognitive clarity, and physical longevity.",
        "science": "Curious thinkers and science enthusiasts who love mind-bending astrophysics, quantum mechanics, and futuristic discoveries.",
        "self_improvement": "Discipline seekers and young professionals interested in stoicism, deep work, and escaping modern distractions.",
        "podcasts": "Everyday entertainment seekers who love high-stakes debates, celebrity confessions, and humorous cultural commentary.",
        "gaming": "Next-gen gamers and esports enthusiasts who crave high-octane gameplay moments, glitch breakdowns, and competitive drama.",
    }

    target_audience = audience_fallbacks.get(
        norm_niche,
        "Broad, high-intent digital audience seeking concise, highly engaging, and actionable insights.",
    )

    clip_strategy = (
        f"Clip the central 35-45s climax of the discussion. Start directly at the most provocative statement without "
        f"introductory pleasantries. Use the native YouTube Related Video button to funnel viewers directly to "
        f"{top_source.url if top_source else 'the full original video'}."
    )

    hooks = (
        list(profile.hook_angles)
        if profile.hook_angles
        else [
            "What nobody tells you about this until it's too late...",
            "If you do not know this by age 30, you are falling behind.",
            "The single most important rule that changed everything.",
        ]
    )

    reasoning = (
        f"In the {profile.display_name} niche, short-form viewers have an average attention threshold of 2.5 seconds. "
        f"Pairing the recommended {profile.recommended_studio_preset.upper()} caption preset with subtle {bgm_title} "
        f"sidechain ducking ensures high vocal intelligibility while sustaining dopamine arousal throughout the 40-second window."
    )

    # 4. Attempt LLM enhancement if configured
    cfg = config or load_config()
    if cfg and getattr(cfg, "llm", None):
        try:
            from autoshorts.core.providers import get_provider

            provider = get_provider(name=cfg.llm.provider, settings=cfg)
            v_title = top_source.title if top_source else profile.display_name
            prompt = (
                f"You are an elite YouTube Shorts Strategist. Return a JSON object with 4 keys:\n"
                f"- target_audience: string describing the psychology of viewers in {profile.display_name}\n"
                f"- clipping_strategy: string detailing how to edit video '{v_title}' for 90%+ retention\n"
                f"- three_hook_variants: list of 3 explosive opening hooks (each under 14 words)\n"
                f"- reasoning: string explaining the retention psychology and funnel strategy\n"
                f"Respond ONLY with valid JSON."
            )
            raw_text = provider.complete(prompt)
            # Find JSON inside response
            s = raw_text.strip()
            if s.startswith("```"):
                s = s.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                target_audience = parsed.get("target_audience") or target_audience
                clip_strategy = parsed.get("clipping_strategy") or clip_strategy
                h_list = parsed.get("three_hook_variants")
                if isinstance(h_list, list) and len(h_list) >= 3:
                    hooks = [str(x) for x in h_list[:3]]
                reasoning = parsed.get("reasoning") or reasoning
        except Exception as llm_err:
            logger.debug(
                "LLM brief synthesis skipped/failed (%s); using deterministic expert fallback.",
                llm_err,
            )

    return ViralStrategyBrief(
        brief_id=f"brief_{uuid4().hex[:10]}",
        created_at=datetime.now(UTC).isoformat(),
        niche=profile.niche_id,
        target_audience=target_audience,
        recommended_source=top_source,
        clipping_strategy=clip_strategy,
        three_hook_variants=hooks[:3],
        recommended_studio_profile=profile.recommended_studio_preset,
        recommended_background_theme="minecraft" if "gaming" in norm_niche else "subway",
        recommended_bgm=bgm_title,
        projected_earnings=payout_data,
        reasoning=reasoning,
    )


__all__ = ["generate_viral_strategy_brief"]
