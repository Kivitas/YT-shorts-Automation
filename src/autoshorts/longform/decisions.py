"""Scene decisions for long-form sections: backdrop theme, caption preset, sprite placement.

``decide_scene`` takes one narration section and returns a :class:`SceneDecision`. The
theme follows the keyword rules in ``beats`` when they can be imported, otherwise a small
local table. Consecutive sections of one job never share a theme. Every value is recorded
with ``record_decision`` under the names ``scene_theme`` and ``scene_caption``.

``check_decisions`` reviews a list of decisions and returns human-readable problems, logging
each one at WARNING. Nothing in the pipeline calls this module yet.

``decide_scene`` never raises. Empty narration gives source ``default`` with reason
``empty narration``.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import cast

from autoshorts.decider.records import DecisionRecord, DecisionSource, record_decision
from autoshorts.decider.style import DEFAULT_LAYOUT_MODE

logger = logging.getLogger(__name__)

DEFAULT_CAPTION_PRESET = "kurz"
DEFAULT_THEME = "navy_night"
SPRITE_PLACEMENT_MODE = "auto"
EMPTY_NARRATION_REASON = "empty narration"

REQUIRED_REASONS: tuple[str, ...] = (
    "theme",
    "layout_mode",
    "caption_preset",
    "sprite_placement_mode",
)

_FALLBACK_CYCLE: tuple[str, ...] = (
    "forest_dawn",
    "office_warm",
    "lab_teal",
    "city_neon",
    "candy",
    "sunset",
    DEFAULT_THEME,
)
# Used only when ``autoshorts.longform.beats`` cannot be imported.
_FALLBACK_KEYWORD_THEMES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"night", "sleep", "bed", "dark"}), "navy_night"),
    (frozenset({"work", "desk", "office", "email", "meeting"}), "office_warm"),
    (frozenset({"study", "lab", "science", "research", "data"}), "lab_teal"),
    (frozenset({"city", "phone", "online"}), "city_neon"),
    (frozenset({"morning", "sun", "wake", "dawn"}), "forest_dawn"),
    (frozenset({"win", "celebrate", "fun", "happy"}), "candy"),
)

_WORD_RE = re.compile(r"[a-z]+")
_MEMORY_LIMIT = 64

_lock = threading.Lock()
# job_id -> (section_index, theme) of the last decided section, used to avoid repeats.
_last_theme: OrderedDict[str | None, tuple[int, str]] = OrderedDict()


@dataclass
class SceneDecision:
    """Resolved scene settings for one section, with the reason for each value."""

    theme: str
    layout_mode: str
    caption_preset: str
    sprite_placement_mode: str = SPRITE_PLACEMENT_MODE
    reasons: dict[str, str] = field(default_factory=dict)


def _record(job_id: str | None, name: str, value: str, reason: str, source: str) -> None:
    try:
        record_decision(
            DecisionRecord(
                job_id=job_id,
                name=name,
                value=value,
                reason=reason,
                source=cast(DecisionSource, source),
            )
        )
    except Exception as err:  # recording must never break the pipeline
        logger.debug("Scene decision not recorded: %s", err)


def _valid_themes() -> set[str]:
    try:
        from autoshorts.longform.background import theme_names

        return set(theme_names())
    except Exception as err:
        logger.debug("Background themes unavailable, using fallback cycle: %s", err)
        return set(_FALLBACK_CYCLE)


def _beats_theme_rules() -> (
    tuple[Callable[[set[str], int], str], tuple[frozenset[str], ...], tuple[str, ...]] | None
):
    """Keyword rules from beats, or None when beats cannot be imported."""
    try:
        from autoshorts.longform import beats
    except Exception as err:
        logger.debug("beats not importable, using local theme table: %s", err)
        return None
    keyword_sets = (
        beats.NIGHT_THEME_WORDS,
        beats.MORNING_THEME_WORDS,
        beats.OFFICE_THEME_WORDS,
        beats.LAB_THEME_WORDS,
        beats.CITY_THEME_WORDS,
        beats.CANDY_THEME_WORDS,
    )
    return beats._choose_theme, keyword_sets, tuple(beats.THEME_CYCLE)


def _candidate_theme(words: set[str], index: int) -> tuple[str, bool]:
    """Return (theme, keyword_matched) before validation."""
    rules = _beats_theme_rules()
    if rules is not None:
        choose, keyword_sets, _cycle = rules
        return choose(words, index), any(words & keys for keys in keyword_sets)
    for keys, name in _FALLBACK_KEYWORD_THEMES:
        if words & keys:
            return name, True
    return _FALLBACK_CYCLE[index % len(_FALLBACK_CYCLE)], False


def _previous_theme(job_id: str | None, index: int) -> str | None:
    with _lock:
        entry = _last_theme.get(job_id)
    if entry is not None and entry[0] == index - 1:
        return entry[1]
    return None


def _remember(job_id: str | None, index: int, theme: str) -> None:
    with _lock:
        _last_theme[job_id] = (index, theme)
        _last_theme.move_to_end(job_id)
        while len(_last_theme) > _MEMORY_LIMIT:
            _last_theme.popitem(last=False)


def _other_theme(valid: set[str], previous: str, index: int) -> str:
    """A valid theme that is not ``previous``, rotated by section index for variety."""
    ordered = [name for name in _FALLBACK_CYCLE if name in valid and name != previous]
    if not ordered:
        ordered = sorted(name for name in valid if name != previous)
    return ordered[index % len(ordered)] if ordered else previous


def _decide(
    section_index: int, narration: str, total_sections: int, job_id: str | None
) -> SceneDecision:
    index = int(section_index)
    text = narration.strip() if isinstance(narration, str) else ""
    valid = _valid_themes()

    if text:
        words = set(_WORD_RE.findall(text.lower()))
        theme, keyword_matched = _candidate_theme(words, index)
        if theme not in valid:
            theme = _FALLBACK_CYCLE[index % len(_FALLBACK_CYCLE)]
            keyword_matched = False
        if keyword_matched:
            theme_source, theme_reason = "heuristic", "backdrop keyword match"
        else:
            theme_source, theme_reason = "default", "backdrop cycle (no keyword match)"
        caption_source, caption_reason = "config", "longform default caption preset"
    else:
        theme = _FALLBACK_CYCLE[index % len(_FALLBACK_CYCLE)]
        theme_source, theme_reason = "default", EMPTY_NARRATION_REASON
        caption_source, caption_reason = "default", EMPTY_NARRATION_REASON

    previous = _previous_theme(job_id, index)
    if previous is not None and theme == previous:
        theme = _other_theme(valid, previous, index)
        theme_source = "default"
        if text:
            theme_reason = f"{theme_reason}; moved off {previous!r} to avoid a repeat"

    _remember(job_id, index, theme)
    _record(job_id, "scene_theme", theme, theme_reason, theme_source)
    _record(job_id, "scene_caption", DEFAULT_CAPTION_PRESET, caption_reason, caption_source)

    reasons = {
        "theme": f"{theme_source}: {theme_reason}",
        "layout_mode": "config: longform default layout",
        "caption_preset": f"{caption_source}: {caption_reason}",
        "sprite_placement_mode": "auto: sprites placed by narration keywords",
        "section": f"section {index + 1} of {total_sections}",
    }
    return SceneDecision(
        theme=theme,
        layout_mode=DEFAULT_LAYOUT_MODE,
        caption_preset=DEFAULT_CAPTION_PRESET,
        sprite_placement_mode=SPRITE_PLACEMENT_MODE,
        reasons=reasons,
    )


def decide_scene(
    section_index: int,
    heading: str,
    narration: str,
    speaker: str,
    total_sections: int,
    job_id: str | None = None,
) -> SceneDecision:
    """Decide theme, layout, caption preset and sprite placement for one section.

    ``heading`` and ``speaker`` are accepted for context; the rules read the narration only.
    Never raises: on an unexpected error the defaults are returned and recorded as such.
    """
    del heading, speaker
    try:
        return _decide(section_index, narration, total_sections, job_id)
    except Exception as err:
        logger.warning(
            "Scene decision failed for section %s, using defaults: %s", section_index, err
        )
        reason = f"error: {err}"
        _record(job_id, "scene_theme", DEFAULT_THEME, reason, "default")
        _record(job_id, "scene_caption", DEFAULT_CAPTION_PRESET, reason, "default")
        return SceneDecision(
            theme=DEFAULT_THEME,
            layout_mode=DEFAULT_LAYOUT_MODE,
            caption_preset=DEFAULT_CAPTION_PRESET,
            sprite_placement_mode=SPRITE_PLACEMENT_MODE,
            reasons={key: f"default: {reason}" for key in REQUIRED_REASONS},
        )


def check_decisions(decisions: Sequence[SceneDecision]) -> list[str]:
    """Return human-readable problems in a sequence of scene decisions.

    Checks: consecutive identical themes, themes missing from background.theme_names(),
    caption presets missing from ass_styler.CAPTION_PRESETS, and missing reasons. Each
    problem is also logged at WARNING.
    """
    problems: list[str] = []
    try:
        from autoshorts.longform.background import theme_names

        valid_themes: set[str] | None = set(theme_names())
    except Exception as err:
        problems.append(f"background themes unavailable: {err}")
        valid_themes = None
    try:
        from autoshorts.media.ass_styler import CAPTION_PRESETS

        valid_presets: set[str] | None = set(CAPTION_PRESETS)
    except Exception as err:
        problems.append(f"caption presets unavailable: {err}")
        valid_presets = None

    previous_theme: str | None = None
    for number, decision in enumerate(decisions, start=1):
        if valid_themes is not None and decision.theme not in valid_themes:
            problems.append(f"scene {number}: unknown theme {decision.theme!r}")
        if number > 1 and decision.theme == previous_theme:
            problems.append(f"scene {number}: theme {decision.theme!r} repeats the previous scene")
        if valid_presets is not None and decision.caption_preset not in valid_presets:
            problems.append(f"scene {number}: unknown caption preset {decision.caption_preset!r}")
        for key in REQUIRED_REASONS:
            if not str(decision.reasons.get(key, "")).strip():
                problems.append(f"scene {number}: missing reason for {key}")
        previous_theme = decision.theme

    for problem in problems:
        logger.warning("Scene decision problem: %s", problem)
    return problems


__all__ = [
    "DEFAULT_CAPTION_PRESET",
    "SceneDecision",
    "check_decisions",
    "decide_scene",
]
