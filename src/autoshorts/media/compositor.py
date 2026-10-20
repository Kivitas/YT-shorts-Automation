"""Deterministic FFmpeg video compositor, preview generator, and audio extraction engine.

Strictly uses FFmpeg and FFprobe subprocess calls (zero MoviePy):
- Implements autoshorts.core.contracts.Compositor
- 9:16 vertical split-screen (1080x1920)
- Hardware probed encoding (h264_qsv or libx264 veryfast)
- Subtitles with Windows path escaping
- Background loops with random offsets or procedural lavfi fallback
- 60-second duration hard cap
- Progress reporting and timeout protection
- Atomic .tmp write then os.replace
- 540x960 15fps fast preview generator
- 16kHz mono WAV extraction helper
- Lazy-imported faster-whisper wrapper implementing contracts.Transcriber
"""

from __future__ import annotations

import logging
import os
import random
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from autoshorts.core.config import Settings, load_config
from autoshorts.core.models import EditDecisionList
from autoshorts.core.paths import get_assets_dir
from autoshorts.media.dopamine_effects import (
    build_color_grading_filter,
    build_complex_filtergraph,
    build_watermark_filter,
    find_user_audio_assets,
    get_dopamine_preset,
    pick_background_asset,
)
from autoshorts.media.downloader import run_command_with_cleanup, terminate_process_tree
from autoshorts.media.encoder_probe import EncoderInfo, probe_encoder

logger = logging.getLogger(__name__)


def get_ffmpeg_path() -> str:
    """Resolve FFmpeg executable path.

    Checks system PATH first; if not found, checks for bundled executable from
    the imageio_ffmpeg package. Falls back to 'ffmpeg'.
    """
    resolved = shutil.which("ffmpeg")
    if resolved:
        return resolved
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    except Exception:
        pass
    return "ffmpeg"


def probe_media_duration(media_path: Path | str, default: float = 45.0) -> float:
    """Query duration of a media file in seconds using ffprobe."""
    target = Path(media_path).resolve()
    if not target.is_file():
        return default

    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return default

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(target),
    ]
    try:
        proc = run_command_with_cleanup(
            cmd,
            timeout=10.0,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            val = float(proc.stdout.strip())
            return val if val > 0 else default
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.debug("ffprobe duration probe failed for %s: %s", target, exc)

    return default


def has_audio_stream(media_path: Path | str) -> bool:
    """Return True if media file contains at least one audio stream."""
    target = Path(media_path).resolve()
    if not target.is_file():
        return False

    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return True  # Optimistic assumption

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(target),
    ]
    try:
        proc = run_command_with_cleanup(
            cmd,
            timeout=10.0,
        )
        return "audio" in proc.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return True


def extract_16khz_mono_wav(
    input_path: Path | str,
    output_wav: Path | str,
    start_sec: float | None = None,
    duration_sec: float | None = None,
    ffmpeg_binary: str = "ffmpeg",
    timeout_sec: float = 60.0,
) -> Path:
    """Extract a 16kHz 16-bit mono PCM WAV file from video or audio using FFmpeg."""
    src = Path(input_path).resolve()
    dest = Path(output_wav).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not src.is_file():
        raise FileNotFoundError(f"Input file not found for WAV extraction: {src}")

    target_bin = get_ffmpeg_path() if ffmpeg_binary == "ffmpeg" else ffmpeg_binary
    cmd = [target_bin, "-y"]
    if start_sec is not None and start_sec > 0:
        cmd.extend(["-ss", f"{start_sec:.3f}"])
    cmd.extend(["-i", str(src)])
    if duration_sec is not None and duration_sec > 0:
        cmd.extend(["-t", f"{duration_sec:.3f}"])

    # 16kHz, mono, 16-bit pcm
    cmd.extend(
        [
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(dest),
        ]
    )

    try:
        proc = run_command_with_cleanup(
            cmd,
            timeout=timeout_sec,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"FFmpeg 16kHz WAV extraction failed (code {proc.returncode}):\n{proc.stderr}"
            )
        return dest
    except FileNotFoundError as exc:
        raise RuntimeError(f"FFmpeg binary '{ffmpeg_binary}' not found on system PATH.") from exc


class FasterWhisperTranscriber:
    """Wrapper around faster-whisper int8 CPU transcription implementing contracts.Transcriber.

    Imports faster-whisper lazily so modules can load without strict whisper dependencies.
    """

    def __init__(
        self,
        model_name: str | None = None,
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: int = 4,
    ):
        settings = load_config()
        self.model_name = model_name or settings.media.whisper_model or "base.en"
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Install it with 'pip install faster-whisper' or 'uv pip install -e .[whisper]'"
            ) from exc

        logger.info(
            "Loading faster-whisper model '%s' (device=%s, compute_type=%s, threads=%d)",
            self.model_name,
            self.device,
            self.compute_type,
            self.cpu_threads,
        )
        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
        )
        return self._model

    def transcribe(self, audio_path: Path) -> list[dict[str, Any]]:
        """Transcribe an audio file into timed segments with word timestamps."""
        model = self._get_model()
        segments, _info = model.transcribe(
            str(audio_path),
            word_timestamps=True,
            vad_filter=True,
        )
        results: list[dict[str, Any]] = []
        for seg in segments:
            words_list = []
            if seg.words:
                for w in seg.words:
                    words_list.append(
                        {
                            "word": w.word,
                            "start": float(w.start),
                            "end": float(w.end),
                            "probability": float(w.probability),
                        }
                    )
            results.append(
                {
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "text": seg.text.strip(),
                    "words": words_list,
                }
            )
        return results

    def align_words(
        self,
        audio_path: Path,
        text: str | None = None,
    ) -> list[dict[str, Any]]:
        """Extract precise word-level start and end timestamps for karaoke subtitle styling."""
        segments = self.transcribe(audio_path)
        words: list[dict[str, Any]] = []
        for seg in segments:
            words.extend(seg.get("words", []))
        return words


class FFmpegCompositor:
    """High-efficiency deterministic FFmpeg compositor for vertical 9:16 YouTube Shorts.

    Implements autoshorts.core.contracts.Compositor.
    """

    def __init__(
        self,
        config: Settings | None = None,
        encoder_preferred: str = "auto",
        ffmpeg_binary: str = "ffmpeg",
    ):
        self.config = config or load_config()
        self.ffmpeg_binary = get_ffmpeg_path() if ffmpeg_binary == "ffmpeg" else ffmpeg_binary
        self.encoder_preferred = encoder_preferred or self.config.media.preferred_encoder

    def _get_encoder(self) -> EncoderInfo:
        """Resolve and verify the video encoder."""
        return probe_encoder(
            preferred=self.encoder_preferred,
            ffmpeg_binary=self.ffmpeg_binary,
        )

    def compose(
        self,
        job_id: str,
        source_clip: Path,
        edl: EditDecisionList | None,
        background_path: Path | str | None,
        subtitles_path: Path | str,
        output_path: Path | str,
        dopamine_level: str | None = None,
        progress_callback: Callable[[float], None] | None = None,
        timeout_sec: float = 300.0,
        layout_mode: str | None = None,
        color_grading: str | None = None,
        watermark_text: str | None = None,
        watermark_position: str | None = None,
        watermark_opacity: float | None = None,
        progress_bar_position: str | None = None,
        progress_bar_color: str | None = None,
        voice_boost: bool | None = None,
        target_lufs: float | None = None,
        bg_music_volume: float | None = None,
        **kwargs: Any,
    ) -> Path:
        """Render a vertical 1080x1920 Short MP4 via FFmpeg executing the EDL.

        Atomic write: renders to .tmp.mp4 first then renames via os.replace.
        """
        src = Path(source_clip).resolve()
        sub_path = Path(subtitles_path).resolve()
        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not src.is_file():
            raise FileNotFoundError(f"Source clip not found: {src}")
        if not sub_path.is_file():
            raise FileNotFoundError(f"Subtitles file not found: {sub_path}")

        # Resolve studio styling defaults from config branding / audio mastering
        branding = getattr(self.config, "branding", None)
        audio_cfg = getattr(self.config, "audio_mastering", None)
        job_defaults = getattr(self.config, "job_defaults", None)

        eff_layout = layout_mode or getattr(job_defaults, "layout_mode", None) or "split_50_50"
        eff_color_grading = color_grading or getattr(branding, "color_grading", None) or "none"
        eff_watermark_text = (
            watermark_text
            if watermark_text is not None
            else getattr(branding, "watermark_text", None)
        )
        eff_watermark_pos = (
            watermark_position or getattr(branding, "watermark_position", None) or "top_right"
        )
        eff_watermark_opacity = (
            watermark_opacity
            if watermark_opacity is not None
            else getattr(branding, "watermark_opacity", 0.65)
        )
        eff_pb_pos = (
            progress_bar_position or getattr(branding, "progress_bar_position", None) or "bottom"
        )
        eff_pb_color = (
            progress_bar_color or getattr(branding, "progress_bar_color", None) or "red@0.8"
        )
        eff_voice_boost = (
            voice_boost if voice_boost is not None else getattr(audio_cfg, "voice_boost", True)
        )
        eff_target_lufs = (
            target_lufs if target_lufs is not None else getattr(audio_cfg, "target_lufs", -14.0)
        )
        eff_bg_music_vol = (
            bg_music_volume
            if bg_music_volume is not None
            else getattr(audio_cfg, "bg_music_volume", 0.18)
        )

        # Determine target duration and trim points
        clip_duration = probe_media_duration(src)
        hook_in = 0.0
        hook_out = clip_duration

        if edl:
            if edl.hook_in_sec > 0:
                hook_in = edl.hook_in_sec
            if edl.hook_out_sec > hook_in:
                hook_out = min(clip_duration, edl.hook_out_sec)

        target_duration = max(1.0, min(60.0, hook_out - hook_in))
        logger.info(
            "Job %s: Rendering short with target duration %.2fs (trim: %.2fs to %.2fs, layout=%s)",
            job_id,
            target_duration,
            hook_in,
            hook_out,
            eff_layout,
        )

        level = dopamine_level or self.config.job_defaults.dopamine_level
        preset = get_dopamine_preset(level)
        encoder = self._get_encoder()

        # Input 0: Source clip with trim
        cmd = [self.ffmpeg_binary, "-y"]
        if hook_in > 0:
            cmd.extend(["-ss", f"{hook_in:.3f}"])
        cmd.extend(["-t", f"{target_duration:.3f}", "-i", str(src)])

        # Single source vs multi-source layouts
        is_single_source = eff_layout in ("full_blur_bg", "full_center_crop")
        has_bg_file = False
        next_input_idx = 1 if is_single_source else 2

        if not is_single_source:
            # Input 1: Background video or procedural lavfi
            bg_file: Path | None = None
            bg_offset = 0.0

            if background_path:
                p = Path(background_path).resolve()
                if p.is_file():
                    bg_file = p
                    bg_dur = probe_media_duration(p)
                    max_offset = max(0.0, bg_dur - target_duration)
                    bg_offset = random.uniform(0.0, min(180.0, max_offset))

            if bg_file is None:
                # Try to pick from assets directory based on EDL or config
                bg_style = (
                    (edl.background_style_recommendation if edl else None)
                    or self.config.job_defaults.background_category
                    or "minecraft"
                )
                bg_file, bg_offset = pick_background_asset(category=bg_style)

            if bg_file and bg_file.is_file():
                cmd.extend(
                    [
                        "-ss",
                        f"{bg_offset:.3f}",
                        "-stream_loop",
                        "-1",
                        "-t",
                        f"{target_duration:.3f}",
                        "-i",
                        str(bg_file),
                    ]
                )
                has_bg_file = True
            else:
                # Procedural fallback background via lavfi color/gradients
                cmd.extend(
                    [
                        "-f",
                        "lavfi",
                        "-t",
                        f"{target_duration:.3f}",
                        "-i",
                        f"color=c=0x16213e:s=1080x960:r=30:d={target_duration:.3f}",
                    ]
                )
                has_bg_file = False

        # Optional audio assets (music, sfx)
        music_idx: int | None = None
        sfx_idx: int | None = None

        if preset.enable_music_ducking:
            music_files = find_user_audio_assets("music")
            if music_files:
                chosen_music = random.choice(music_files)
                cmd.extend(
                    ["-stream_loop", "-1", "-t", f"{target_duration:.3f}", "-i", str(chosen_music)]
                )
                music_idx = next_input_idx
                next_input_idx += 1

        if preset.enable_sfx:
            sfx_files = find_user_audio_assets("sfx")
            if sfx_files:
                chosen_sfx = random.choice(sfx_files)
                cmd.extend(["-i", str(chosen_sfx)])
                sfx_idx = next_input_idx
                next_input_idx += 1

        has_src_audio = has_audio_stream(src)
        fonts_dir = get_assets_dir() / "fonts"
        fonts_param = fonts_dir if fonts_dir.is_dir() else None

        # Build complex filtergraph
        filtergraph, out_v, out_a = build_complex_filtergraph(
            source_clip_has_audio=has_src_audio,
            subtitles_path=sub_path,
            duration_sec=target_duration,
            dopamine_preset=preset,
            edl=edl,
            fonts_dir=fonts_param,
            has_background_video=has_bg_file,
            music_input_index=music_idx,
            sfx_input_index=sfx_idx,
            layout_mode=eff_layout,
            color_grading=eff_color_grading,
            watermark_text=eff_watermark_text,
            watermark_position=eff_watermark_pos,
            watermark_opacity=eff_watermark_opacity,
            progress_bar_position=eff_pb_pos,
            progress_bar_color=eff_pb_color,
            voice_boost=eff_voice_boost,
            target_lufs=eff_target_lufs,
            bg_music_volume=eff_bg_music_vol,
        )

        cmd.extend(["-filter_complex", filtergraph])
        cmd.extend(["-map", out_v, "-map", out_a])

        # Video encoding parameters
        cmd.extend(encoder.get_ffmpeg_args())

        # Audio encoding parameters
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "44100"])

        # Hard cap at 60s and shortest stream
        cmd.extend(["-t", f"{target_duration:.3f}", "-shortest"])

        # Temporary atomic destination
        tmp_dest = dest.with_name(f".tmp_{job_id}_{dest.name}")
        cmd.append(str(tmp_dest))

        logger.debug("Executing FFmpeg command: %s", " ".join(cmd))
        start_time = time.time()

        try:
            proc = run_command_with_cleanup(
                cmd,
                timeout=timeout_sec,
            )
            elapsed = time.time() - start_time
            if proc.returncode != 0:
                if tmp_dest.is_file():
                    tmp_dest.unlink(missing_ok=True)
                raise RuntimeError(
                    f"FFmpeg compositing failed (exit code {proc.returncode}):\n{proc.stderr}"
                )

            # Atomic rename on success
            if not tmp_dest.is_file():
                raise RuntimeError(f"FFmpeg completed but output file was not created: {tmp_dest}")

            os.replace(tmp_dest, dest)
            logger.info("Compositing job %s completed in %.1fs -> %s", job_id, elapsed, dest)
            return dest

        except subprocess.TimeoutExpired as exc:
            if tmp_dest.is_file():
                tmp_dest.unlink(missing_ok=True)
            raise TimeoutError(
                f"FFmpeg compositing for job {job_id} exceeded timeout ({timeout_sec}s)"
            ) from exc
        except Exception:
            if tmp_dest.is_file():
                tmp_dest.unlink(missing_ok=True)
            raise

    def generate_preview(
        self,
        source_clip: Path | str,
        output_path: Path | str,
        max_duration_sec: float = 30.0,
        timeout_sec: float = 30.0,
        layout_mode: str | None = None,
        color_grading: str | None = None,
        watermark_text: str | None = None,
        watermark_position: str | None = None,
        watermark_opacity: float | None = None,
        **kwargs: Any,
    ) -> Path:
        """Render a rapid 540x960 15fps low-resolution preview MP4 in <6s for dashboard scrub."""
        src = Path(source_clip).resolve()
        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not src.is_file():
            raise FileNotFoundError(f"Source clip not found for preview: {src}")

        tmp_dest = dest.with_name(f".tmp_prev_{dest.name}")

        vf_filters = ["scale=540:960:force_original_aspect_ratio=increase,crop=540:960,fps=15"]
        if color_grading and color_grading != "none":
            cg = build_color_grading_filter(color_grading)
            if cg:
                vf_filters.append(cg)
        if watermark_text:
            wm = build_watermark_filter(
                watermark_text=watermark_text,
                position=watermark_position or "top_right",
                opacity=watermark_opacity or 0.65,
                width=540,
                height=960,
            )
            if wm:
                vf_filters.append(wm)

        vf_arg = ",".join(vf_filters)

        # Fast 540x960 15fps with ultrafast preset
        cmd = [
            self.ffmpeg_binary,
            "-y",
            "-t",
            f"{max_duration_sec:.2f}",
            "-i",
            str(src),
            "-vf",
            vf_arg,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(tmp_dest),
        ]

        try:
            proc = run_command_with_cleanup(
                cmd,
                timeout=timeout_sec,
            )
            if proc.returncode != 0:
                if tmp_dest.is_file():
                    tmp_dest.unlink(missing_ok=True)
                raise RuntimeError(
                    f"FFmpeg preview generation failed (code {proc.returncode}):\n{proc.stderr}"
                )

            os.replace(tmp_dest, dest)
            return dest
        except Exception:
            if tmp_dest.is_file():
                tmp_dest.unlink(missing_ok=True)
            raise


__all__ = [
    "FFmpegCompositor",
    "FasterWhisperTranscriber",
    "extract_16khz_mono_wav",
    "get_ffmpeg_path",
    "has_audio_stream",
    "probe_media_duration",
    "run_command_with_cleanup",
    "terminate_process_tree",
]
