"""Remotion caption overlay: render word-by-word captions with React, then composite with FFmpeg.

The pipeline keeps FFmpeg as the only compositor. Remotion is used for one job only: rendering a
transparent caption track (VP9 with alpha) from word timings. FFmpeg then overlays that track onto
the finished Short, so layouts, audio, and encoding are unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autoshorts.core.paths import PROJECT_ROOT
from autoshorts.media.ass_styler import (
    TimedWord,
    offset_words_for_hook,
    remap_words_for_jump_cuts,
)

logger = logging.getLogger("autoshorts.remotion_overlay")

REMOTION_PROJECT_DIR = PROJECT_ROOT / "remotion"
COMPOSITION_ID = "CaptionOverlay"
OVERLAY_FPS = 30
RENDER_TIMEOUT_SEC = 900.0


class RemotionUnavailableError(RuntimeError):
    """Raised when the Remotion project has not been installed (npm install)."""


class RemotionRenderError(RuntimeError):
    """Raised when the Remotion render or the FFmpeg overlay step fails."""


def _remotion_binary(project_dir: Path) -> Path:
    name = "remotion.cmd" if os.name == "nt" else "remotion"
    return project_dir / "node_modules" / ".bin" / name


def is_remotion_ready(project_dir: Path = REMOTION_PROJECT_DIR) -> bool:
    """True when the Remotion CLI is installed in the project."""
    return _remotion_binary(project_dir).is_file()


def build_caption_timeline(
    words: Sequence[TimedWord | dict[str, Any]],
    *,
    hook_in_sec: float = 0.0,
    jump_cuts: Sequence[Any] | None = None,
    total_duration_sec: float | None = None,
) -> tuple[list[TimedWord], float | None]:
    """Map source-clip word timings onto the final Short's timeline.

    Applies the same hook trim and jump-cut remapping as the ASS subtitle writer, so the Remotion
    captions stay in sync with the rest of the edit.
    """
    timed = offset_words_for_hook(words, hook_in_sec) if hook_in_sec > 0 else list(words)
    duration = total_duration_sec
    if jump_cuts:
        timed = remap_words_for_jump_cuts(timed, jump_cuts)
        if duration is not None:
            cut_total = sum(
                max(0.0, c.cut_end - c.cut_start) for c in jump_cuts if c.cut_end > c.cut_start >= 0
            )
            duration = max(1.0, duration - cut_total)
    else:
        timed = [w if isinstance(w, TimedWord) else TimedWord.from_dict(w) for w in timed]
    return timed, duration


def write_caption_plan(
    path: Path,
    words: Sequence[TimedWord],
    *,
    duration_sec: float,
    style_preset: str = "rapid",
    fps: int = OVERLAY_FPS,
) -> Path:
    """Write the Remotion input props as JSON.

    Presets only change the caption look; 'rapid' shows 1-2 words at a time, others show 3.
    """
    words_per_page = 2 if style_preset == "rapid" else 3
    payload = {
        "words": [{"text": w.word, "start": w.start, "end": w.end} for w in words],
        "durationSec": max(0.5, float(duration_sec)),
        "fps": fps,
        "wordsPerPage": words_per_page,
        "activeColor": "#FFFF00",
        "primaryColor": "#FFFFFF",
        "fontSize": 84 if style_preset == "rapid" else 76,
        "bottomPercent": 38,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def render_caption_overlay(
    props_path: Path,
    output_path: Path,
    *,
    project_dir: Path = REMOTION_PROJECT_DIR,
    timeout_sec: float = RENDER_TIMEOUT_SEC,
) -> Path:
    """Render the transparent caption track (VP9 + alpha) with the Remotion CLI."""
    binary = _remotion_binary(project_dir)
    if not binary.is_file():
        raise RemotionUnavailableError(
            f"Remotion is not installed in {project_dir}. Run `npm install` in that folder."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(binary),
        "render",
        COMPOSITION_ID,
        str(output_path),
        f"--props={props_path}",
        "--codec=vp9",
        "--pixel-format=yuva420p",
        "--image-format=png",
        "--overwrite",
        "--log=error",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemotionRenderError(f"Remotion render timed out after {timeout_sec:.0f}s") from exc
    if proc.returncode != 0 or not output_path.is_file():
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise RemotionRenderError(f"Remotion render failed (exit {proc.returncode}): {tail}")
    return output_path


def overlay_captions(
    base_video: Path,
    caption_track: Path,
    output_path: Path,
    *,
    ffmpeg_path: str,
    timeout_sec: float = 900.0,
) -> Path:
    """Composite the transparent caption track over the finished Short. Audio is copied as-is."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(base_video),
        # The native VP9 decoder drops the alpha plane and paints the captions over black.
        # libvpx-vp9 keeps it, so the transparent background shows the video underneath.
        "-c:v",
        "libvpx-vp9",
        "-i",
        str(caption_track),
        "-filter_complex",
        "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]",
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemotionRenderError(f"Caption overlay timed out after {timeout_sec:.0f}s") from exc
    if proc.returncode != 0 or not output_path.is_file():
        tail = (proc.stderr or "").strip()[-800:]
        raise RemotionRenderError(f"Caption overlay failed (exit {proc.returncode}): {tail}")
    return output_path
