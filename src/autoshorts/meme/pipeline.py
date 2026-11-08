"""Automated Production Pipeline for Country Memes and Types Of... Meme Animation Videos.

End-to-End Workflow:
1. Topic selection / AI invention
2. Script generation (5-10 types/countries, hook, intro, punchlines, ending CTA)
3. Audio synthesis (Narration + mood-matched BGM + sudden punchline cuts + SFX)
4. Visual frame rendering (Anime/Manga characters, cinematic backgrounds, camera animations, speedlines, speech bubbles, kinetic subtitles)
5. FFmpeg video encoding (16:9 or 9:16)
6. High-CTR thumbnail synthesis
7. Automated Quality Control (QC) check
8. YouTube publishing or local export
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from autoshorts.core.config import Settings, load_config
from autoshorts.core.paths import get_output_dir, get_work_dir
from autoshorts.core.state_manager import StateManager
from autoshorts.media.encoder_probe import get_ffmpeg_path
from autoshorts.meme.audio import synthesize_meme_audio
from autoshorts.meme.publish import publish_meme_video
from autoshorts.meme.qc import MemeQC, QCOutcome
from autoshorts.meme.script import build_description, build_meme_script
from autoshorts.meme.thumbnail import make_meme_thumbnail
from autoshorts.meme.types import MemeConfig, MemeResult
from autoshorts.meme.visuals import get_resolution, stream_all_meme_frames

logger = logging.getLogger(__name__)


def render_meme(
    config: MemeConfig | None = None,
    app_settings: Settings | None = None,
    state_manager: StateManager | None = None,
    work_dir: Path | None = None,
    out_dir: Path | None = None,
    cleanup_frames: bool = True,
    fps: int = 30,
) -> MemeResult:
    """Execute the complete end-to-end automated meme video production pipeline."""
    if config is None:
        config = MemeConfig()
    config.validate()

    cfg = app_settings or load_config()
    job_id = f"meme_{uuid4().hex[:8]}"

    # Setup directories
    base_work = Path(work_dir) if work_dir else (get_work_dir() / job_id)
    base_out = Path(out_dir) if out_dir else (get_output_dir() / "meme")
    frames_dir = base_work / "frames"
    audio_dir = base_work / "audio"

    base_work.mkdir(parents=True, exist_ok=True)
    base_out.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting Meme Video Generation: mode=%s, topic='%s', format=%s, style=%s",
        config.mode,
        config.topic,
        config.aspect_ratio,
        config.visual_style,
    )

    # 1. Topic & Script Generation
    t0 = time.monotonic()
    script = build_meme_script(config)
    logger.info("Generated Meme Script: '%s' with %d segments", script.title, len(script.segments))

    # 2. Audio & Multi-Stem SFX Synthesis
    master_audio_path, segment_audios = synthesize_meme_audio(
        script=script,
        work_dir=audio_dir,
        voice=config.voice,
    )
    total_audio_duration = sum(sa.duration for sa in segment_audios)
    logger.info(
        "Synthesized audio in %.2fs (total audio dur=%.2fs)",
        time.monotonic() - t0,
        total_audio_duration,
    )

    # 3. Direct Streaming Frame Rendering & Video Encoding with FFmpeg
    t_v0 = time.monotonic()
    video_out_path = base_out / f"{job_id}_{config.aspect_ratio.replace(':', '_')}.mp4"
    width, height = get_resolution(config.aspect_ratio)
    ffmpeg_bin = get_ffmpeg_path()

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-i",
        str(master_audio_path),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(video_out_path),
    ]

    logger.info("Streaming and encoding MP4 video directly with FFmpeg...")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_stream = stream_all_meme_frames(
        script=script,
        audio_segments=segment_audios,
        aspect_ratio=config.aspect_ratio,
        visual_style=config.visual_style,
        fps=fps,
    )
    assert proc.stdin is not None
    try:
        for f_bytes in frame_stream:
            proc.stdin.write(f_bytes)
        proc.stdin.close()
        stdout, stderr = proc.communicate(timeout=600)
    except Exception as pipe_err:
        proc.kill()
        raise RuntimeError(f"FFmpeg streaming encoding failed: {pipe_err}") from pipe_err

    if proc.returncode != 0:
        err_msg = (
            stderr.decode("utf-8", errors="replace") if stderr else f"Exit code {proc.returncode}"
        )
        raise RuntimeError(f"FFmpeg meme video encoding failed: {err_msg}")

    logger.info(
        "Rendered and encoded video in %.2fs -> %s", time.monotonic() - t_v0, video_out_path
    )

    # 5. Thumbnail Generation
    thumb_path = base_out / f"{job_id}_thumbnail.png"
    make_meme_thumbnail(
        script=script,
        out_path=thumb_path,
        aspect_ratio=config.aspect_ratio,
        visual_style=config.visual_style,
    )
    logger.info("Generated Thumbnail: %s", thumb_path)

    # 6. Quality Control Check
    qc_result: QCOutcome = MemeQC.run_full_qc(
        script=script,
        config=config,
        audio_segments=segment_audios,
        video_path=video_out_path,
        thumbnail_path=thumb_path,
        master_audio=master_audio_path,
    )

    if not qc_result.passed:
        logger.warning("Meme QC found issues: %s", qc_result.issues)
    else:
        logger.info("Meme QC Passed successfully!")

    # 7. Description & SEO Metadata
    full_description = build_description(script)

    # 8. Publishing / Upload
    youtube_url: str | None = None
    if config.publish:
        try:
            result_obj = MemeResult(
                script=script,
                video_path=video_out_path,
                thumbnail_path=thumb_path,
                title=script.title,
                description=full_description,
                hashtags=script.hashtags,
                duration=total_audio_duration,
                aspect_ratio=config.aspect_ratio,
                qc_passed=qc_result.passed,
                youtube_url=None,
            )
            youtube_url = publish_meme_video(
                result=result_obj,
                config=cfg,
                state_manager=state_manager,
            )
            logger.info("Published meme video: %s", youtube_url)
        except Exception as pub_err:
            logger.error("Publishing meme video failed: %s", pub_err)

    # 9. Cleanup temporary raw frames if requested
    if cleanup_frames and frames_dir.exists():
        try:
            shutil.rmtree(frames_dir, ignore_errors=True)
        except Exception:
            pass

    return MemeResult(
        script=script,
        video_path=video_out_path,
        thumbnail_path=thumb_path,
        title=script.title,
        description=full_description,
        hashtags=script.hashtags,
        duration=round(total_audio_duration, 2),
        aspect_ratio=config.aspect_ratio,
        qc_passed=qc_result.passed,
        youtube_url=youtube_url,
    )
