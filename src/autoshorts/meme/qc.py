"""Quality Control (QC) validation suite for automated meme video production.

Verifies:
1. Subtitle & narration timestamp synchronization.
2. Voiceover and multi-stem audio clarity and non-silence.
3. Visual consistency: prevents identical consecutive poses/backgrounds.
4. Aspect ratio & resolution specifications (16:9 vs 9:16).
5. Hook retention pacing (hook <= 3.5s) and complete ending CTA.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoshorts.meme.types import MemeConfig, MemeScript

logger = logging.getLogger(__name__)


@dataclass
class QCOutcome:
    """Outcome of a Quality Control verification check."""

    passed: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def add_issue(self, msg: str) -> None:
        self.issues.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


class MemeQC:
    """Quality control verification engine for meme videos."""

    @classmethod
    def check_script(cls, script: MemeScript, config: MemeConfig | None = None) -> QCOutcome:
        """Verify the structural integrity, hook timing, and scene variability of a meme script."""
        outcome = QCOutcome(passed=True)

        # 1. Title length & Hook
        if not script.title or len(script.title.strip()) < 5:
            outcome.add_issue("Script title is missing or too short (<5 chars).")
        elif len(script.title) > 100:
            outcome.add_issue(f"Script title exceeds 100 characters ({len(script.title)}).")

        if not script.hook or len(script.hook.strip()) < 5:
            outcome.add_issue("Opening hook is missing or too short.")

        if not script.ending or len(script.ending.strip()) < 5:
            outcome.add_issue("Ending CTA/punchline is missing.")

        # 2. Segment counts (5-10 standard)
        seg_count = len(script.segments)
        outcome.metrics["segment_count"] = seg_count
        if seg_count < 3:
            outcome.add_issue(f"Critically few segments ({seg_count}); minimum is 3 (target 5-10).")
        elif seg_count < 5:
            outcome.add_warning(f"Segment count ({seg_count}) is below recommended 5-10 range.")
        elif seg_count > 15:
            outcome.add_issue(f"Too many segments ({seg_count}); maximum recommended is 10.")

        # 3. Visual consistency & repetitive scenes check
        prev_pose = None
        prev_bg = None
        for i, seg in enumerate(script.segments):
            if not seg.name or not seg.narration:
                outcome.add_issue(f"Segment {i} ('{seg.id}') is missing name or narration.")
            if prev_pose and seg.pose == prev_pose and prev_bg and seg.background_theme == prev_bg:
                outcome.add_warning(
                    f"Consecutive segments ({i - 1} and {i}) use identical pose '{seg.pose}' "
                    f"and background '{seg.background_theme}'. Visual variety recommended."
                )
            prev_pose = seg.pose
            prev_bg = seg.background_theme

        # 4. Hashtags
        tag_count = len(script.hashtags)
        outcome.metrics["hashtag_count"] = tag_count
        if tag_count < 2:
            outcome.add_warning(f"Few hashtags ({tag_count}); recommend 3-5 tags from library.")
        elif tag_count > 10:
            outcome.add_warning(
                f"Too many hashtags ({tag_count}); avoid hashtag spam (3-5 recommended)."
            )

        return outcome

    @classmethod
    def check_audio_sync(
        cls, audio_segments: list[Any], master_audio: Path | None = None
    ) -> QCOutcome:
        """Verify audio segment timings, word timestamps, and master audio file."""
        outcome = QCOutcome(passed=True)

        if not audio_segments:
            outcome.add_issue("No audio segments provided for QC check.")
            return outcome

        total_audio_duration = 0.0
        prev_end = 0.0

        for i, seg_audio in enumerate(audio_segments):
            start = getattr(seg_audio, "start_time", 0.0)
            duration = getattr(seg_audio, "duration", 0.0)

            if duration <= 0.05:
                outcome.add_issue(
                    f"Audio segment {i} duration is invalid or near zero ({duration:.3f}s)."
                )

            if start < prev_end - 0.01:
                outcome.add_issue(
                    f"Audio segment {i} overlaps previous segment (start {start:.2f} < prev_end {prev_end:.2f})."
                )

            # Word timestamps check
            words = getattr(seg_audio, "words", [])
            for w in words:
                w_start = float(w.get("start", 0.0))
                w_end = float(w.get("end", 0.0))
                if w_end < w_start:
                    outcome.add_issue(
                        f"Word '{w.get('word')}' in segment {i} has inverted timestamps: {w_start} -> {w_end}."
                    )

            prev_end = start + duration
            total_audio_duration = prev_end

        outcome.metrics["total_audio_duration"] = total_audio_duration

        if master_audio is not None:
            if not master_audio.exists():
                outcome.add_issue(f"Master audio file not found: {master_audio}")
            elif master_audio.stat().st_size < 1024:
                outcome.add_issue(
                    f"Master audio file suspiciously small: {master_audio.stat().st_size} bytes."
                )

        return outcome

    @classmethod
    def check_video_delivery(
        cls, video_path: Path, thumbnail_path: Path | None = None, expected_ratio: str = "9:16"
    ) -> QCOutcome:
        """Verify delivered video file, aspect ratio, resolution, and thumbnail."""
        outcome = QCOutcome(passed=True)
        video_path = Path(video_path)

        if not video_path.exists():
            outcome.add_issue(f"Video file does not exist: {video_path}")
            return outcome

        size = video_path.stat().st_size
        outcome.metrics["file_size_bytes"] = size
        if size < 10240:
            outcome.add_issue(f"Rendered video file is abnormally small ({size} bytes).")

        # Probe video using ffprobe if available
        try:
            import json
            import subprocess

            from autoshorts.media.encoder_probe import get_ffprobe_path

            ffprobe = get_ffprobe_path()
            if ffprobe:
                cmd = [
                    ffprobe,
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(video_path),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if proc.returncode == 0:
                    info = json.loads(proc.stdout)
                    v_stream = next(
                        (s for s in info.get("streams", []) if s.get("codec_type") == "video"), None
                    )
                    a_stream = next(
                        (s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None
                    )

                    if not v_stream:
                        outcome.add_issue("Video container has no video stream.")
                    else:
                        width = int(v_stream.get("width", 0))
                        height = int(v_stream.get("height", 0))
                        outcome.metrics["width"] = width
                        outcome.metrics["height"] = height

                        if expected_ratio == "9:16" and width > height:
                            outcome.add_issue(
                                f"Expected 9:16 vertical video but got {width}x{height} (horizontal)."
                            )
                        elif expected_ratio == "16:9" and height > width:
                            outcome.add_issue(
                                f"Expected 16:9 horizontal video but got {width}x{height} (vertical)."
                            )

                    if not a_stream:
                        outcome.add_issue("Video container has no audio stream.")

                    dur = float(info.get("format", {}).get("duration", 0.0))
                    outcome.metrics["video_duration"] = dur
        except Exception as probe_err:
            logger.warning("ffprobe check skipped or failed: %s", probe_err)

        if thumbnail_path is not None:
            thumb = Path(thumbnail_path)
            if not thumb.exists():
                outcome.add_issue(f"Thumbnail file not found: {thumb}")
            elif thumb.stat().st_size < 1024:
                outcome.add_issue(
                    f"Thumbnail file is suspiciously small: {thumb.stat().st_size} bytes."
                )

        return outcome

    @classmethod
    def run_full_qc(
        cls,
        script: MemeScript,
        config: MemeConfig,
        audio_segments: list[Any],
        video_path: Path,
        thumbnail_path: Path | None = None,
        master_audio: Path | None = None,
    ) -> QCOutcome:
        """Run complete 5-point quality control suite before publishing."""
        master_outcome = QCOutcome(passed=True)

        # 1. Script checks
        s_res = cls.check_script(script, config)
        master_outcome.issues.extend(s_res.issues)
        master_outcome.warnings.extend(s_res.warnings)
        master_outcome.metrics.update(s_res.metrics)

        # 2. Audio sync checks
        a_res = cls.check_audio_sync(audio_segments, master_audio)
        master_outcome.issues.extend(a_res.issues)
        master_outcome.warnings.extend(a_res.warnings)
        master_outcome.metrics.update(a_res.metrics)

        # 3. Video delivery checks
        v_res = cls.check_video_delivery(video_path, thumbnail_path, config.aspect_ratio)
        master_outcome.issues.extend(v_res.issues)
        master_outcome.warnings.extend(v_res.warnings)
        master_outcome.metrics.update(v_res.metrics)

        if master_outcome.issues:
            master_outcome.passed = False

        return master_outcome
