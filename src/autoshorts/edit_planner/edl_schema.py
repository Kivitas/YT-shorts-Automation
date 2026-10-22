"""Pydantic schema, validation, clamping, and repair utilities for EditDecisionList.

Enforces strict structural constraints on AI-generated micro-editing parameters,
clamps timestamps to physical clip bounds, excises overlapping or absurd jump cuts,
and produces deterministic empty EDL fallbacks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from autoshorts.core.models import (
    EditDecisionList,
    JumpCut,
    ZoomPunchIn,
)

EDL_JSON_SCHEMA = EditDecisionList.model_json_schema()


REQUIRED_EDL_KEYS = [
    "hook_in_sec",
    "hook_out_sec",
    "jump_cuts",
    "zoom_punch_ins",
    "caption_corrections",
    "recommended_thumbnail_timestamp",
    "recommended_title",
    "recommended_hook",
    "background_style_recommendation",
]


def validate_edl(data: str | Mapping[str, Any], strict_keys: bool = False) -> EditDecisionList:
    """Strictly validate raw JSON string or mapping against EditDecisionList schema.

    Args:
        data: JSON string or dictionary representation of EDL.
        strict_keys: If True, require all PLAN.md 5.5 required fields to be present.

    Returns:
        Validated EditDecisionList instance.

    Raises:
        ValidationError: If the data violates field constraints.
        ValueError: If JSON string parsing fails or required fields are missing.
    """
    if isinstance(data, str):
        cleaned_str = data.strip()
        # Remove common markdown code fences if present
        if cleaned_str.startswith("```"):
            lines = cleaned_str.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned_str = "\n".join(lines).strip()

        parsed_dict = json.loads(cleaned_str)
    elif isinstance(data, Mapping):
        parsed_dict = dict(data)
    else:
        raise ValueError(f"Unsupported data type for validate_edl: {type(data).__name__}")

    if not isinstance(parsed_dict, dict):
        raise ValueError(f"Expected JSON object, got {type(parsed_dict).__name__}")

    if strict_keys:
        missing = [k for k in REQUIRED_EDL_KEYS if k not in parsed_dict]
        if missing:
            raise ValueError(f"Missing required EditDecisionList fields: {missing}")

    return EditDecisionList.model_validate(parsed_dict)


def create_empty_edl(
    clip_duration: float = 45.0,
    title: str = "",
    hook: str = "",
    background: str = "minecraft",
    fallback_reason: str | None = None,
) -> EditDecisionList:
    """Generate a deterministic empty Edit Decision List with no micro-edits.

    Used as an unyielding fallback if multimodal LLM calls or schema repair fail.
    The result is tagged ``source="empty_fallback"`` so downstream stages and the
    dashboard can tell a placeholder plan from a deliberately edit-free real plan.
    """
    safe_duration = max(5.0, float(clip_duration))
    safe_title = (title.strip() if title else "Viral Insight #Shorts")[:60]
    safe_hook = (hook.strip() if hook else "Watch what happens next")[:100]
    bg_style = background if background in ("minecraft", "subway", "auto") else "minecraft"

    return EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=safe_duration,
        jump_cuts=[],
        zoom_punch_ins=[],
        caption_corrections=[],
        recommended_thumbnail_timestamp=min(1.0, safe_duration / 2.0),
        recommended_title=safe_title,
        recommended_hook=safe_hook,
        background_style_recommendation=bg_style,  # type: ignore[arg-type]
        source="empty_fallback",
        fallback_reason=fallback_reason,
    )


def clamp_edl(edl: EditDecisionList, clip_duration: float) -> EditDecisionList:
    """Clamp EDL timestamps to physical media boundaries and reject invalid cuts.

    Rules applied:
    1. hook_in_sec is clamped to [0.0, clip_duration - 1.0].
    2. hook_out_sec is clamped to [hook_in_sec + 3.0, clip_duration].
    3. Thumbnail timestamp is clamped to [hook_in_sec, hook_out_sec].
    4. Jump cuts:
       - Rejects cuts outside [hook_in_sec, hook_out_sec].
       - Rejects absurd cuts (cut_start >= cut_end, or cut >= 90% of duration).
       - Rejects overlapping cuts (subsequent cut starts before previous cut ends).
       - Rejects cuts if total excised time leaves < 5.0 seconds of speech.
    5. Zoom punch-ins:
       - Timestamps clamped to [hook_in_sec, hook_out_sec].
       - Scale factor clamped to [1.05, 1.25].
       - Duration clamped to [0.3, 2.0].
       - Discards punch-ins that fall inside an excised jump cut interval.
    6. Title & hook lengths clamped to model maximums (60 and 100 chars).
    7. Background style guaranteed to be valid enum.
    """
    max_dur = max(5.0, float(clip_duration))

    # 1. Clamp hook in / out
    hook_in = max(0.0, min(float(edl.hook_in_sec), max_dur - 1.0))
    hook_out = max(hook_in + 3.0, min(float(edl.hook_out_sec), max_dur))
    if hook_out <= hook_in:
        hook_out = max_dur

    effective_span = hook_out - hook_in

    # 2. Thumbnail
    thumb_ts = max(hook_in, min(float(edl.recommended_thumbnail_timestamp), hook_out))

    # 3. Filter and validate jump cuts
    valid_cuts: list[JumpCut] = []
    # Sort candidate cuts by start timestamp
    sorted_cuts = sorted(edl.jump_cuts, key=lambda c: float(c.cut_start))

    last_end = hook_in
    for cut in sorted_cuts:
        c_start = float(cut.cut_start)
        c_end = float(cut.cut_end)

        # Absurdity check: start after end or negative
        if c_start < 0.0 or c_start >= c_end:
            continue

        # Absurdity check: cut outside active hook window
        if c_start < hook_in or c_end > hook_out:
            continue

        # Absurdity check: single cut excises more than 80% of window or duration < 0.1s
        cut_len = c_end - c_start
        if cut_len < 0.1 or cut_len >= (effective_span * 0.8):
            continue

        # Overlapping check: must not overlap with preceding accepted cut
        if c_start < last_end:
            # Overlapping cut rejected!
            continue

        valid_cuts.append(
            JumpCut(
                cut_start=round(c_start, 2),
                cut_end=round(c_end, 2),
                reason=cut.reason or "silence/pause",
            )
        )
        last_end = c_end

    # Sanity check: Ensure remaining unexcised duration is at least 5.0 seconds
    total_excised = sum(c.cut_end - c.cut_start for c in valid_cuts)
    if (effective_span - total_excised) < 5.0:
        # Jump cuts excised too much content; reject all cuts to preserve narrative
        valid_cuts = []

    # 4. Zoom punch-ins
    valid_punch_ins: list[ZoomPunchIn] = []
    for punch in edl.zoom_punch_ins:
        p_ts = float(punch.timestamp)
        # Must be within hook range
        if p_ts < hook_in or p_ts > hook_out:
            continue

        # Discard if punch-in falls inside an excised jump cut
        in_cut = any(c.cut_start <= p_ts <= c.cut_end for c in valid_cuts)
        if in_cut:
            continue

        clamped_scale = max(1.05, min(1.25, float(punch.scale)))
        clamped_duration = max(0.3, min(2.0, float(punch.duration)))

        valid_punch_ins.append(
            ZoomPunchIn(
                timestamp=round(p_ts, 2),
                scale=round(clamped_scale, 2),
                duration=round(clamped_duration, 2),
                emphasis_word=punch.emphasis_word.strip()[:30],
            )
        )

    # 5. Metadata clamping
    title = (edl.recommended_title or "Insight #Shorts")[:60]
    hook = (edl.recommended_hook or "")[:100]
    bg_style = (
        edl.background_style_recommendation
        if edl.background_style_recommendation in ("minecraft", "subway", "auto")
        else "minecraft"
    )

    return EditDecisionList(
        hook_in_sec=round(hook_in, 2),
        hook_out_sec=round(hook_out, 2),
        jump_cuts=valid_cuts,
        zoom_punch_ins=valid_punch_ins,
        caption_corrections=list(edl.caption_corrections),
        recommended_thumbnail_timestamp=round(thumb_ts, 2),
        recommended_title=title,
        recommended_hook=hook,
        background_style_recommendation=bg_style,
        # Clamping must never launder the plan's provenance.
        source=edl.source,
        fallback_reason=edl.fallback_reason,
    )
