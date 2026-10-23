"""A/B Viral Title Variants and YouTube Shorts Metadata Builder.

Generates high-CTR YouTube Shorts title variants (<100 characters, sanitized,
with #Shorts guaranteed) across three proven psychological framing styles:
1. Curiosity Gap ('curiosity')
2. Contrarian / Truth ('contrarian')
3. Direct Viral Hook ('punchy')
"""

from __future__ import annotations

import re

from autoshorts.uploader.description_builder import (
    DEFAULT_ATTRIBUTION_DISCLAIMER,
    DEFAULT_DESCRIPTION_TEMPLATE,
    DEFAULT_TITLE_TEMPLATE,
    MAX_DESCRIPTION_BYTES,
    MAX_TITLE_LENGTH,
    DescriptionBuilder,
    build_short_description,
    build_short_title,
    format_timestamp_display,
    normalize_hashtags,
    sanitize_youtube_text,
    truncate_on_word,
)


def _format_bounded_shorts_title(core_text: str, max_len: int = 99) -> str:
    """Ensure title is sanitized, includes #Shorts, and is strictly < 100 characters."""
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", sanitize_youtube_text(core_text)).strip()
    # Remove any trailing #shorts to avoid duplication before re-appending
    if cleaned.lower().endswith("#shorts"):
        cleaned = cleaned[: -len("#shorts")].rstrip()
    if not cleaned:
        cleaned = "Short"

    suffix = " #Shorts"
    limit = min(max_len, MAX_TITLE_LENGTH - 1)  # Strictly < 100 chars (max 99)
    max_core = limit - len(suffix)

    if len(cleaned) > max_core:
        # Cut on a word boundary; the ellipsis counts toward the core budget.
        head = truncate_on_word(cleaned, max(1, max_core - 3)).rstrip(" -|:,.!?")
        cleaned = f"{head or cleaned[:1]}..."

    final = f"{cleaned}{suffix}".strip()
    return final[:limit]


def generate_title_variants(
    hook: str,
    channel: str,
    title: str = "",
) -> list[dict[str, str]]:
    """Generate 3 ready-to-use <100-char YouTube Shorts title variants.

    Returns:
        1. {"style": "curiosity", "label": "Curiosity Gap", "title": ...}
        2. {"style": "contrarian", "label": "Contrarian / Truth", "title": ...}
        3. {"style": "punchy", "label": "Direct Viral Hook", "title": ...}
    """
    clean_hook = sanitize_youtube_text(hook).strip()
    clean_channel = sanitize_youtube_text(channel).strip()
    clean_title = sanitize_youtube_text(title).strip()

    # Strip trailing #shorts from inputs if present so templates don't double-tag
    for attr_val in (clean_hook, clean_title):
        if attr_val.lower().endswith("#shorts"):
            attr_val = attr_val[: -len("#shorts")].rstrip()

    if clean_hook.lower().endswith("#shorts"):
        clean_hook = clean_hook[: -len("#shorts")].rstrip()
    if clean_title.lower().endswith("#shorts"):
        clean_title = clean_title[: -len("#shorts")].rstrip()

    base_topic = clean_hook or clean_title or "This Changes Everything"
    speaker_prefix = f"{clean_channel}: " if clean_channel else ""
    speaker_suffix = f" | {clean_channel}" if clean_channel else ""

    # 1. Curiosity Gap
    if "?" in base_topic:
        curiosity_core = f"{base_topic}{speaker_suffix}"
    else:
        curiosity_core = f"Why {base_topic}?{speaker_suffix}"

    # 2. Contrarian / Truth
    if clean_channel:
        contrarian_core = f"The Truth About {clean_title or base_topic} ({clean_channel})"
    else:
        contrarian_core = f"The Truth About {clean_title or base_topic}"

    # 3. Direct Viral Hook
    punchy_core = f"{speaker_prefix}{base_topic}"

    return [
        {
            "style": "curiosity",
            "label": "Curiosity Gap",
            "title": _format_bounded_shorts_title(curiosity_core),
        },
        {
            "style": "contrarian",
            "label": "Contrarian / Truth",
            "title": _format_bounded_shorts_title(contrarian_core),
        },
        {
            "style": "punchy",
            "label": "Direct Viral Hook",
            "title": _format_bounded_shorts_title(punchy_core),
        },
    ]


# Attach convenience method onto DescriptionBuilder as well
DescriptionBuilder.generate_title_variants = staticmethod(generate_title_variants)  # type: ignore[attr-defined]


__all__ = [
    "DEFAULT_ATTRIBUTION_DISCLAIMER",
    "DEFAULT_DESCRIPTION_TEMPLATE",
    "DEFAULT_TITLE_TEMPLATE",
    "MAX_DESCRIPTION_BYTES",
    "MAX_TITLE_LENGTH",
    "DescriptionBuilder",
    "build_short_description",
    "build_short_title",
    "format_timestamp_display",
    "generate_title_variants",
    "normalize_hashtags",
    "sanitize_youtube_text",
]
