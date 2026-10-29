"""Render a long-form animated studio video: script -> TTS -> multi-stem audio -> frames -> ffmpeg mp4."""

from __future__ import annotations

import logging
import math
import struct
import subprocess
import wave
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


def _find_music_bed(music_dir: Path | None = None) -> Path | None:
    """Return the first non-empty mp3/wav in ``assets/music`` if available."""
    base = music_dir or Path("assets/music")
    if not base.is_dir():
        return None
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        for candidate in sorted(base.glob(ext)):
            try:
                if candidate.is_file() and candidate.stat().st_size > 1024:
                    return candidate
            except OSError:
                continue
    return None


def _synthesize_transition_pulse(path: Path, duration: float = 0.18, sample_rate: int = 48000) -> Path:
    """Synthesize a subtle broadcast transition whoosh/pop pulse WAV."""
    path = Path(path)
    n_samples = max(1, int(duration * sample_rate))
    buf = bytearray()
    for i in range(n_samples):
        t = i / sample_rate
        env = math.sin(math.pi * (i / n_samples)) ** 2
        freq = 180.0 + 340.0 * (1.0 - i / n_samples)
        val = int(32767 * 0.08 * env * math.sin(2.0 * math.pi * freq * t))
        buf += struct.pack("<hh", val, val)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(buf))
    return path


def _concat_audio(
    sections: list[SectionAudio],
    work_dir: Path,
    music_bed: Path | None = None,
) -> Path:
    """Join section audio with the concat *filter* and optional sidechain-ducked music bed.

    The concat demuxer needs identical codec parameters, which breaks when one section fell back
    from Edge (mp3) to Kokoro (wav). Each input is normalised to 48 kHz stereo first.
    """
    if not sections:
        raise ValueError("No audio sections to concatenate")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
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

    if music_bed is not None and Path(music_bed).is_file():
        mixed_out = work_dir / "narration_master.m4a"
        total_dur = max(0.5, sum(sa.duration for sa in sections))
        mix_cmd = [
            get_ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", str(out),
            "-stream_loop", "-1", "-i", str(Path(music_bed).resolve()),
            "-filter_complex",
            (
                f"[0:a]asplit=2[vo][sc];"
                f"[1:a]atrim=0:{total_dur:.2f},aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume=0.14[bg];"
                f"[bg][sc]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=250[ducked];"
                f"[vo][ducked]amix=inputs=2:duration=first:dropout_transition=2[master]"
            ),
            "-map", "[master]", "-c:a", "aac", "-b:a", "192k", str(mixed_out),
        ]
        mix_proc = subprocess.run(
            mix_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600, check=False,
        )
        if mix_proc.returncode == 0 and mixed_out.is_file() and mixed_out.stat().st_size > 0:
            return mixed_out
        logger.warning("Music bed sidechain mix failed; using clean narration stem.")

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
        except (BrokenPipeError, OSError):
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
    topic: str,
    minutes: float = 2.5,
    provider: str = "api",
    config=None,
    *,
    genre: str | None = None,
) -> LongformResult:
    script = build_script(topic, minutes, provider, config, genre=genre)
    return render_script(script, config=config)


def render_script(
    script: LongformScript, config=None, extra_description: str = ""
) -> LongformResult:
    """Render an already written script: narration, animated studio video, thumbnail."""
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
    thumb_path = make_thumbnail(
        script.title, out_dir / f"{job_id}_thumb.png", genre=script.genre or "auto"
    )

    music_bed = _find_music_bed()
    audio = _concat_audio(sections, work_dir, music_bed=music_bed)
    scene_key = script.genre or topic
    _encode(sections, script.title, audio, video_path, scenes_for_topic(scene_key))
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
