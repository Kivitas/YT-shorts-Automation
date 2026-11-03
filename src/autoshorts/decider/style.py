"""Style decider: resolves ``layout_mode`` and ``style_preset`` for one job.

Pure, deterministic and dependency-free. ``choose_style`` takes a plain
mapping of signals already present on a job and returns a ``StyleChoice``.
Every resolved value is recorded with ``record_decision`` (names
``layout_mode`` and ``style_preset``).

Precedence per value (first match wins):

1. ``user``: the job already carries a non-default value (the model default
   is ``split_50_50`` for layout and ``neon`` for style). It is kept as-is.
2. ``recipe``: ``job_context["recipe"]`` carries an analytics recipe value.
3. ``heuristic``:
   - style_preset ``kurz`` when the clip is shorter than ``SHORT_CLIP_SEC``.
   - layout_mode ``split_50_50`` when the source is a talking head and the
     clip is not a ranking edit.
4. ``config``: the config default the caller passed in (unchanged).

Missing or malformed fields never raise; they fall through to the next rule.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from autoshorts.decider.records import DecisionRecord, DecisionSource, record_decision

logger = logging.getLogger(__name__)

SHORT_CLIP_SEC = 35.0
SHORT_CLIP_PRESET = "kurz"
TALKING_HEAD_LAYOUT = "split_50_50"

DEFAULT_LAYOUT_MODE = "split_50_50"
DEFAULT_STYLE_PRESET = "neon"
NEUTRAL_BACKGROUND = "none"

# Decision sources beyond the records.py Literal. They are passed through
# ``cast`` at runtime; records.py should extend DecisionSource to include them.
_SOURCE_USER = "user"
_SOURCE_RECIPE = "recipe"
_SOURCE_HEURISTIC = "heuristic"
_SOURCE_CONFIG = "config"


@dataclass(frozen=True)
class StyleChoice:
    """Resolved style for a job, with the reason and source for each value."""

    layout_mode: str
    style_preset: str
    background_category: str
    reasons: dict[str, str] = field(default_factory=dict)


def _clean(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out > 0 else None  # NaN and non-positive rejected


def _recipe_value(ctx: Mapping[str, Any], key: str) -> str | None:
    recipe = ctx.get("recipe")
    if not isinstance(recipe, Mapping):
        return None
    return _clean(recipe.get(key))


def _resolve_layout(ctx: Mapping[str, Any]) -> tuple[str, str, str]:
    cfg = _clean(ctx.get("config_layout_mode")) or DEFAULT_LAYOUT_MODE
    user = _clean(ctx.get("job_layout_mode"))
    if user and user != DEFAULT_LAYOUT_MODE:
        return user, _SOURCE_USER, "layout set by user on the job"
    recipe = _recipe_value(ctx, "layout_mode")
    if recipe:
        return recipe, _SOURCE_RECIPE, "layout from analytics recipe"
    is_ranking = _clean(ctx.get("format_kind")) == "ranking"
    if _clean(ctx.get("source_kind")) == "talking_head" and not is_ranking:
        return TALKING_HEAD_LAYOUT, _SOURCE_HEURISTIC, "talking-head source"
    return cfg, _SOURCE_CONFIG, "config default"


def _resolve_style(ctx: Mapping[str, Any]) -> tuple[str, str, str]:
    cfg = _clean(ctx.get("config_style_preset")) or DEFAULT_STYLE_PRESET
    user = _clean(ctx.get("job_style_preset"))
    if user and user != DEFAULT_STYLE_PRESET:
        return user, _SOURCE_USER, "style set by user on the job"
    recipe = _recipe_value(ctx, "style_preset")
    if recipe:
        return recipe, _SOURCE_RECIPE, "style from analytics recipe"
    duration = _as_float(ctx.get("duration_sec"))
    if duration is not None and duration < SHORT_CLIP_SEC:
        return (
            SHORT_CLIP_PRESET,
            _SOURCE_HEURISTIC,
            f"clip {duration:.1f}s is under {SHORT_CLIP_SEC:.0f}s",
        )
    return cfg, _SOURCE_CONFIG, "config default"


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
    except Exception as err:  # recording must never break rendering
        logger.debug("Style decision not recorded: %s", err)


def choose_style(job_context: Mapping[str, Any] | None) -> StyleChoice:
    """Resolve layout_mode and style_preset for a job from its context signals.

    Recognised keys (all optional):
        job_id, duration_sec, format_kind ("ranking" or other), source_kind
        ("talking_head" or "gameplay"), job_layout_mode, job_style_preset
        (current job values; defaults count as unset), config_layout_mode,
        config_style_preset, background_category, recipe ({"layout_mode",
        "style_preset"}).
    """
    try:
        ctx: Mapping[str, Any] = job_context if isinstance(job_context, Mapping) else {}
        job_id = _clean(ctx.get("job_id"))
        layout, layout_src, layout_reason = _resolve_layout(ctx)
        style, style_src, style_reason = _resolve_style(ctx)
        background = _clean(ctx.get("background_category")) or NEUTRAL_BACKGROUND
    except Exception as err:  # never raise: fall back to the config values
        logger.debug("choose_style fell back to config: %s", err)
        ctx = job_context if isinstance(job_context, Mapping) else {}
        return StyleChoice(
            layout_mode=_clean(ctx.get("config_layout_mode")) or DEFAULT_LAYOUT_MODE,
            style_preset=_clean(ctx.get("config_style_preset")) or DEFAULT_STYLE_PRESET,
            background_category=NEUTRAL_BACKGROUND,
            reasons={"fallback": "invalid job context"},
        )

    _record(job_id, "layout_mode", layout, layout_reason, layout_src)
    _record(job_id, "style_preset", style, style_reason, style_src)
    reasons = {
        "layout_mode": f"{layout_src}: {layout_reason}",
        "style_preset": f"{style_src}: {style_reason}",
        "background_category": "passed through" if background != NEUTRAL_BACKGROUND else "none",
    }
    return StyleChoice(
        layout_mode=layout,
        style_preset=style,
        background_category=background,
        reasons=reasons,
    )


__all__ = [
    "DEFAULT_LAYOUT_MODE",
    "DEFAULT_STYLE_PRESET",
    "SHORT_CLIP_PRESET",
    "SHORT_CLIP_SEC",
    "StyleChoice",
    "TALKING_HEAD_LAYOUT",
    "choose_style",
]
