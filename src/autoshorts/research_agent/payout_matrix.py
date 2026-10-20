"""Genre & Highest Payout (Shorts RPM + Long-Form Funnel CPM) Intelligence.

Provides detailed RPM/CPM profiles for the top 8 YouTube Shorts niches,
ordered by effective blended RPM, and an interactive payout projector
calculating direct Shorts ad-share and long-form funnel revenue.
"""

from __future__ import annotations

from typing import Any

from autoshorts.research_agent.models import NichePayoutProfile

NICHE_CATALOG: list[dict[str, Any]] = [
    {
        "niche_id": "finance",
        "display_name": "Finance & Investing",
        "shorts_rpm_range": (0.25, 0.45),
        "funnel_cpm_range": (28.00, 55.00),
        "avg_shorts_rpm": 0.35,
        "avg_funnel_cpm": 41.50,
        "effective_blended_rpm": 1.38,
        "competition_level": "High",
        "saturation_score": 72.0,
        "best_upload_hours_pt": ["06:00", "11:00", "16:00"],
        "recommended_studio_preset": "cinematic",
        "recommended_dopamine_level": "low",
        "hook_angles": [
            "The top 1% never put their money in a savings account. Here is where it actually goes...",
            "If you have $1,000 in the bank, this single IRS rule changes everything.",
            "Why buying a house in 2026 is mathematically a trap for 90% of people.",
        ],
        "description": "Highest RPM on YouTube. Financial institutions bid aggressively. Viewers who click the Related Video link generate massive long-form funnel ad payouts.",
    },
    {
        "niche_id": "ai_tech",
        "display_name": "AI Tools & SaaS Tech",
        "shorts_rpm_range": (0.20, 0.38),
        "funnel_cpm_range": (22.00, 45.00),
        "avg_shorts_rpm": 0.29,
        "avg_funnel_cpm": 33.50,
        "effective_blended_rpm": 1.12,
        "competition_level": "Medium",
        "saturation_score": 58.0,
        "best_upload_hours_pt": ["07:00", "12:00", "17:00"],
        "recommended_studio_preset": "hormozi",
        "recommended_dopamine_level": "medium",
        "hook_angles": [
            "Stop using ChatGPT like an amateur. This one prompt does 8 hours of work in 30 seconds.",
            "OpenAI just released a tool that replaces entire software engineering departments.",
            "3 free AI websites that feel completely illegal to know in 2026.",
        ],
        "description": "Exploding enterprise and developer advertiser demand. Highly viral across LinkedIn and Twitter clips with exceptional click-through to full product breakdowns.",
    },
    {
        "niche_id": "business",
        "display_name": "Business & Startups",
        "shorts_rpm_range": (0.18, 0.35),
        "funnel_cpm_range": (20.00, 40.00),
        "avg_shorts_rpm": 0.265,
        "avg_funnel_cpm": 30.00,
        "effective_blended_rpm": 1.01,
        "competition_level": "Medium",
        "saturation_score": 60.0,
        "best_upload_hours_pt": ["06:00", "10:00", "15:00"],
        "recommended_studio_preset": "podcast_70_30",
        "recommended_dopamine_level": "low",
        "hook_angles": [
            "How this 24-year-old built a $10M company from his bedroom with zero funding.",
            "The counter-intuitive pricing secret Alex Hormozi used to 10x his profit margins.",
            "Why 99% of businesses fail in year two, and the one metric that predicts it.",
        ],
        "description": "High business-to-business (B2B) advertiser CPMs. High intent audience with high affiliate and sponsor conversion rates.",
    },
    {
        "niche_id": "health",
        "display_name": "Health & Longevity",
        "shorts_rpm_range": (0.15, 0.30),
        "funnel_cpm_range": (18.00, 32.00),
        "avg_shorts_rpm": 0.225,
        "avg_funnel_cpm": 25.00,
        "effective_blended_rpm": 0.85,
        "competition_level": "Medium",
        "saturation_score": 52.0,
        "best_upload_hours_pt": ["05:00", "09:00", "18:00"],
        "recommended_studio_preset": "podcast_70_30",
        "recommended_dopamine_level": "medium",
        "hook_angles": [
            "If you drink coffee within 90 minutes of waking up, you are ruining your afternoon energy.",
            "The 3-minute sleep protocol neuroscientists use to fall asleep instantly.",
            "The single blood marker you must test before turning 30.",
        ],
        "description": "Driven by Huberman and Attia biohacking trends. Premium supplement and fitness hardware advertisers drive strong RPMs.",
    },
    {
        "niche_id": "science",
        "display_name": "Science & Space",
        "shorts_rpm_range": (0.12, 0.25),
        "funnel_cpm_range": (14.00, 26.00),
        "avg_shorts_rpm": 0.185,
        "avg_funnel_cpm": 20.00,
        "effective_blended_rpm": 0.68,
        "competition_level": "Low",
        "saturation_score": 35.0,
        "best_upload_hours_pt": ["08:00", "13:00", "19:00"],
        "recommended_studio_preset": "cinematic",
        "recommended_dopamine_level": "high",
        "hook_angles": [
            "James Webb just found something at the edge of the universe that breaks all known laws of physics.",
            "What happens if you fall into a supermassive black hole? It's far worse than you think.",
            "Quantum physicists finally proved that reality does not exist until it is observed.",
        ],
        "description": "Highest organic global virality and retention. Low competitive saturation with broad educational appeal across all age brackets.",
    },
    {
        "niche_id": "self_improvement",
        "display_name": "Mindset & Self-Improvement",
        "shorts_rpm_range": (0.10, 0.22),
        "funnel_cpm_range": (12.00, 22.00),
        "avg_shorts_rpm": 0.16,
        "avg_funnel_cpm": 17.00,
        "effective_blended_rpm": 0.58,
        "competition_level": "High",
        "saturation_score": 78.0,
        "best_upload_hours_pt": ["06:00", "12:00", "20:00"],
        "recommended_studio_preset": "hormozi",
        "recommended_dopamine_level": "medium",
        "hook_angles": [
            "Marcus Aurelius wrote this 2,000 years ago, and it solves modern anxiety completely.",
            "The 1 habit that separated the top 0.1% performers from everyone else.",
            "Stop waiting for motivation. Here is how dopamine actually works in your brain.",
        ],
        "description": "Massive volume and shareability. High retention when paired with Hormozi kinetic typography and tension BGM.",
    },
    {
        "niche_id": "podcasts",
        "display_name": "Podcasts & Interviews",
        "shorts_rpm_range": (0.08, 0.18),
        "funnel_cpm_range": (8.00, 16.00),
        "avg_shorts_rpm": 0.13,
        "avg_funnel_cpm": 12.00,
        "effective_blended_rpm": 0.43,
        "competition_level": "Very High",
        "saturation_score": 88.0,
        "best_upload_hours_pt": ["09:00", "14:00", "18:00"],
        "recommended_studio_preset": "pip_reaction",
        "recommended_dopamine_level": "medium",
        "hook_angles": [
            "He could not believe what the guest revealed after the cameras were supposed to stop rolling...",
            "The confession that stunned the entire studio into absolute silence.",
            "The untold story behind the biggest internet scandal of the decade.",
        ],
        "description": "Massive clip inventory available online. Extremely high engagement with debate, controversy, and humorous split-screen reaction formats.",
    },
    {
        "niche_id": "gaming",
        "display_name": "Gaming & Esports",
        "shorts_rpm_range": (0.05, 0.14),
        "funnel_cpm_range": (6.00, 12.00),
        "avg_shorts_rpm": 0.095,
        "avg_funnel_cpm": 9.00,
        "effective_blended_rpm": 0.32,
        "competition_level": "Very High",
        "saturation_score": 95.0,
        "best_upload_hours_pt": ["11:00", "15:00", "19:00"],
        "recommended_studio_preset": "mrbeast",
        "recommended_dopamine_level": "max",
        "hook_angles": [
            "This pro player made a 1 in a million clutch play that had the entire arena screaming.",
            "The secret glitch that speedrunners hid for 10 years just got exposed.",
            "No human has ever beaten this level... until today.",
        ],
        "description": "Youngest demographic and highest raw view counts. Requires max dopamine pacing, rapid sound effects, and MrBeast zoom punch-ins.",
    },
]


def get_niche_payout_matrix() -> list[NichePayoutProfile]:
    """Return all 8 niche payout profiles sorted by highest effective blended RPM."""
    profiles = [NichePayoutProfile(**entry) for entry in NICHE_CATALOG]
    profiles.sort(key=lambda p: p.effective_blended_rpm, reverse=True)
    return profiles


def calculate_projected_payout(
    niche_id: str,
    projected_views: int = 500_000,
    related_video_ctr_pct: float = 2.5,
) -> dict[str, Any]:
    """Calculate direct Shorts ad-share and long-form funnel revenue projection.

    - Direct Shorts Revenue = (views / 1,000) * avg_shorts_rpm
    - Funnel Clicks = views * (ctr_pct / 100)
    - Funnel Revenue = (clicks / 1,000) * avg_funnel_cpm
    - Total Blended Revenue = Direct + Funnel
    - Effective Blended RPM = (Total / views) * 1,000
    """
    profiles = {p.niche_id: p for p in get_niche_payout_matrix()}
    norm_niche = niche_id.lower().replace("-", "_").replace(" ", "_")
    profile = profiles.get(norm_niche)
    if not profile:
        profile = profiles.get("ai_tech") or next(iter(profiles.values()))

    views = max(1_000, projected_views)
    ctr = max(0.1, min(20.0, related_video_ctr_pct))

    direct_shorts_revenue = round((views / 1_000.0) * profile.avg_shorts_rpm, 2)
    funnel_clicks = round(views * (ctr / 100.0))
    funnel_revenue = round((funnel_clicks / 1_000.0) * profile.avg_funnel_cpm, 2)
    total_revenue = round(direct_shorts_revenue + funnel_revenue, 2)
    effective_blended_rpm = round((total_revenue / views) * 1_000.0, 3)

    return {
        "niche_id": profile.niche_id,
        "display_name": profile.display_name,
        "projected_views": views,
        "related_video_ctr_pct": ctr,
        "avg_shorts_rpm": profile.avg_shorts_rpm,
        "avg_funnel_cpm": profile.avg_funnel_cpm,
        "direct_shorts_revenue_usd": direct_shorts_revenue,
        "funnel_clicks": funnel_clicks,
        "funnel_revenue_usd": funnel_revenue,
        "total_revenue_usd": total_revenue,
        "effective_blended_rpm": effective_blended_rpm,
    }


__all__ = [
    "NICHE_CATALOG",
    "calculate_projected_payout",
    "get_niche_payout_matrix",
]
