"""Max-retention narrative script generation for long-form videos (LLM + curated fallback)."""

from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from typing import Any

from autoshorts.core.providers import get_provider
from autoshorts.longform.topics import (
    MAX_TITLE_CHARS,
    TopicSpec,
    hashtags_for,
    resolve_topic_spec,
    title_for,
)
from autoshorts.longform.types import MAX_VIDEO_SECONDS, LongformScript, Section

logger = logging.getLogger(__name__)

WORDS_PER_MINUTE = 150
WORDS_PER_SECOND = 2.6
MAX_WORDS = int(MAX_VIDEO_SECONDS * WORDS_PER_SECOND)  # 3120 words (20 min cap)
MIN_MINUTES = 1.0
MAX_MINUTES = 20.0
MIN_SECTIONS = 4
MAX_SECTIONS = 12
PROVIDERS = ("api", "local")

CURATED_TITLE_SUBJECTS = {
    "discipline": "build it in 5 daily steps",
    "money": "a simple plan that works",
    "testosterone": "what actually matters",
    "science": "what it does to your mind",
    "self_improvement": "the 5 steps that work",
    "parody": "why everyone gets it backwards",
    "history": "the turning point that changed all",
    "comparison": "the ultimate 5-stat breakdown",
    "finance": "how compounding builds wealth",
    "motivation": "rewire your habits and focus",
}

_SCIENCE_CYCLE = ("biology", "chemistry", "physics", "astrophysics", "biology")
_HISTORY_ERAS = ("480 BC", "44 BC", "476 AD", "1066 AD", "1453 AD", "1789 AD")


def count_words(text: str) -> int:
    return len(text.split())


def estimate_duration(script: LongformScript) -> float:
    """Rough spoken length in seconds."""
    return count_words(script.narration) / WORDS_PER_SECOND


def description_for(script: LongformScript) -> str:
    """YouTube description: hook, section outline, safety note and hashtags."""
    spec = resolve_topic_spec(script.topic, genre=script.genre or None, allow_custom=True)
    lines = [script.hook.strip(), ""]
    for i, s in enumerate(script.sections, 1):
        lines.append(f"{i}. {s.heading}")
    tags = ["#shorts", *(script.hashtags or hashtags_for(script.topic))[:7]]
    lines += ["", spec.safety_note, "", " ".join(tags)]
    return "\n".join(lines).strip()


def _default_comparison_data(label: str, index: int, sub_domain: str) -> dict[str, Any]:
    parts = re.split(r"\b(?:vs\.?|versus)\b", label, flags=re.IGNORECASE)
    if len(parts) >= 2:
        left_name = parts[0].strip() or "Contender A"
        right_name = parts[1].strip() or "Contender B"
    else:
        left_name = "Spartans" if "spartan" in label.lower() else "Contender A"
        right_name = "Samurai" if "samurai" in label.lower() else "Contender B"

    is_tier = sub_domain == "tier_list" or index >= 4
    return {
        "mode": "tier" if is_tier else "vs",
        "left_name": left_name[:22],
        "right_name": right_name[:22],
        "stats": [
            {"label": "Power", "left": 88, "right": 79},
            {"label": "Speed", "left": 74, "right": 93},
            {"label": "Intelligence", "left": 84, "right": 89},
            {"label": "Cost", "left": 70, "right": 82},
            {"label": "Durability", "left": 94, "right": 77},
        ],
        "tiers": {
            "S": [left_name[:16], "Adaptive Strategy"],
            "A": [right_name[:16], "Precision Timing"],
            "B": ["Heavy Armor", "High Output"],
            "C": ["Glass Cannon"],
            "D": ["Static Defense"],
            "F": ["No Plan"],
        },
    }


def _enrich_section(
    heading: str,
    narration: str,
    spec: TopicSpec,
    index: int,
    total_sections: int,
    raw_item: dict[str, Any] | None = None,
) -> Section:
    item = raw_item or {}
    genre = spec.genre
    speaker = str(item.get("speaker") or "").strip().lower()
    if not speaker:
        if genre == "parody":
            speaker = "skeptic" if index % 2 == 1 else "narrator"
        else:
            speaker = "narrator"

    sub_domain = str(item.get("sub_domain") or "").strip().lower()
    if not sub_domain:
        if genre == "science":
            if spec.sub_domain and index == 0:
                sub_domain = spec.sub_domain
            else:
                sub_domain = _SCIENCE_CYCLE[index % len(_SCIENCE_CYCLE)]
        else:
            sub_domain = spec.sub_domain

    visual_mode = str(item.get("visual_mode") or "").strip().lower()
    if not visual_mode or visual_mode == "auto":
        if genre == "parody":
            visual_mode = "parody_duet"
        elif genre == "history":
            visual_mode = "history_map" if index % 2 == 0 else "history_timeline"
        elif genre == "science":
            visual_mode = f"science_{sub_domain or 'physics'}"
        elif genre == "comparison":
            visual_mode = (
                "comparison_tier"
                if (sub_domain == "tier_list" or index == total_sections - 1)
                else "comparison_vs"
            )
        elif genre == "finance":
            visual_mode = "finance_curve"
        else:
            visual_mode = "motivation_loop" if index % 2 == 0 else "motivation_value"

    hook_callout = str(item.get("hook_callout") or "").strip()
    if not hook_callout:
        if genre == "history":
            hook_callout = f"{_HISTORY_ERAS[index % len(_HISTORY_ERAS)]} • {heading.upper()}"
        elif genre == "finance":
            hook_callout = f"COMPOUND STAGE {index + 1} • {heading.upper()}"
        elif genre == "science":
            hook_callout = f"{(sub_domain or 'SCIENCE').upper()} LAB • {heading.upper()}"
        elif genre == "comparison":
            hook_callout = f"ROUND {index + 1} • {heading.upper()}"
        elif genre == "parody":
            hook_callout = "SKEPTIC CHECK" if speaker == "skeptic" else "BOLD CLAIM"
        else:
            hook_callout = (
                "CUE -> CRAVING -> RESPONSE -> REWARD"
                if index % 2 == 0
                else "1% DAILY COMPOUNDING"
            )

    comp_data = item.get("comparison_data")
    if not isinstance(comp_data, dict):
        comp_data = (
            _default_comparison_data(spec.label, index, sub_domain)
            if genre == "comparison"
            else None
        )

    return Section(
        heading=heading,
        narration=narration,
        visual_mode=visual_mode,
        speaker=speaker,
        sub_domain=sub_domain,
        comparison_data=comp_data,
        hook_callout=hook_callout,
    )


def _curated_script(spec: TopicSpec, provider: str) -> LongformScript:
    total_pts = len(spec.curated_points)
    sections = [
        _enrich_section(h, n, spec, idx, total_pts)
        for idx, (h, n) in enumerate(spec.curated_points)
    ]
    if sections and spec.curated_hook:
        first = sections[0]
        sections[0] = Section(
            heading=first.heading,
            narration=f"{spec.curated_hook} {first.narration}",
            visual_mode=first.visual_mode,
            speaker=first.speaker,
            sub_domain=first.sub_domain,
            comparison_data=first.comparison_data,
            hook_callout=first.hook_callout,
        )
    subject = CURATED_TITLE_SUBJECTS.get(spec.key, "the complete visual breakdown")
    return LongformScript(
        topic=spec.key,
        title=title_for(spec.label if spec.key not in CURATED_TITLE_SUBJECTS else spec.key, subject),
        hook=spec.curated_hook,
        sections=sections,
        provider=provider,
        hashtags=hashtags_for(spec.label if spec.key not in CURATED_TITLE_SUBJECTS else spec.key),
        genre=spec.genre,
    )


def _build_prompt(spec: TopicSpec, minutes: float) -> str:
    words = round(minutes * WORDS_PER_MINUTE)
    target_sections = min(MAX_SECTIONS, max(MIN_SECTIONS, round(minutes * 2)))
    dialogue_rule = (
        'Multi-Character Parody Dialogue: Alternate "speaker" between "narrator" (confident '
        'know-it-all) and "skeptic" (deadpan realist) with sharp comedic punchlines.\n'
        if spec.genre == "parody"
        else 'Use "speaker": "narrator" across all sections for a consistent authoritative voice.\n'
    )
    return (
        f"You write high-retention narration for an animated YouTube video about {spec.label} "
        f"(genre: {spec.genre}).\n"
        f"Target length: about {words} words of narration in total (never more than "
        f"{MAX_WORDS}).\n"
        "Retention architecture you MUST enforce:\n"
        "1. 0-15s High-Stakes Cold Open Hook: Open with a pattern interrupt, counter-intuitive "
        "paradox, and visual tease in the very first sentence.\n"
        "2. Open-Loop Chapter Chaining: Every section must resolve the prior curiosity gap and "
        "plant a new open loop at the end of its narration so viewers watch to 100% completion.\n"
        f"3. {dialogue_rule}"
        "Return ONLY valid JSON with these keys:\n"
        f'- "title": at most {MAX_TITLE_CHARS} characters, starts with a heavily searched '
        f"keyword such as {', '.join(spec.title_keywords[:3])}\n"
        '- "hook": one high-stakes pattern-interrupt sentence that opens the video (0-15s cold open)\n'
        f'- "sections": a list of {MIN_SECTIONS} to {max(6, target_sections)} objects with '
        '"heading" (2 to 5 words), "narration" (spoken text ending with an open loop), and '
        'optional "speaker" ("narrator" or "skeptic"), "visual_mode", "sub_domain", "hook_callout"\n'
        "Style: plain, direct, vivid language. No filler, no emojis, no hashtags, no stage "
        "directions. No promises of results.\n"
        f"Safety rules you must follow: {spec.safety_note}\n"
        "Never mention steroids, supplement doses, guaranteed income or specific "
        "investments."
    )


def _parse_json(raw: str) -> Any:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0].strip()
    return json.loads(s)


def _validate_llm(data: Any, spec: TopicSpec, minutes: float, provider: str) -> LongformScript:
    """Turn parsed LLM output into a script or raise ValueError."""
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")
    hook = str(data.get("hook") or "").strip()
    raw_sections = data.get("sections")
    if not hook:
        raise ValueError("missing hook")
    if not isinstance(raw_sections, list) or not (
        MIN_SECTIONS <= len(raw_sections) <= MAX_SECTIONS
    ):
        raise ValueError(f"sections must be a list of {MIN_SECTIONS} to {MAX_SECTIONS} items")
    sections: list[Section] = []
    total_sec = len(raw_sections)
    for idx, item in enumerate(raw_sections):
        if not isinstance(item, dict):
            raise ValueError("section is not an object")
        heading = str(item.get("heading") or "").strip()
        narration = str(item.get("narration") or "").strip()
        if not heading or not narration:
            raise ValueError("empty heading or narration")
        sections.append(_enrich_section(heading, narration, spec, idx, total_sec, item))
    if not sections[0].narration.lower().startswith(hook.lower()[:30]):
        first = sections[0]
        sections[0] = Section(
            heading=first.heading,
            narration=f"{hook} {first.narration}",
            visual_mode=first.visual_mode,
            speaker=first.speaker,
            sub_domain=first.sub_domain,
            comparison_data=first.comparison_data,
            hook_callout=first.hook_callout,
        )
    total = sum(count_words(s.narration) for s in sections)
    min_words = max(60, int(minutes * WORDS_PER_MINUTE * 0.5))
    max_words_for_request = min(MAX_WORDS, max(624, int(minutes * WORDS_PER_MINUTE * 1.8)))
    if total > max_words_for_request or total < min_words:
        raise ValueError(f"word total {total} outside {min_words}..{max_words_for_request}")
    title = str(data.get("title") or "").strip()
    if not title or len(title) > MAX_TITLE_CHARS:
        title = title_for(spec.label, title)
    return LongformScript(
        topic=spec.key,
        title=title,
        hook=hook,
        sections=sections,
        provider=provider,
        hashtags=hashtags_for(spec.label),
        genre=spec.genre,
    )


def build_script(
    topic: str,
    minutes: float = 2.5,
    provider: str = "api",
    config: Any = None,
    rng: random.Random | None = None,
    *,
    genre: str | None = None,
    allow_custom: bool = True,
) -> LongformScript:
    """Build a long-form script. Falls back to the curated script on any LLM problem."""
    spec = resolve_topic_spec(topic, genre=genre, allow_custom=allow_custom)
    if not isinstance(minutes, (int, float)) or not (MIN_MINUTES <= minutes <= MAX_MINUTES):
        raise ValueError(f"minutes must be between {MIN_MINUTES:g} and {MAX_MINUTES:g}")
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    curated = _curated_script(spec, provider)

    if config is None:
        return curated
    if provider == "local":
        model_path = str(getattr(getattr(config, "llm", None), "local_model_path", "") or "")
        if not model_path or not Path(model_path).exists():
            logger.warning(
                "Local model not found (%s); using curated %s script.", model_path, spec.key
            )
            return curated

    try:
        name = "gemini" if provider == "api" else "local"
        llm = get_provider(name=name, settings=config)
        raw = llm.complete(_build_prompt(spec, float(minutes)))
        return _validate_llm(_parse_json(raw), spec, float(minutes), provider)
    except Exception as exc:
        logger.warning("LLM script for %s unusable (%s); using curated script.", spec.key, exc)
        return curated


def clean_text(text: str) -> str:
    """Collapse whitespace; helper for callers that display narration."""
    return re.sub(r"\s+", " ", text).strip()
