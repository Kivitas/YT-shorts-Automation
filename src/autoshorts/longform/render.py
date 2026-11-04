"""Render a long-form animated studio video: script -> TTS -> multi-stem audio -> frames -> ffmpeg mp4."""

from __future__ import annotations

import json
import logging
import math
import re
import struct
import subprocess
import wave
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from autoshorts.core.paths import get_output_dir, get_work_dir
from autoshorts.longform.drawer import FPS, HEIGHT, WIDTH, render_scene_frames, scenes_for_topic
from autoshorts.longform.script import build_script, description_for
from autoshorts.longform.sfx import (
    mix_events,
    synth_alarm,
    synth_clock_ticks,
    synth_glass_crack,
    synth_punch,
    synth_whisper,
    synth_whoosh,
)
from autoshorts.longform.thumbnail import make_thumbnail
from autoshorts.longform.tts import SECTION_GAP_SECONDS, synthesize_sections
from autoshorts.longform.types import (
    MAX_VIDEO_SECONDS,
    LongformResult,
    LongformScript,
    SectionAudio,
)
from autoshorts.media.audio_master import normalize_two_pass
from autoshorts.media.encoder_probe import get_ffmpeg_path

# Level of the diegetic SFX stem under the narration (narration stays at unity).
SFX_GAIN = 0.35
# Integrated loudness target for the final longform audio (EBU R128).
LOUDNESS_TARGET_LUFS = -14.0
# Gain applied to the music bed inside EDL ``bgm_ducks`` spans (multiplies the bed level).
BGM_DUCK_GAIN = 0.3
# Name of the EDL file (same naming as workflow_optimizer's snapshots) read for bgm_ducks.
EDL_FILENAME = "edl.json"
_SFX_SAMPLE_RATE = 48000
_SKEPTIC_PATTERN = re.compile(r"\b(not|lazy)\b", re.IGNORECASE)
_MIRROR_CRACK_WORDS = ("mirror", "reflection", "shatter", "excuse", "can't", "quit")
_WHOOSH_WORDS = ("punch", "strike", "fight", "bag", "climb", "mountain", "sprint", "run")

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


def _synthesize_transition_pulse(
    path: Path, duration: float = 0.18, sample_rate: int = 48000
) -> Path:
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


# Silence inserted between sections so each narration beat breathes. Sections are not trimmed:
# word timings are relative to each section's start, so trimming would desync the captions.
NARRATION_COMPRESSOR = "acompressor=threshold=0.1:ratio=3:attack=20:release=250"


def _narration_filter_graph(count: int) -> str:
    """Build the filter_complex graph: per-section resample, gaps, concat, light compression.

    Input ``i`` is section ``i``. Output label is ``[outc]``. Pure string builder, no ffmpeg call.
    """
    if count < 1:
        raise ValueError("count must be at least 1")
    parts = []
    for i in range(count):
        parts.append(
            f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
        )
    labels: list[str] = []
    for i in range(count):
        labels.append(f"[a{i}]")
        if i < count - 1:
            parts.append(
                f"anullsrc=sample_rate=48000:channel_layout=stereo,"
                f"atrim=0:{SECTION_GAP_SECONDS:.2f}[g{i}]"
            )
            labels.append(f"[g{i}]")
    n_segments = len(labels)
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={n_segments}:v=0:a=1[cat]"
    return graph + f";[cat]{NARRATION_COMPRESSOR}[outc]"


def _section_starts(sections: list[SectionAudio]) -> list[float]:
    """Start time of each section on the narration timeline, including the inter-section gaps."""
    starts: list[float] = []
    cursor = 0.0
    for i, sa in enumerate(sections):
        starts.append(cursor)
        cursor += float(sa.duration)
        if i < len(sections) - 1:
            cursor += SECTION_GAP_SECONDS
    return starts


def build_sfx_track(sections: list[SectionAudio], work_dir: Path) -> Path | None:
    """Synthesize diegetic effects aligned to the narration and mix them into one WAV.

    Returns ``None`` when there is nothing to play. Otherwise writes ``work_dir/sfx_track.wav``
    spanning the full narration timeline (sections plus gaps).
    """
    if not sections:
        return None
    work_dir = Path(work_dir)
    sfx_dir = work_dir / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)

    starts = _section_starts(sections)
    events: list[tuple[Path, float]] = []

    def add(kind: str, idx: int, synth, offset: float, **kwargs) -> None:
        target = sfx_dir / f"{kind}_{idx:03d}_{len(events):03d}.wav"
        res = synth(target, **kwargs)
        if isinstance(res, (Path, str)):
            path = Path(res)
        else:
            path = target
        events.append((path, offset))

    for i, sa in enumerate(sections):
        start = starts[i]
        add("punch", i, synth_punch, start + 0.0)

        section = sa.section
        text = f"{section.heading} {section.narration}".lower()
        if section.speaker == "skeptic" or _SKEPTIC_PATTERN.search(text):
            add("whisper", i, synth_whisper, start + 0.2)

        scheduled_crack = False
        scheduled_whoosh = False

        for word in sa.words:
            raw_token = str(word.get("word", "")).strip().lower()
            token = re.sub(r"[^\w']", "", raw_token)
            at = start + float(word.get("start", 0.0))
            if token.startswith("clock") or token.startswith("alarm"):
                add("ticks", i, synth_clock_ticks, at, duration=2.0)
                add("alarm", i, synth_alarm, at)
            if any(token == kw or token.startswith(kw) for kw in _MIRROR_CRACK_WORDS):
                add("glass_crack", i, synth_glass_crack, at)
                scheduled_crack = True
            if any(token == kw or token.startswith(kw) for kw in _WHOOSH_WORDS):
                add("whoosh", i, synth_whoosh, at)
                scheduled_whoosh = True

        if not scheduled_crack:
            for kw in _MIRROR_CRACK_WORDS:
                if re.search(rf"\b{re.escape(kw)}\b", text):
                    add("glass_crack", i, synth_glass_crack, start + 0.3)
                    break

        if not scheduled_whoosh:
            for kw in _WHOOSH_WORDS:
                if re.search(rf"\b{re.escape(kw)}\b", text):
                    add("whoosh", i, synth_whoosh, start + 0.4)
                    break

    if not events:
        return None

    total_seconds = starts[-1] + float(sections[-1].duration)
    return mix_events(events, work_dir / "sfx_track.wav", total_seconds)


def load_bgm_ducks(edl_path: Path | None) -> list[tuple[float, float]]:
    """Read ``bgm_ducks`` spans from an EDL JSON file as ``(start, end)`` seconds.

    Uses the field names workflow_optimizer writes (``start_sec`` / ``end_sec``). Returns ``[]``
    when the file is missing or unreadable, so the music bed keeps its current mix.
    """
    if edl_path is None or not Path(edl_path).is_file():
        return []
    try:
        data = json.loads(Path(edl_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read EDL %s for bgm_ducks: %s", edl_path, exc)
        return []
    raw = data.get("bgm_ducks") if isinstance(data, dict) else None
    spans: list[tuple[float, float]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start_sec", item.get("start", 0.0)))
            end = float(item.get("end_sec", item.get("end", 0.0)))
        except (TypeError, ValueError):
            continue
        if end > start >= 0:
            spans.append((start, end))
    return sorted(spans)


def _duck_enable_expression(spans: Sequence[tuple[float, float]]) -> str:
    """Build the ffmpeg ``enable`` expression that is true inside any of ``spans``.

    Example: ``[(1, 2.5), (4, 5)]`` -> ``between(t,1.00,2.50)+between(t,4.00,5.00)``.
    """
    return "+".join(f"between(t,{start:.2f},{end:.2f})" for start, end in spans)


def _loudness_normalise(audio: Path, work_dir: Path) -> Path:
    """Two-pass EBU R128 normalise the final narration/music mix to ``LOUDNESS_TARGET_LUFS``.

    Returns the normalised file, or the unnormalised input if normalisation fails. Never raises.
    """
    dst = Path(work_dir) / "narration_loudnorm.m4a"
    try:
        normalize_two_pass(Path(audio), dst, LOUDNESS_TARGET_LUFS)
    except Exception as exc:  # loudness is polish; never break the render over it
        logger.warning(
            "Two-pass loudness normalisation failed; keeping unnormalised audio: %s", exc
        )
        return Path(audio)
    return dst if dst.is_file() and dst.stat().st_size > 0 else Path(audio)


def _concat_audio(
    sections: list[SectionAudio],
    work_dir: Path,
    music_bed: Path | None = None,
    sfx_track: Path | None = None,
    bgm_ducks: Sequence[tuple[float, float]] | None = None,
) -> Path:
    """Join section audio with the concat *filter*, optional SFX stem and optional ducked music bed.

    The concat demuxer needs identical codec parameters, which breaks when one section fell back
    from Edge (mp3) to Kokoro (wav). Each input is normalised to 48 kHz stereo first.
    """
    if not sections:
        raise ValueError("No audio sections to concatenate")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "narration.m4a"
    cmd = [get_ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
    for sa in sections:
        cmd += ["-i", str(Path(sa.audio_path).resolve())]
    graph = _narration_filter_graph(len(sections))
    cmd += ["-filter_complex", graph, "-map", "[outc]", "-c:a", "aac", "-b:a", "192k", str(out)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        raise RuntimeError(f"audio concat failed: {(proc.stderr or '').strip()[-800:]}")

    if sfx_track is not None and Path(sfx_track).is_file():
        sfx_out = work_dir / "narration_sfx.m4a"
        sfx_cmd = [
            get_ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", str(out),
            "-i", str(Path(sfx_track).resolve()),
            "-filter_complex",
            (
                f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"volume={SFX_GAIN}[fx];"
                f"[0:a][fx]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[sfxmix]"
            ),
            "-map", "[sfxmix]", "-c:a", "aac", "-b:a", "192k", str(sfx_out),
        ]  # fmt: skip
        sfx_proc = subprocess.run(
            sfx_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600, check=False,
        )  # fmt: skip
        if sfx_proc.returncode == 0 and sfx_out.is_file() and sfx_out.stat().st_size > 0:
            out = sfx_out
        else:
            logger.warning("SFX mix failed; using narration without effects.")

    if music_bed is not None and Path(music_bed).is_file():
        mixed_out = work_dir / "narration_master.m4a"
        total_dur = max(0.5, sum(sa.duration for sa in sections))
        duck_chain = ""
        if bgm_ducks:
            duck_chain = f",volume={BGM_DUCK_GAIN}:enable='{_duck_enable_expression(bgm_ducks)}'"
        mix_cmd = [
            get_ffmpeg_path(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(out),
            "-stream_loop",
            "-1",
            "-i",
            str(Path(music_bed).resolve()),
            "-filter_complex",
            (
                f"[0:a]asplit=2[vo][sc];"
                f"[1:a]atrim=0:{total_dur:.2f},aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume=0.14{duck_chain}[bg];"
                f"[bg][sc]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=250[ducked];"
                f"[vo][ducked]amix=inputs=2:duration=first:dropout_transition=2[master]"
            ),
            "-map",
            "[master]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(mixed_out),
        ]
        mix_proc = subprocess.run(
            mix_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
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
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
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
    try:
        sfx_track = build_sfx_track(sections, work_dir)
    except Exception:  # SFX are optional polish; never fail the render over them.
        logger.warning("SFX track build failed; rendering without effects.", exc_info=True)
        sfx_track = None
    bgm_ducks = load_bgm_ducks(work_dir / EDL_FILENAME)
    audio = _concat_audio(
        sections, work_dir, music_bed=music_bed, sfx_track=sfx_track, bgm_ducks=bgm_ducks
    )
    audio = _loudness_normalise(audio, work_dir)
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
