"""Wojak long-form cultural parody script generator in the style of SapX.

Enforces:
- 16:9 Landscape (1920x1080) format
- 2.5 to 3.0 minutes total duration (150.0 to 180.0 seconds)
- 25 to 35 countries per video
- Exactly 4.0 to 4.5 seconds per country segment
- Mandatory 3.5s Parody Disclaimer Card at the beginning
- Relatable, sharp, viral cultural satire punchlines
- Complete procedural generation fallback + optional LLM (Gemini) support
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from autoshorts.longform.wojak_topics import (
    ASPECT_RATIO,
    DEFAULT_COUNTRIES_PER_VIDEO,
    DEFAULT_COUNTRY_DURATION,
    DISCLAIMER_DURATION_SECONDS,
    LANDSCAPE_HEIGHT,
    LANDSCAPE_WIDTH,
    MANDATORY_DISCLAIMER_BODY,
    MANDATORY_DISCLAIMER_HEADING,
    MAX_COUNTRIES_PER_VIDEO,
    MAX_COUNTRY_DURATION,
    MAX_PUNCHLINE_CHARS,
    MAX_TOTAL_DURATION,
    MIN_COUNTRIES_PER_VIDEO,
    MIN_COUNTRY_DURATION,
    MIN_TOTAL_DURATION,
    TOPIC_HASHTAGS,
    TOPIC_TITLES,
    generate_procedural_joke,
    get_country_meta,
    get_curated_topic_data,
    list_available_countries,
    normalize_wojak_topic,
)

logger = logging.getLogger(__name__)

# Valid character context domains matching character_composer.py and wojak_generator.py
VALID_EXPRESSIONS = (
    "neutral",
    "smug",
    "annoyed",
    "tired",
    "sad",
    "happy",
    "shocked",
    "crying",
    "npc",
    "chad",
    "doomer",
    "bandaged",
)
VALID_PROPS = (
    "none",
    "bill",
    "phone",
    "tea_cup",
    "coffee_cup",
    "pasta_pot",
    "pastry",
    "ball",
    "bandage",
    "clipboard",
    "pennant",
    "bag",
    "sign",
    "stethoscope",
    "card",
)
VALID_ATTIRES = ("shirt", "lab_coat", "jacket", "jersey", "coat", "gown")
VALID_GENDERS = ("male", "female", "androgynous")
VALID_SKIN_TONES = ("pale", "light", "fair", "tan", "brown", "dark")


@dataclass
class WojakSegment:
    """One country segment in a Wojak longform video."""

    country: str
    country_code: str
    heading: str
    joke_text: str
    setting: str
    character_context: dict[str, Any] = field(default_factory=dict)
    duration: float = DEFAULT_COUNTRY_DURATION

    def __post_init__(self) -> None:
        # Guarantee duration bounds strictly
        if not (MIN_COUNTRY_DURATION <= self.duration <= MAX_COUNTRY_DURATION):
            # Clamp or round into legal range
            self.duration = max(
                MIN_COUNTRY_DURATION, min(MAX_COUNTRY_DURATION, float(self.duration))
            )

        # Normalize character context dictionary
        ctx = dict(self.character_context or {})
        expr = str(ctx.get("expression") or "neutral").lower()
        if expr not in VALID_EXPRESSIONS:
            expr = "neutral"
        ctx["expression"] = expr

        prop = str(ctx.get("prop") or "none").lower()
        if prop not in VALID_PROPS:
            prop = "none"
        ctx["prop"] = prop

        attire = str(ctx.get("attire") or "shirt").lower()
        if attire not in VALID_ATTIRES:
            attire = "shirt"
        ctx["attire"] = attire

        gender = str(ctx.get("gender") or "male").lower()
        if gender not in VALID_GENDERS:
            gender = "male"
        ctx["gender"] = gender

        race = str(ctx.get("race") or ctx.get("skin_tone") or "fair").lower()
        if race not in VALID_SKIN_TONES:
            race = "fair"
        ctx["race"] = race
        ctx["skin_tone"] = race

        if "age" not in ctx or not isinstance(ctx["age"], int):
            ctx["age"] = 32

        # Enforce punchline brevity (<120 characters) for Wojak Explainer pacing
        if len(self.joke_text) >= MAX_PUNCHLINE_CHARS:
            truncated = self.joke_text[: MAX_PUNCHLINE_CHARS - 1].rsplit(" ", 1)[0]
            self.joke_text = truncated if truncated else self.joke_text[: MAX_PUNCHLINE_CHARS - 1]

        self.character_context = ctx

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WojakSegment:
        return cls(
            country=str(data.get("country") or "Country"),
            country_code=str(data.get("country_code") or "US").upper(),
            heading=str(data.get("heading") or data.get("country") or "Country"),
            joke_text=str(data.get("joke_text") or "").strip(),
            setting=str(data.get("setting") or "city street").strip(),
            character_context=dict(data.get("character_context") or {}),
            duration=float(data.get("duration", DEFAULT_COUNTRY_DURATION)),
        )

    def to_character_composer_context(self) -> dict[str, Any]:
        """Convert into a context dictionary directly usable by character_composer.spec_from_context."""
        return {
            "country": self.country,
            "setting": self.setting,
            "emotion": self.character_context.get("expression", "neutral"),
            "line": self.joke_text,
            "prop": self.character_context.get("prop", "none"),
            "attire": self.character_context.get("attire", "shirt"),
            "torso": self.character_context.get("attire", "shirt"),
            "gender": self.character_context.get("gender", "male"),
            "skin_tone": self.character_context.get("race", "fair"),
            "keywords": [self.character_context.get("prop", "none")],
        }


@dataclass
class WojakLongformScript:
    """Full-length Wojak cultural parody script (16:9, 2.5 to 3.0 minutes)."""

    topic: str
    title: str
    disclaimer_text: str = MANDATORY_DISCLAIMER_BODY
    disclaimer_heading: str = MANDATORY_DISCLAIMER_HEADING
    disclaimer_duration: float = DISCLAIMER_DURATION_SECONDS
    segments: list[WojakSegment] = field(default_factory=list)
    total_duration: float = 0.0
    hashtags: list[str] = field(default_factory=list)
    format: str = ASPECT_RATIO
    width: int = LANDSCAPE_WIDTH
    height: int = LANDSCAPE_HEIGHT
    provider: str = "procedural"

    def __post_init__(self) -> None:
        self.recalculate_duration()

    def recalculate_duration(self) -> float:
        """Compute exact duration: disclaimer card + sum of all country segments."""
        seg_duration = sum(s.duration for s in self.segments)
        self.total_duration = round(self.disclaimer_duration + seg_duration, 2)
        return self.total_duration

    def validate_pacing(self, strict: bool = True) -> list[str]:
        """Validate script constraints against the SapX viral longform requirements.

        Returns a list of error strings. If strict=True, raises ValueError on any violation.
        """
        errors: list[str] = []

        # 1. Video duration range (2.5 to 3.0 minutes = 150.0 to 180.0s)
        self.recalculate_duration()
        if not (MIN_TOTAL_DURATION - 0.5 <= self.total_duration <= MAX_TOTAL_DURATION + 0.5):
            errors.append(
                f"Total video duration {self.total_duration:.1f}s is outside target range "
                f"[{MIN_TOTAL_DURATION:.1f}s, {MAX_TOTAL_DURATION:.1f}s] (2.5 to 3.0 minutes)."
            )

        # 2. Number of countries (25 to 35)
        num_seg = len(self.segments)
        if not (MIN_COUNTRIES_PER_VIDEO <= num_seg <= MAX_COUNTRIES_PER_VIDEO):
            errors.append(
                f"Segment count ({num_seg}) is outside requirement [{MIN_COUNTRIES_PER_VIDEO}, {MAX_COUNTRIES_PER_VIDEO}]."
            )

        # 3. Pacing per country (4.0 to 4.5s)
        for idx, seg in enumerate(self.segments):
            if not (MIN_COUNTRY_DURATION - 0.01 <= seg.duration <= MAX_COUNTRY_DURATION + 0.01):
                errors.append(
                    f"Segment {idx + 1} ({seg.country}) duration {seg.duration:.2f}s is outside [4.0s, 4.5s]."
                )

        # 4. Mandatory Disclaimer check
        if self.disclaimer_heading != MANDATORY_DISCLAIMER_HEADING:
            errors.append(
                f"Disclaimer heading mismatch. Expected '{MANDATORY_DISCLAIMER_HEADING}', got '{self.disclaimer_heading}'."
            )
        if (
            MANDATORY_DISCLAIMER_BODY not in self.disclaimer_text
            and self.disclaimer_text != MANDATORY_DISCLAIMER_BODY
        ):
            errors.append("Mandatory disclaimer text body is missing or altered.")
        if abs(self.disclaimer_duration - DISCLAIMER_DURATION_SECONDS) > 0.01:
            errors.append(
                f"Disclaimer duration must be exactly {DISCLAIMER_DURATION_SECONDS}s, got {self.disclaimer_duration}s."
            )

        # 5. Aspect ratio & dimensions
        if self.width != LANDSCAPE_WIDTH or self.height != LANDSCAPE_HEIGHT:
            errors.append(f"Format must be 1920x1080 landscape, got {self.width}x{self.height}.")

        # 6. Punchline brevity (<120 characters)
        for idx, seg in enumerate(self.segments):
            if len(seg.joke_text) >= MAX_PUNCHLINE_CHARS:
                errors.append(
                    f"Segment {idx + 1} ({seg.country}) joke length ({len(seg.joke_text)} chars) "
                    f"exceeds brevity limit of {MAX_PUNCHLINE_CHARS} characters."
                )

        if strict and errors:
            raise ValueError("WojakLongformScript validation failed:\n- " + "\n- ".join(errors))
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "title": self.title,
            "disclaimer_heading": self.disclaimer_heading,
            "disclaimer_text": self.disclaimer_text,
            "disclaimer_duration": self.disclaimer_duration,
            "segments": [s.to_dict() for s in self.segments],
            "total_duration": self.total_duration,
            "hashtags": list(self.hashtags),
            "format": self.format,
            "width": self.width,
            "height": self.height,
            "provider": self.provider,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WojakLongformScript:
        raw_segments = data.get("segments") or []
        segments = [WojakSegment.from_dict(s) for s in raw_segments]
        return cls(
            topic=str(data.get("topic") or "hospital"),
            title=str(data.get("title") or "Every Country Be Like"),
            disclaimer_text=str(data.get("disclaimer_text") or MANDATORY_DISCLAIMER_BODY),
            disclaimer_heading=str(data.get("disclaimer_heading") or MANDATORY_DISCLAIMER_HEADING),
            disclaimer_duration=float(data.get("disclaimer_duration", DISCLAIMER_DURATION_SECONDS)),
            segments=segments,
            total_duration=float(data.get("total_duration", 0.0)),
            hashtags=list(data.get("hashtags") or []),
            format=str(data.get("format") or ASPECT_RATIO),
            width=int(data.get("width", LANDSCAPE_WIDTH)),
            height=int(data.get("height", LANDSCAPE_HEIGHT)),
            provider=str(data.get("provider") or "procedural"),
        )

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> WojakLongformScript:
        return cls.from_dict(json.loads(json_str))

    def summary(self) -> str:
        lines = [
            f"=== {self.title} ===",
            f"Topic: {self.topic} | Format: {self.format} ({self.width}x{self.height})",
            f"Total Duration: {self.total_duration:.1f}s ({self.total_duration / 60:.2f} min)",
            f"Disclaimer: {self.disclaimer_heading} ({self.disclaimer_duration}s)",
            f"Country Segments: {len(self.segments)}",
            "-" * 40,
        ]
        for i, s in enumerate(self.segments[:5], 1):
            lines.append(f"{i:02d}. [{s.heading} - {s.duration:.1f}s] {s.joke_text}")
        if len(self.segments) > 5:
            lines.append(f"... and {len(self.segments) - 5} more country segments.")
        lines.append(f"Hashtags: {' '.join(self.hashtags)}")
        return "\n".join(lines)


# ==============================================================================
# Procedural Script Generator
# ==============================================================================


def procedural_wojak_script(
    topic: str,
    countries_count: int = DEFAULT_COUNTRIES_PER_VIDEO,
    segment_duration: float = DEFAULT_COUNTRY_DURATION,
    selected_countries: list[str] | None = None,
    seed: int | None = None,
) -> WojakLongformScript:
    """Build a procedural Wojak longform script using curated catalogs or the procedural joke engine.

    Guarantees:
    - 25 to 35 countries
    - exactly 4.0 to 4.5 seconds per segment
    - 2.5 to 3.0 minutes total duration (150s to 180s)
    - 100% offline and deterministic when seed is provided
    """
    canonical_key = normalize_wojak_topic(topic)
    curated_data = get_curated_topic_data(canonical_key)
    rng = random.Random(seed if seed is not None else 42)

    # Validate and clamp count to 25..35
    count = max(MIN_COUNTRIES_PER_VIDEO, min(MAX_COUNTRIES_PER_VIDEO, int(countries_count)))
    seg_dur = max(MIN_COUNTRY_DURATION, min(MAX_COUNTRY_DURATION, float(segment_duration)))

    # Verify if total duration lands in [150.0, 180.0]
    total_sec = DISCLAIMER_DURATION_SECONDS + count * seg_dur
    if total_sec < MIN_TOTAL_DURATION:
        # If too short, increase count or segment duration
        if seg_dur < MAX_COUNTRY_DURATION:
            seg_dur = MAX_COUNTRY_DURATION
            total_sec = DISCLAIMER_DURATION_SECONDS + count * seg_dur
        if total_sec < MIN_TOTAL_DURATION:
            # Increase count up to 35
            count = min(
                MAX_COUNTRIES_PER_VIDEO,
                int((MIN_TOTAL_DURATION - DISCLAIMER_DURATION_SECONDS) // seg_dur) + 1,
            )
            total_sec = DISCLAIMER_DURATION_SECONDS + count * seg_dur
    elif total_sec > MAX_TOTAL_DURATION:
        # If too long, decrease segment duration or count
        seg_dur = MIN_COUNTRY_DURATION
        total_sec = DISCLAIMER_DURATION_SECONDS + count * seg_dur
        if total_sec > MAX_TOTAL_DURATION:
            count = max(
                MIN_COUNTRIES_PER_VIDEO,
                int((MAX_TOTAL_DURATION - DISCLAIMER_DURATION_SECONDS) // seg_dur),
            )

    # Resolve candidate country list
    if selected_countries:
        chosen_country_keys = list(selected_countries)
    elif curated_data:
        # Prefer curated list order
        chosen_country_keys = list(curated_data.keys())
    else:
        chosen_country_keys = list_available_countries()

    # If curated data has enough countries, pick from it
    if len(chosen_country_keys) >= count:
        picked_countries = chosen_country_keys[:count]
    else:
        # Fill remaining with available countries
        all_countries = list_available_countries()
        needed = count - len(chosen_country_keys)
        remaining = [c for c in all_countries if c not in chosen_country_keys]
        picked_countries = chosen_country_keys + remaining[:needed]

    segments: list[WojakSegment] = []
    for c_key in picked_countries:
        cm = get_country_meta(c_key)
        if curated_data and c_key in curated_data:
            entry = curated_data[c_key]
            joke = entry["joke_text"]
            setting = entry.get("setting", f"{cm.heading} setting")
            char_ctx = dict(entry.get("character", {}))
        else:
            # Procedural generation
            proc = generate_procedural_joke(topic, cm.heading, seed=rng.randint(1, 1000000))
            joke = proc["joke_text"]
            setting = proc["setting"]
            char_ctx = proc["character"]

        seg = WojakSegment(
            country=cm.name,
            country_code=cm.country_code,
            heading=cm.heading,
            joke_text=joke,
            setting=setting,
            character_context=char_ctx,
            duration=seg_dur,
        )
        segments.append(seg)

    title = TOPIC_TITLES.get(
        canonical_key, f"Every Country's {topic.replace('_', ' ').title()} Be Like"
    )
    hashtags = TOPIC_HASHTAGS.get(
        canonical_key,
        ["#wojak", "#memes", "#everycountrybelike", "#culturalmemes", "#funny", "#parody"],
    )

    script = WojakLongformScript(
        topic=canonical_key,
        title=f"{title} (Wojak Cultural Parody)",
        disclaimer_text=MANDATORY_DISCLAIMER_BODY,
        disclaimer_heading=MANDATORY_DISCLAIMER_HEADING,
        disclaimer_duration=DISCLAIMER_DURATION_SECONDS,
        segments=segments,
        hashtags=hashtags,
        provider="procedural",
    )
    script.validate_pacing(strict=True)
    return script


# ==============================================================================
# LLM Script Generator with Resilient Procedural Fallback
# ==============================================================================


def _build_llm_prompt(topic: str, countries: list[str], count: int, segment_duration: float) -> str:
    return f"""You are the head comedy writer for viral Wojak cultural parody videos in the style of SapX.
Create a high-retention, hilarious parody script for the topic: "Every Country's {topic.replace("_", " ").title()} Be Like".

CRITICAL TIMING & PACING CONSTRAINTS:
1. Video format: 16:9 Landscape (1920x1080).
2. Number of countries: exactly {count} countries (use these candidates: {", ".join(countries[:count])}).
3. Duration per country: exactly {segment_duration:.1f} seconds.
4. Total duration must be between 150.0 and 180.0 seconds (2.5 to 3.0 minutes), including the 3.5s Parody Disclaimer.
5. Punchline Brevity: Every joke MUST be strictly under 120 characters (<120 chars, max 20-25 words), culturally specific, satirical, and fast to read in 4.0-4.5s.

VIRAL SAPX STYLE EXAMPLES (Hospital Topic):
- USA: "Hospital looks like a 5-star hotel. Price is also 5-star. $38 for one aspirin."
- UK: "Lying on a trolley in the corridor for 14 hours. Still says 'lovely nurses.'"
- Sweden: "Free and fair. You're number 112 in the phone queue. Fika while you wait."
- India: "World-class surgery for 10x less than in the US. Americans fly in with a list."
- South Korea: "Came in for a cold. Left with a new jawline. Paid with a coupon."
- Germany: "Please sign 42 compliance forms in triplicate before suffering from appendicitis."
- Netherlands: "Doctor prescribes 500mg paracetamol and cycling for a shattered femur."
- Italy: "Hospital food is bad, so the whole family brings lasagna. To the ICU."

Return ONLY valid JSON matching this schema:
{{
  "title": "Every Country's {topic.replace("_", " ").title()} Be Like (Wojak Cultural Parody)",
  "disclaimer_heading": "{MANDATORY_DISCLAIMER_HEADING}",
  "disclaimer_text": "{MANDATORY_DISCLAIMER_BODY}",
  "disclaimer_duration": {DISCLAIMER_DURATION_SECONDS},
  "hashtags": ["#wojak", "#memes", "#everycountrybelike", "#culturalmemes", "#satire"],
  "segments": [
    {{
      "country": "United States",
      "country_code": "US",
      "heading": "USA",
      "joke_text": "Hospital looks like a 5-star hotel. Price is also 5-star. $38 for one aspirin.",
      "setting": "luxury hospital lobby with billing desk",
      "character_context": {{
        "age": 32,
        "gender": "male",
        "race": "fair",
        "expression": "shocked",
        "prop": "bill",
        "attire": "jacket"
      }},
      "duration": {segment_duration:.1f}
    }}
  ]
}}
Allowed values for character_context:
- expression: neutral, smug, annoyed, tired, sad, happy, shocked, crying, npc, chad, doomer, bandaged
- prop: none, bill, phone, tea_cup, coffee_cup, pasta_pot, pastry, ball, bandage, clipboard, pennant, bag, sign, stethoscope
- attire: shirt, lab_coat, jacket, jersey, coat, gown
- gender: male, female, androgynous
- race: pale, light, fair, tan, brown, dark
"""


def _parse_llm_json(raw: str) -> dict[str, Any]:
    s = (raw or "").strip()
    if "```json" in s:
        s = s.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in s:
        s = s.split("```", 1)[1].split("```", 1)[0].strip()
    return json.loads(s)


def _validate_llm_wojak(
    data: Any, topic: str, expected_count: int, seg_dur: float
) -> WojakLongformScript:
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")

    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list) or len(raw_segments) < MIN_COUNTRIES_PER_VIDEO:
        raise ValueError(f"segments list must contain at least {MIN_COUNTRIES_PER_VIDEO} items")

    parsed_segments: list[WojakSegment] = []
    for item in raw_segments[:MAX_COUNTRIES_PER_VIDEO]:
        if not isinstance(item, dict):
            continue
        joke = str(item.get("joke_text") or "").strip()
        country_name = str(item.get("country") or item.get("heading") or "").strip()
        if not joke or not country_name:
            continue
        cm = get_country_meta(country_name)
        char_ctx = dict(item.get("character_context") or {})
        parsed_segments.append(
            WojakSegment(
                country=cm.name,
                country_code=cm.country_code,
                heading=str(item.get("heading") or cm.heading),
                joke_text=joke,
                setting=str(item.get("setting") or f"{cm.heading} setting"),
                character_context=char_ctx,
                duration=seg_dur,
            )
        )

    if len(parsed_segments) < MIN_COUNTRIES_PER_VIDEO:
        raise ValueError(
            f"Only {len(parsed_segments)} valid country segments parsed (minimum {MIN_COUNTRIES_PER_VIDEO} required)."
        )

    title = str(data.get("title") or f"Every Country's {topic.title()} Be Like")
    hashtags = list(data.get("hashtags") or ["#wojak", "#memes", "#everycountrybelike"])

    script = WojakLongformScript(
        topic=normalize_wojak_topic(topic),
        title=title,
        disclaimer_text=MANDATORY_DISCLAIMER_BODY,
        disclaimer_heading=MANDATORY_DISCLAIMER_HEADING,
        disclaimer_duration=DISCLAIMER_DURATION_SECONDS,
        segments=parsed_segments,
        hashtags=hashtags,
        provider="llm",
    )
    script.validate_pacing(strict=True)
    return script


def build_wojak_script(
    topic: str,
    countries_count: int = DEFAULT_COUNTRIES_PER_VIDEO,
    segment_duration: float = DEFAULT_COUNTRY_DURATION,
    provider: str = "api",
    config: Any = None,
    selected_countries: list[str] | None = None,
    seed: int | None = None,
) -> WojakLongformScript:
    """Build a complete Wojak longform video script with LLM generation and 100% procedural fallback.

    Args:
        topic: Topic name or canonical key (e.g. "hospital", "Every Country's Public Transport Be Like").
        countries_count: Number of country segments (25 to 35, default 33).
        segment_duration: Duration per country segment in seconds (4.0 to 4.5, default 4.5).
        provider: "api" (Gemini/OpenRouter) or "procedural" / "local".
        config: Optional Settings configuration for LLM credentials.
        selected_countries: Optional list of countries to include.
        seed: Random seed for deterministic procedural generation.

    Returns:
        WojakLongformScript ready for synthesis and rendering.
    """
    # 1. Always generate the procedural script first as the bedrock fallback
    procedural_fallback = procedural_wojak_script(
        topic=topic,
        countries_count=countries_count,
        segment_duration=segment_duration,
        selected_countries=selected_countries,
        seed=seed,
    )

    if provider in ("procedural", "local") or config is None:
        return procedural_fallback

    # 2. Attempt LLM generation
    try:
        from autoshorts.core.providers import get_provider

        llm_name = "gemini" if provider == "api" else str(provider)
        llm = get_provider(name=llm_name, settings=config)

        candidate_countries = (
            selected_countries
            if selected_countries
            else [s.heading for s in procedural_fallback.segments]
        )
        prompt = _build_llm_prompt(
            topic=topic,
            countries=candidate_countries,
            count=len(procedural_fallback.segments),
            segment_duration=segment_duration,
        )
        raw_output = llm.complete(prompt, max_tokens=4096)
        parsed_json = _parse_llm_json(raw_output)
        return _validate_llm_wojak(
            data=parsed_json,
            topic=topic,
            expected_count=len(procedural_fallback.segments),
            seg_dur=segment_duration,
        )
    except Exception as exc:
        logger.warning(
            "LLM Wojak script generation failed (%s); using curated procedural script for '%s'.",
            exc,
            topic,
        )
        return procedural_fallback
