"""Script generation for long-form videos (LLM with a curated, always-valid fallback)."""

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
    get_topic,
    hashtags_for,
    normalize_topic,
    title_for,
)
from autoshorts.longform.types import MAX_VIDEO_SECONDS, LongformScript, Section

logger = logging.getLogger(__name__)

WORDS_PER_MINUTE = 150
WORDS_PER_SECOND = 2.6
MAX_WORDS = int(MAX_VIDEO_SECONDS * WORDS_PER_SECOND)  # 624
MIN_MINUTES = 1.0
MAX_MINUTES = 4.0
MIN_SECTIONS = 4
MAX_SECTIONS = 6
PROVIDERS = ("api", "local")


def count_words(text: str) -> int:
    return len(text.split())


def estimate_duration(script: LongformScript) -> float:
    """Rough spoken length in seconds."""
    return count_words(script.narration) / WORDS_PER_SECOND


def description_for(script: LongformScript) -> str:
    """YouTube description: hook, section outline, safety note and hashtags."""
    spec = get_topic(script.topic)
    lines = [script.hook.strip(), ""]
    for i, s in enumerate(script.sections, 1):
        lines.append(f"{i}. {s.heading}")
    lines += ["", spec.safety_note, "", " ".join(script.hashtags or hashtags_for(script.topic))]
    return "\n".join(lines).strip()


def _curated_script(spec: TopicSpec, provider: str) -> LongformScript:
    sections = [Section(h, n) for h, n in spec.curated_points]
    if sections and spec.curated_hook:
        sections[0] = Section(sections[0].heading, f"{spec.curated_hook} {sections[0].narration}")
    return LongformScript(
        topic=spec.key,
        title=title_for(spec.key, CURATED_TITLE_SUBJECTS.get(spec.key, "")),
        hook=spec.curated_hook,
        sections=sections,
        provider=provider,
        hashtags=hashtags_for(spec.key),
    )


def _build_prompt(spec: TopicSpec, minutes: float) -> str:
    words = round(minutes * WORDS_PER_MINUTE)
    return (
        f"You write narration for a faceless YouTube video about {spec.label}.\n"
        f"Target length: about {words} words of narration in total (never more than "
        f"{MAX_WORDS}).\n"
        "Return ONLY valid JSON with these keys:\n"
        f'- "title": at most {MAX_TITLE_CHARS} characters, starts with a heavily searched '
        f"keyword such as {', '.join(spec.title_keywords[:3])}\n"
        '- "hook": one sentence that opens the video\n'
        f'- "sections": a list of {MIN_SECTIONS} to {MAX_SECTIONS} objects with '
        '"heading" (2 to 5 words) and "narration" (spoken text)\n'
        "Style: plain, direct language. No filler, no emojis, no hashtags, no stage "
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
        raise ValueError("sections must be a list of 4 to 6 items")
    sections: list[Section] = []
    for item in raw_sections:
        if not isinstance(item, dict):
            raise ValueError("section is not an object")
        heading = str(item.get("heading") or "").strip()
        narration = str(item.get("narration") or "").strip()
        if not heading or not narration:
            raise ValueError("empty heading or narration")
        sections.append(Section(heading, narration))
    if not sections[0].narration.lower().startswith(hook.lower()[:30]):
        sections[0] = Section(sections[0].heading, f"{hook} {sections[0].narration}")
    total = sum(count_words(s.narration) for s in sections)
    min_words = max(60, int(minutes * WORDS_PER_MINUTE * 0.5))
    if total > MAX_WORDS or total < min_words:
        raise ValueError(f"word total {total} outside {min_words}..{MAX_WORDS}")
    title = str(data.get("title") or "").strip()
    if not title or len(title) > MAX_TITLE_CHARS:
        title = title_for(spec.key, title)
    return LongformScript(
        topic=spec.key,
        title=title,
        hook=hook,
        sections=sections,
        provider=provider,
        hashtags=hashtags_for(spec.key),
    )


# Short subject phrases for curated titles, so titles read like searchable video titles.
CURATED_TITLE_SUBJECTS = {
    "discipline": "build it in 5 daily steps",
    "money": "a simple plan that works",
    "testosterone": "what actually matters",
    "science": "what it does to your mind",
    "self_improvement": "the 5 steps that work",
}


def build_script(
    topic: str,
    minutes: float = 2.5,
    provider: str = "api",
    config: Any = None,
    rng: random.Random | None = None,
) -> LongformScript:
    """Build a long-form script. Falls back to the curated script on any LLM problem."""
    key = normalize_topic(topic)
    if not isinstance(minutes, (int, float)) or not (MIN_MINUTES <= minutes <= MAX_MINUTES):
        raise ValueError(f"minutes must be between {MIN_MINUTES:g} and {MAX_MINUTES:g}")
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    spec = get_topic(key)
    curated = _curated_script(spec, provider)

    if config is None:
        return curated
    if provider == "local":
        model_path = str(getattr(getattr(config, "llm", None), "local_model_path", "") or "")
        if not model_path or not Path(model_path).exists():
            logger.warning("Local model not found (%s); using curated %s script.", model_path, key)
            return curated

    try:
        name = "gemini" if provider == "api" else "local"
        llm = get_provider(name=name, settings=config)
        raw = llm.complete(_build_prompt(spec, float(minutes)))
        return _validate_llm(_parse_json(raw), spec, float(minutes), provider)
    except Exception as exc:
        logger.warning("LLM script for %s unusable (%s); using curated script.", key, exc)
        return curated


def clean_text(text: str) -> str:
    """Collapse whitespace; helper for callers that display narration."""
    return re.sub(r"\s+", " ", text).strip()
