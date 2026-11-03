"""Retention Editor Specialist Agent.

Configures speech-adaptive silence excision thresholds, optical speaker
zoom centering ('left', 'right', 'center'), and mathematically guarded
pattern interrupt parameters.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, Literal

from autoshorts.brain.schemas import RetentionPlan

logger = logging.getLogger("autoshorts.brain.retention_editor")


def cliffs_to_interrupts(
    cliffs: Iterable[Any],
    clip_start: float,
    clip_end: float,
    max_per_minute: int = 4,
    refractory: float = 4.5,
) -> list[float]:
    """Turn forecast retention cliffs into pattern-interrupt times for one clip.

    Args:
        cliffs: ``RetentionCliff`` objects (anything with a ``timestamp`` attribute)
            or plain numbers, in source-video seconds.
        clip_start: Clip window start in source-video seconds.
        clip_end: Clip window end in source-video seconds.
        max_per_minute: Maximum interrupts in any rolling 60 s window of the clip.
        refractory: Minimum spacing in seconds between consecutive interrupts.

    Returns:
        Interrupt times relative to ``clip_start``, sorted ascending. Cliffs outside
        the window are dropped; cliffs violating the refractory or the rate cap are
        dropped in time order (earliest cliff wins).
    """
    if clip_end <= clip_start or max_per_minute <= 0:
        return []

    times = sorted(
        float(c.timestamp) if hasattr(c, "timestamp") else float(c)  # type: ignore[arg-type]
        for c in cliffs
    )

    accepted: list[float] = []
    for t in times:
        if t < clip_start or t > clip_end:
            continue
        rel = t - clip_start
        if accepted and (rel - accepted[-1]) < refractory:
            continue
        in_window = sum(1 for a in accepted if (rel - a) < 60.0)
        if in_window >= max_per_minute:
            continue
        accepted.append(rel)

    return [round(a, 2) for a in accepted]


class RetentionEditorAgent:
    """Specialist agent that calibrates retention pacing, silence thresholds, and framing."""

    async def plan(
        self,
        words_per_second: float = 2.5,
        speaker_layout_hint: str | None = None,
        transcript: str | None = None,
    ) -> RetentionPlan:
        """Calculate adaptive silence trimming and optical speaker centering."""
        # 1. Adaptive silence trimming threshold
        if words_per_second > 3.0:
            adaptive_thresh = -28.0  # Tight aggressive silence excision for rapid speech
        elif words_per_second < 2.2:
            adaptive_thresh = -35.0  # Gentle headroom for slower/dramatic speech pauses
        else:
            adaptive_thresh = -32.0  # Industry standard short-form baseline

        # 2. Optical speaker zoom center
        center: Literal["left", "right", "center"] = "center"
        hint = (speaker_layout_hint or "").lower()
        if "left" in hint:
            center = "left"
        elif "right" in hint:
            center = "right"
        elif transcript:
            t_lower = transcript.lower()
            if "host on left" in t_lower or "split left" in t_lower:
                center = "left"
            elif "host on right" in t_lower or "guest on right" in t_lower:
                center = "right"

        return RetentionPlan(
            adaptive_silence_thresh_db=adaptive_thresh,
            speaker_zoom_center=center,
            pattern_interrupt_enabled=True,
            max_interrupts=4,  # Hard ceiling: <=4 per 60s Short
            min_cooldown_seconds=4.5,  # Minimum refractory cooldown >=4.5s
            cliff_slope_threshold=-0.035,  # Retention cliff derivative threshold
            jump_cuts_enabled=True,
        )
