"""Retention Editor Specialist Agent.

Configures speech-adaptive silence excision thresholds, optical speaker
zoom centering ('left', 'right', 'center'), and mathematically guarded
pattern interrupt parameters.
"""

from __future__ import annotations

import logging
from typing import Literal

from autoshorts.brain.schemas import RetentionPlan

logger = logging.getLogger("autoshorts.brain.retention_editor")


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
