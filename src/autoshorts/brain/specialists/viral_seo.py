"""Viral SEO Specialist Agent.

Generates 3 distinct A/B titles (<100 characters), a 3-layer hashtag pyramid
(3-5 description #-tags), and clean keyword search tags (<=500 characters for YouTube API).
"""

from __future__ import annotations

import logging
import re

from autoshorts.brain.schemas import SEOMetadata

logger = logging.getLogger("autoshorts.brain.viral_seo")

NICHE_CATEGORY_MAP: dict[str, str] = {
    "ai_tech": "28",  # Science & Technology
    "science": "28",
    "gaming": "20",  # Gaming
    "business": "27",  # Education
    "finance": "27",  # Education
    "health": "27",  # Education
    "self_improvement": "27",  # Education
    "podcasts": "24",  # Entertainment
    "music": "10",  # Music
}


class ViralSeoAgent:
    """Specialist agent responsible for metadata packaging, title A/B options, and SEO compliance."""

    async def generate_seo(
        self,
        hook: str,
        topic: str = "",
        niche: str = "general",
        key_insights: list[str] | None = None,
    ) -> SEOMetadata:
        """Construct full SEO package respecting platform character limits and tag formatting."""
        clean_hook = (hook or "Must Watch").strip()
        # Remove trailing period if present for titles
        clean_hook_title = clean_hook.rstrip(".!?")
        clean_topic = (topic or niche or "Shorts").strip().title()
        norm_niche = (niche or "general").strip().lower()

        # 1. Generate 3 A/B Title Candidates (<100 chars each)
        t1 = self._clamp_length(f"{clean_hook_title} #Shorts", max_len=95)
        t2 = self._clamp_length(f"The 1 Huge {clean_topic} Mistake You're Making", max_len=95)
        t3 = self._clamp_length(f"Why Nobody Talks About This {clean_topic} Secret", max_len=95)

        titles = [t1, t2, t3]
        primary_title = titles[0]

        # 2. Generate 3-Layer Hashtag Pyramid (3 to 5 #-tags)
        # Layer 1: Broad viral platform tags (#Shorts, #Viral)
        # Layer 2: Category/Niche mid-tier tag (#Tech, #Finance, etc.)
        # Layer 3: Specific micro-niche / topic tag
        pyramid_tags: list[str] = ["#Shorts", "#Viral"]

        niche_tag = f"#{re.sub(r'[^a-zA-Z0-9]', '', norm_niche.title())}"
        if niche_tag and niche_tag not in pyramid_tags:
            pyramid_tags.append(niche_tag)

        topic_words = re.findall(r"[a-zA-Z0-9]+", clean_topic)
        if topic_words:
            topic_tag = f"#{''.join(topic_words[:2]).capitalize()}"
            if topic_tag and topic_tag not in pyramid_tags:
                pyramid_tags.append(topic_tag)

        if len(pyramid_tags) < 3:
            pyramid_tags.append("#Trending")
        if len(pyramid_tags) > 5:
            pyramid_tags = pyramid_tags[:5]

        # 3. Clean Search Keywords (No #, Total string <= 500 characters)
        raw_candidates = [
            clean_topic.lower(),
            norm_niche.lower(),
            f"{clean_topic.lower()} tutorial",
            f"{clean_topic.lower()} explained",
            f"best {clean_topic.lower()}",
            "shorts",
            "viral",
            "youtube shorts",
            "how to",
            "tips",
        ]
        if key_insights:
            for ins in key_insights[:3]:
                words = re.findall(r"[a-zA-Z]{4,}", ins.lower())
                if words:
                    raw_candidates.append(" ".join(words[:3]))

        clean_tags: list[str] = []
        cur_length = 0
        seen: set[str] = set()

        for c in raw_candidates:
            tag = c.strip().lstrip("#").lower()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            cost = len(tag) + (1 if clean_tags else 0)
            if cur_length + cost <= 490:  # Safety margin under 500 chars
                clean_tags.append(tag)
                cur_length += cost
            else:
                break

        category_id = NICHE_CATEGORY_MAP.get(norm_niche, "28")

        return SEOMetadata(
            primary_title=primary_title,
            titles=titles,
            description_hashtags=pyramid_tags,
            search_tags=clean_tags,
            category_id=category_id,
        )

    def _clamp_length(self, text: str, max_len: int = 95) -> str:
        s = text.strip()
        if len(s) <= max_len:
            return s
        return s[: max_len - 3] + "..."
