"""Render a long-form stick-figure video: script -> TTS -> frames -> ffmpeg mp4."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from uuid import uuid4

from autoshorts.core.paths import get_output_dir, get_work_dir
from autoshorts.longform.drawer import FPS, HEIGHT, WIDTH, render_scene_frames
from autoshorts.longform.script import build_script, description_for
from autoshorts.longform.thumbnail import make_thumbnail
from autoshorts.longform.tts import synthesize_sections
from autoshorts.longform.types import MAX_VIDEO_SECONDS, LongformResult, SectionAudio
from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)


def _concat_audio(sections: list[SectionAudio], work_dir: Path) -> Path:
    list_file = work_dir / "audio_list.txt"
    lines = []
    for sa in sections:
        p = Path(sa.audio_path).resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{p}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = work_dir / "narration.m4a"
    cmd = [
        get_ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:a", "aac", "-b:a", "192k", str(out),
    ]  # fmt: skip
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def _encode(sections: list[SectionAudio], title: str, audio: Path, out_path: Path) -> None:
    frames = render_scene_frames(sections, title)
    cmd = [
        get_ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
        "-r", str(FPS), "-i", "pipe:0", "-i", str(audio),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None and proc.stderr is not None
    try:
        for frame in frames:
            proc.stdin.write(frame)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed: {err[-800:]}")


def render_longform(
    topic: str, minutes: float = 2.5, provider: str = "api", config=None
) -> LongformResult:
    script = build_script(topic, minutes, provider, config)
    job_id = f"longform_{uuid4().hex[:10]}"
    work_dir = get_work_dir() / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    sections = synthesize_sections(script, work_dir)
    duration = float(sum(sa.duration for sa in sections))
    if duration > MAX_VIDEO_SECONDS:
        raise ValueError(f"Video duration {duration:.1f}s exceeds the {MAX_VIDEO_SECONDS:.0f}s cap")

    out_dir = get_output_dir() / "longform"
    out_dir.mkdir(parents=True, exist_ok=True)
    video_path = out_dir / f"{job_id}.mp4"
    thumb_path = make_thumbnail(script.title, out_dir / f"{job_id}_thumb.png")

    audio = _concat_audio(sections, work_dir)
    _encode(sections, script.title, audio, video_path)
    logger.info("Rendered long-form video %s (%.1fs)", video_path, duration)

    return LongformResult(
        topic=topic,
        title=script.title,
        video_path=video_path,
        thumbnail_path=thumb_path,
        duration=duration,
        description=description_for(script),
        hashtags=list(script.hashtags),
        provider=provider,
    )
