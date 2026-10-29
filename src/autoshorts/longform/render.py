"""Render a long-form cartoon video: script -> TTS -> frames -> ffmpeg mp4."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from uuid import uuid4

from autoshorts.core.paths import get_output_dir, get_work_dir
from autoshorts.longform.drawer import FPS, HEIGHT, WIDTH, render_scene_frames, scenes_for_topic
from autoshorts.longform.script import build_script, description_for
from autoshorts.longform.thumbnail import make_thumbnail
from autoshorts.longform.tts import synthesize_sections
from autoshorts.longform.types import (
    MAX_VIDEO_SECONDS,
    LongformResult,
    LongformScript,
    SectionAudio,
)
from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)


def _concat_audio(sections: list[SectionAudio], work_dir: Path) -> Path:
    """Join section audio with the concat *filter*.

    The concat demuxer needs identical codec parameters, which breaks when one section fell back
    from Edge (mp3) to Kokoro (wav). Each input is normalised to 48 kHz stereo first.
    """
    if not sections:
        raise ValueError("No audio sections to concatenate")
    out = work_dir / "narration.m4a"
    cmd = [get_ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
    parts = []
    for i, sa in enumerate(sections):
        cmd += ["-i", str(Path(sa.audio_path).resolve())]
        parts.append(
            f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
        )
    labels = "".join(f"[a{i}]" for i in range(len(sections)))
    graph = ";".join(parts) + f";{labels}concat=n={len(sections)}:v=0:a=1[outa]"
    cmd += ["-filter_complex", graph, "-map", "[outa]", "-c:a", "aac", "-b:a", "192k", str(out)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        raise RuntimeError(f"audio concat failed: {(proc.stderr or '').strip()[-800:]}")
    return out


def _encode(
    sections: list[SectionAudio], title: str, audio: Path, out_path: Path, scenes: tuple[str, ...]
) -> None:
    frames = render_scene_frames(sections, title, scenes=scenes)
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
        try:
            for frame in frames:
                proc.stdin.write(frame)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        err = proc.stderr.read().decode("utf-8", "replace")
        rc = proc.wait()
    except BaseException:
        # A frame generator error or Ctrl+C must not leave ffmpeg running or the output half-written.
        proc.kill()
        proc.wait()
        Path(out_path).unlink(missing_ok=True)
        raise
    if rc != 0:
        Path(out_path).unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed: {err[-800:]}")


def render_longform(
    topic: str, minutes: float = 2.5, provider: str = "api", config=None
) -> LongformResult:
    script = build_script(topic, minutes, provider, config)
    return render_script(script, config=config)


def render_script(
    script: LongformScript, config=None, extra_description: str = ""
) -> LongformResult:
    """Render an already written script: narration, stick-figure video, thumbnail."""
    topic = script.topic
    provider = script.provider
    job_id = f"longform_{uuid4().hex[:10]}"
    work_dir = get_work_dir() / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    sections = synthesize_sections(script, work_dir)
    duration = float(sum(sa.duration for sa in sections))
    if not sections or duration <= 0:
        raise ValueError("Long-form script produced no narration audio")
    if duration > MAX_VIDEO_SECONDS:
        raise ValueError(f"Video duration {duration:.1f}s exceeds the {MAX_VIDEO_SECONDS:.0f}s cap")

    out_dir = get_output_dir() / "longform"
    out_dir.mkdir(parents=True, exist_ok=True)
    video_path = out_dir / f"{job_id}.mp4"
    thumb_path = make_thumbnail(script.title, out_dir / f"{job_id}_thumb.png")

    audio = _concat_audio(sections, work_dir)
    _encode(sections, script.title, audio, video_path, scenes_for_topic(topic))
    logger.info("Rendered long-form video %s (%.1fs)", video_path, duration)

    description = description_for(script)
    if extra_description:
        description = description + "\n\n" + extra_description

    return LongformResult(
        topic=topic,
        title=script.title,
        video_path=video_path,
        thumbnail_path=thumb_path,
        duration=duration,
        description=description,
        hashtags=list(script.hashtags),
        provider=provider,
    )
