"""Multimodal AI Edit Planner implementing contracts.EditPlanner.

Builds structured prompts and interfaces with multimodal LLM providers (e.g. Gemini Video)
via Files API / inline video frames, or gracefully falls back to transcript-only SemIf planning.
Strictly outputs an Edit Decision List (EDL) JSON; never generates or assumes raw video byte manipulation.
Enforces strict schema validation, one-shot repair retries, timestamp clamping, and privacy warnings.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from autoshorts.core.config import EditPlannerConfig, Settings
from autoshorts.core.contracts import EditPlanner
from autoshorts.core.models import EditDecisionList
from autoshorts.edit_planner.edl_schema import (
    clamp_edl,
    create_empty_edl,
    validate_edl,
)

logger = logging.getLogger(__name__)

PRIVACY_NOTICE = (
    "PRIVACY NOTICE: Video frames will be transmitted to external cloud servers "
    "(Google Gemini API). Zero data leaves your computer only when using local providers."
)


def probe_clip_duration(video_path: Path | str, default: float = 45.0) -> float:
    """Probe video duration using ffprobe, with fallback to default if unavailable."""
    target = Path(video_path)
    if not target.is_file():
        return default

    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(target),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            val = float(result.stdout.strip())
            if val > 0:
                return val
    except Exception as e:
        logger.debug("ffprobe duration probe failed on %s: %s", target, e)

    return default


def build_edl_prompt(
    clip_duration: float,
    transcript_text: str | None = None,
    instructions: str | None = None,
    is_multimodal: bool = False,
) -> str:
    """Construct structured instruction prompt commanding strict EDL JSON response."""
    transcript_section = (
        f"\nTRANSCRIPT EXCERPT:\n{transcript_text.strip()}\n"
        if transcript_text and transcript_text.strip()
        else "\n(No raw transcript provided; analyze spoken dialogue and visual cues directly)\n"
    )

    instructions_section = (
        f"\nOPERATOR EDITORIAL INSTRUCTIONS:\n{instructions.strip()}\n"
        if instructions and instructions.strip()
        else ""
    )

    multimodal_note = (
        "You are inspecting the full video stream (visual frames and audio track)."
        if is_multimodal
        else "You are analyzing the spoken dialogue transcript."
    )

    prompt = f"""[ROLE: EXPERT SHORT-FORM VIRAL VIDEO EDITOR]
{multimodal_note}
Your goal is to plan precision micro-edits for a high-retention YouTube Short.

CLIP DURATION: {clip_duration:.2f} seconds
{transcript_section}
{instructions_section}
OUTPUT FORMAT REQUIREMENTS:
You MUST respond with ONLY a single, valid JSON object matching this exact schema:
{{
  "hook_in_sec": <float: start trim (seconds) to eliminate throat-clearing/dead air, >= 0.0>,
  "hook_out_sec": <float: end trim (seconds) for punchy conclusion, <= {clip_duration:.2f}>,
  "jump_cuts": [
    {{
      "cut_start": <float: start timestamp of dead air/filler>,
      "cut_end": <float: end timestamp of dead air/filler>,
      "reason": <string: e.g. "awkward pause" or "filler phrase">
    }}
  ],
  "zoom_punch_ins": [
    {{
      "timestamp": <float: timestamp of peak emphasis word>,
      "scale": <float: zoom factor between 1.05 and 1.25>,
      "duration": <float: duration between 0.3 and 2.0 seconds>,
      "emphasis_word": <string: peak word>
    }}
  ],
  "caption_corrections": [
    {{
      "original": <string: misheard word>,
      "corrected": <string: correct spelling or jargon>
    }}
  ],
  "recommended_thumbnail_timestamp": <float: timestamp with expressive facial expression>,
  "recommended_title": <string: high CTR title under 60 chars ending in #Shorts>,
  "recommended_hook": <string: intriguing hook question or statement under 100 chars>,
  "background_style_recommendation": <"minecraft" | "subway" | "auto">
}}

CRITICAL INVARIANTS:
1. Do NOT attempt to output video files or binary data. You produce symbolic editing decisions only.
2. hook_in_sec must be strictly less than hook_out_sec.
3. Jump cut timestamps must not overlap with one another and must lie strictly between hook_in_sec and hook_out_sec.
4. Return pure JSON without conversational prose or markdown formatting outside the JSON block.
"""
    return prompt


class MultimodalEditPlanner(EditPlanner):
    """Multimodal AI Edit Planner implementing contracts.EditPlanner."""

    def __init__(
        self,
        provider: Any | None = None,
        config: EditPlannerConfig | Settings | None = None,
        default_duration: float = 45.0,
    ) -> None:
        """Initialize MultimodalEditPlanner with provider and configuration.

        Args:
            provider: Injected LLM provider (multimodal or text).
            config: EditPlannerConfig or Settings.
            default_duration: Default fallback duration if media file duration is unknown.
        """
        if config is not None:
            self.config = config.edit_planner if isinstance(config, Settings) else config
        else:
            self.config = EditPlannerConfig()

        self.provider = provider
        self.default_duration = default_duration
        self.last_privacy_warning: str | None = None

    def generate_edl(
        self,
        video_clip_path: Path,
        transcript_text: str | None = None,
        instructions: str | None = None,
    ) -> EditDecisionList:
        """Analyze video clip and generate symbolic EDL (hook trims, jump cuts, zoom punch-ins).

        If multimodal provider is unavailable or fails, falls back to transcript-only SemIf planning.
        If LLM parsing fails, attempts schema-repair retry once before returning deterministic empty EDL.
        """
        clip_path = Path(video_clip_path)
        clip_duration = probe_clip_duration(clip_path, default=self.default_duration)

        # If planner is disabled or no provider injected, return empty fallback
        if not self.config.enabled or self.provider is None:
            logger.info("Edit planner disabled or no provider injected; returning clean empty EDL")
            return create_empty_edl(clip_duration=clip_duration)

        # 1. Attempt generation (multimodal or transcript-only)
        raw_output: str | None = None
        is_multimodal = False

        # Check if provider supports multimodal video analysis
        if hasattr(self.provider, "analyze_video") or hasattr(self.provider, "complete_multimodal"):
            try:
                self.last_privacy_warning = PRIVACY_NOTICE
                logger.warning(PRIVACY_NOTICE)
                is_multimodal = True

                prompt = build_edl_prompt(
                    clip_duration=clip_duration,
                    transcript_text=transcript_text,
                    instructions=instructions,
                    is_multimodal=True,
                )

                if hasattr(self.provider, "analyze_video"):
                    raw_output = self.provider.analyze_video(clip_path, prompt=prompt)
                elif hasattr(self.provider, "complete_multimodal"):
                    raw_output = self.provider.complete_multimodal(clip_path, prompt=prompt)
            except Exception as e:
                logger.warning("Multimodal video analysis failed: %s", e)
                if not self.config.fallback_to_transcript:
                    return create_empty_edl(clip_duration=clip_duration)

        # Fallback to transcript-only completion if raw_output is still None
        if raw_output is None:
            try:
                prompt = build_edl_prompt(
                    clip_duration=clip_duration,
                    transcript_text=transcript_text,
                    instructions=instructions,
                    is_multimodal=False,
                )
                if hasattr(self.provider, "complete"):
                    raw_output = self.provider.complete(prompt=prompt)
                else:
                    logger.warning("Provider lacks complete() interface; returning empty EDL")
                    return create_empty_edl(clip_duration=clip_duration)
            except Exception as e:
                logger.warning("Transcript-only edit planning failed: %s", e)
                return create_empty_edl(clip_duration=clip_duration)

        # 2. Parse and validate JSON with 1-shot schema repair
        validated_edl: EditDecisionList | None = None
        validation_error: str | None = None

        try:
            validated_edl = validate_edl(raw_output)
        except (ValidationError, ValueError, json.JSONDecodeError) as err:
            validation_error = str(err)
            logger.warning(
                "Initial EDL generation failed schema validation: %s. Attempting schema repair retry...",
                err,
            )

        # Attempt schema-repair retry ONCE if first attempt failed
        if validated_edl is None and validation_error is not None:
            repair_prompt = f"""[EDL SCHEMA REPAIR]
Your previous output failed strict JSON schema validation for EditDecisionList.

VALIDATION ERROR:
{validation_error}

ORIGINAL OUTPUT:
{raw_output}

INSTRUCTION:
Fix all formatting, syntax, and schema errors in the JSON.
Return ONLY the corrected, valid JSON object matching the EditDecisionList schema.
Do NOT include markdown explanations or code blocks outside the JSON.
"""
            try:
                if hasattr(self.provider, "complete"):
                    repaired_output = self.provider.complete(prompt=repair_prompt)
                    validated_edl = validate_edl(repaired_output)
                    logger.info("EDL schema-repair retry succeeded!")
            except Exception as err:
                logger.warning("EDL schema-repair retry failed: %s", err)

        # If both initial and repair attempts failed, use deterministic fallback
        if validated_edl is None:
            logger.warning("All EDL validation attempts failed; falling back to deterministic empty EDL")
            return create_empty_edl(clip_duration=clip_duration)

        # 3. Clamp times and reject absurd/overlapping jump cuts
        final_edl = clamp_edl(validated_edl, clip_duration=clip_duration)
        return final_edl
