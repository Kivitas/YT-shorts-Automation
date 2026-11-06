"""Characterization tests for FFmpegCompositor.compose().

compose() is refactored into smaller private steps. These tests pin what it does today:
the exact ffmpeg argv and run timeouts for representative configurations, the files left
on disk afterwards, and the failure paths. Only the subprocess boundary and the environment
probes (durations, stream checks, encoder, pixel format, asset discovery) are stubbed.

Golden argv lives in tests/fixtures/compose_argv_golden.json. Paths that depend on the
temp directory are replaced with <TMP>, and backslashes become forward slashes, so the same
file matches on Windows and POSIX. Regenerate it only for an intentional behavior change:

    AUTOSHORTS_UPDATE_COMPOSE_GOLDEN=1 python -m pytest tests/test_compositor_compose_characterization.py
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from autoshorts.core.config import Settings
from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
from autoshorts.media.compositor import FFmpegCompositor
from autoshorts.media.encoder_probe import DEFAULT_CPU_ENCODER, EncoderInfo

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "compose_argv_golden.json"
UPDATE_ENV = "AUTOSHORTS_UPDATE_COMPOSE_GOLDEN"
TMP_TOKEN = "<TMP>"

QSV_ENCODER = EncoderInfo(
    codec="h264_qsv",
    extra_args=["-global_quality", "25"],
    is_hardware_accelerated=True,
    verified=True,
)


@dataclass(frozen=True)
class _Spec:
    """One representative compose() configuration. Only the fields that differ are set."""

    name: str
    job_id: str = "job_golden"
    kwargs: dict[str, Any] = field(default_factory=dict)
    edl: EditDecisionList | None = None
    background: str = "video"  # "video" (file exists), "missing" (path given, no file), "none"
    source_has_audio: bool = True
    source_has_video: bool = True
    source_duration: float = 30.0
    background_duration: float = 300.0
    encoder: EncoderInfo = DEFAULT_CPU_ENCODER
    pix_fmt: str = "yuv420p"
    music_assets: tuple[str, ...] = ()
    sfx_assets: tuple[str, ...] = ()
    subtitles_name: str = "subs.ass"
    outcomes: tuple[Any, ...] = ()


class _FakeFFmpeg:
    """Stands in for run_command_with_cleanup: records argv and timeout, writes the output.

    Each entry in `outcomes` is the return code of one call (default 0), or an exception to raise.
    Like a real encode, it writes the temporary output before it returns or raises.
    """

    def __init__(self, outcomes: tuple[Any, ...] = (), write_output: bool = True) -> None:
        self._outcomes = list(outcomes)
        self._write_output = write_output
        self.calls: list[tuple[list[str], Any]] = []

    def __call__(self, cmd: list[str], timeout: Any = None, **_kwargs: Any) -> Any:
        argv = [str(part) for part in cmd]
        self.calls.append((argv, timeout))
        outcome = self._outcomes.pop(0) if self._outcomes else 0
        if self._write_output and argv[-1].endswith(".mp4"):
            Path(argv[-1]).write_bytes(b"fake render")
        if isinstance(outcome, BaseException):
            raise outcome
        stderr = "" if outcome == 0 else "fake ffmpeg: boom"
        return subprocess.CompletedProcess(argv, outcome, stdout="", stderr=stderr)


def _normalize_argv(argv: list[str], root: Path) -> list[str]:
    """Replace the temp root in every spelling compose() can emit, then unify separators."""
    raw = str(root.resolve())
    posix = raw.replace("\\", "/")
    escaped = posix.replace(":", "\\:")  # escape_ffmpeg_filter_path form: C\:/...
    variants = sorted({raw, posix, escaped}, key=len, reverse=True)
    normalized: list[str] = []
    for arg in argv:
        text = arg
        for variant in variants:
            text = text.replace(variant, TMP_TOKEN)
        normalized.append(text.replace("\\", "/"))
    return normalized


def _compositor() -> FFmpegCompositor:
    return FFmpegCompositor(
        config=Settings(_env_file=None),
        ffmpeg_binary="fake-ffmpeg",
        encoder_preferred="libx264",
    )


@contextlib.contextmanager
def _stubbed_environment(tmp_path: Path, comp: FFmpegCompositor, fake: _FakeFFmpeg, spec: _Spec):
    """Stub probes, asset discovery, encoder and pixel-format detection, and the subprocess runner."""
    assets_root = tmp_path / "assets"
    (assets_root / "fonts").mkdir(parents=True, exist_ok=True)
    music_files = [assets_root / "music" / name for name in spec.music_assets]
    sfx_files = [assets_root / "sfx" / name for name in spec.sfx_assets]
    assets_by_folder = {"music": music_files, "sfx": sfx_files}
    durations = {"source.mp4": spec.source_duration, "gameplay.mp4": spec.background_duration}

    def _probe_duration(media_path: Any, default: float = 45.0) -> float:
        return durations.get(Path(media_path).name, default)

    def _probe_video(media_path: Any) -> bool:
        if Path(media_path).name == "source.mp4":
            return spec.source_has_video
        return True

    def _audio_assets(subfolder: str, assets_dir: Any = None) -> list[Path]:
        return list(assets_by_folder.get(subfolder, []))

    with ExitStack() as stack:
        stack.enter_context(
            patch("autoshorts.media.compositor.run_command_with_cleanup", side_effect=fake)
        )
        stack.enter_context(
            patch("autoshorts.media.compositor.probe_media_duration", side_effect=_probe_duration)
        )
        stack.enter_context(
            patch(
                "autoshorts.media.compositor.has_audio_stream",
                return_value=spec.source_has_audio,
            )
        )
        stack.enter_context(
            patch("autoshorts.media.compositor.has_video_stream", side_effect=_probe_video)
        )
        stack.enter_context(
            patch("autoshorts.media.compositor.get_assets_dir", return_value=assets_root)
        )
        stack.enter_context(
            patch("autoshorts.media.compositor.find_user_audio_assets", side_effect=_audio_assets)
        )
        stack.enter_context(
            patch(
                "autoshorts.media.compositor.pick_background_asset",
                return_value=(None, 0.0),
            )
        )
        stack.enter_context(
            patch(
                "autoshorts.media.compositor.detect_encoder_pixel_format",
                return_value=spec.pix_fmt,
            )
        )
        stack.enter_context(patch.object(comp, "_get_encoder", return_value=spec.encoder))
        yield


def _run_spec(tmp_path: Path, spec: _Spec, fake: _FakeFFmpeg) -> Any:
    """Run compose() for `spec` under the stubbed environment. Returns the result or raises."""
    src = tmp_path / "source.mp4"
    src.write_bytes(b"source bytes")
    sub = tmp_path / spec.subtitles_name
    sub.write_text("[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n", encoding="utf-8")
    out = tmp_path / "out" / "short.mp4"

    if spec.background == "video":
        background_path: Path | None = tmp_path / "gameplay.mp4"
        background_path.write_bytes(b"background bytes")
    elif spec.background == "missing":
        background_path = tmp_path / "missing_gameplay.mp4"
    else:
        background_path = None

    comp = _compositor()
    with _stubbed_environment(tmp_path, comp, fake, spec):
        return comp.compose(
            job_id=spec.job_id,
            source_clip=src,
            edl=spec.edl,
            background_path=background_path,
            subtitles_path=sub,
            output_path=out,
            **spec.kwargs,
        )


def _record(tmp_path: Path, spec: _Spec) -> dict[str, Any]:
    """Run `spec` and capture the golden view: normalized argv, timeouts, result, leftover files."""
    fake = _FakeFFmpeg(outcomes=spec.outcomes)
    result = _run_spec(tmp_path, spec, fake)
    out_dir = tmp_path / "out"
    return {
        "result": _normalize_argv([str(result)], tmp_path)[0],
        "calls": [
            {"timeout": timeout, "argv": _normalize_argv(argv, tmp_path)}
            for argv, timeout in fake.calls
        ],
        "leftover_files": sorted(p.name for p in out_dir.iterdir()),
    }


SPECS: list[_Spec] = [
    _Spec(
        name="split_50_50_bg_video_medium_glow_separator",
        kwargs={
            "dopamine_level": "medium",
            "background_offset": 12.5,
            "split_separator": "glow",
        },
    ),
    _Spec(
        name="full_blur_single_source_high_branded_music_sfx",
        kwargs={
            "layout_mode": "full_blur_bg",
            "color_grading": "vibrant",
            "watermark_text": "@AutoShorts",
            "watermark_position": "bottom_left",
            "watermark_opacity": 0.5,
            "progress_bar_position": "top",
            "progress_bar_color": "blue@0.8",
            "voice_boost": False,
            "target_lufs": -16.0,
            "dopamine_level": "high",
        },
        background="none",
        music_assets=("bed.mp3",),
        sfx_assets=("whoosh.wav",),
    ),
    _Spec(
        name="lavfi_gradient_edl_hook_trim_medium",
        kwargs={"dopamine_level": "medium", "layout_mode": "split_50_50"},
        edl=EditDecisionList(
            hook_in_sec=2.0,
            hook_out_sec=20.0,
            jump_cuts=[JumpCut(cut_start=5.0, cut_end=6.0, reason="Dead air")],
            zoom_punch_ins=[ZoomPunchIn(timestamp=3.0, duration=0.8)],
        ),
        background="none",
        source_duration=40.0,
    ),
    _Spec(
        name="audio_only_source_lavfi_portrait_none_preset",
        kwargs={
            "layout_mode": "full_blur_bg",
            "dopamine_level": "none",
            "timeout_sec": 120.0,
        },
        background="none",
        source_has_video=False,
    ),
    _Spec(
        name="explicit_bgm_path_release_override_high",
        kwargs={
            "dopamine_level": "high",
            "background_offset": 0.0,
            "bg_music_path": "music/explicit.mp3",
            "bg_music_volume": 0.25,
            "rhythm_ducking_release": 180,
        },
        music_assets=("bed.mp3",),
        sfx_assets=("whoosh.wav",),
    ),
    _Spec(
        name="job_object_bgm_and_rhythm_release_high",
        kwargs={
            "dopamine_level": "high",
            "background_offset": 0.0,
            "job": SimpleNamespace(
                bgm_path="music/job.mp3",
                bgm_volume=0.3,
                rhythm_metadata={"release_ms": 240},
            ),
        },
        sfx_assets=("whoosh.wav",),
    ),
    _Spec(
        name="hardware_qsv_nv12_pixel_format",
        kwargs={"dopamine_level": "none", "background_offset": 0.0},
        encoder=QSV_ENCODER,
        pix_fmt="nv12",
    ),
    _Spec(
        name="hardware_qsv_failure_cpu_retry",
        kwargs={"dopamine_level": "medium", "background_offset": 0.0},
        encoder=QSV_ENCODER,
        pix_fmt="yuv420p",
        outcomes=(1, 0),
    ),
    _Spec(
        name="subtitles_apostrophe_staged_copy_medium",
        kwargs={"dopamine_level": "medium", "background_offset": 0.0},
        subtitles_name="Bob's captions.ass",
    ),
    _Spec(
        name="source_longer_than_cap_low_lavfi",
        kwargs={"dopamine_level": "low"},
        background="none",
        source_duration=120.0,
    ),
    _Spec(
        name="rhythm_release_zero_counts_as_unset_high",
        kwargs={
            "dopamine_level": "high",
            "background_offset": 0.0,
            "rhythm_ducking_release": 0,
        },
        music_assets=("bed.mp3",),
    ),
    _Spec(
        name="chyron_split_seam_explicit_text_medium",
        kwargs={
            "dopamine_level": "medium",
            "background_offset": 0.0,
            "enable_chyron": True,
            "chyron_badge": "BREAKING NEWS",
            "chyron_text": "Lake level hits record high",
            "chyron_style": "broadcast_red",
        },
    ),
    _Spec(
        name="chyron_full_blur_lower_third_text_only_none",
        kwargs={
            "layout_mode": "full_blur_bg",
            "dopamine_level": "none",
            "chyron_text": "Officials confirm the closure",
        },
        background="none",
    ),
    _Spec(
        name="background_file_missing_falls_back_to_lavfi",
        kwargs={"dopamine_level": "medium", "layout_mode": "split_50_50"},
        background="missing",
    ),
]


def _load_golden() -> dict[str, Any]:
    if not GOLDEN_PATH.is_file():
        return {}
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("spec", SPECS, ids=[s.name for s in SPECS])
def test_compose_argv_matches_golden(spec: _Spec, tmp_path: Path):
    """Every representative configuration builds the same ffmpeg argv, timeouts and outputs."""
    record = _record(tmp_path, spec)

    if os.environ.get(UPDATE_ENV) == "1":
        golden = _load_golden()
        golden[spec.name] = record
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(golden, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return

    golden = _load_golden()
    assert spec.name in golden, f"No golden entry for {spec.name}; regenerate with {UPDATE_ENV}=1"
    assert record == golden[spec.name]


def test_compose_hardware_failure_retries_once_with_cpu_encoder(tmp_path: Path):
    """Readable form of the QSV failure path: one hardware attempt, then libx264 with the same tail."""
    spec = next(s for s in SPECS if s.name == "hardware_qsv_failure_cpu_retry")
    fake = _FakeFFmpeg(outcomes=spec.outcomes)
    _run_spec(tmp_path, spec, fake)

    assert len(fake.calls) == 2
    first, second = fake.calls[0][0], fake.calls[1][0]
    assert first[first.index("-c:v") + 1] == "h264_qsv"
    assert second[second.index("-c:v") + 1] == "libx264"
    assert second[second.index("-crf") + 1] == "22"
    assert second[second.index("-c:a") :] == first[first.index("-c:a") :]


def test_compose_software_failure_raises_with_stderr_and_no_retry(tmp_path: Path):
    """A non-zero exit from a CPU encode raises with the stderr tail and drops the partial file."""
    fake = _FakeFFmpeg(outcomes=(1,))
    with pytest.raises(RuntimeError, match="exit code 1") as exc_info:
        _run_spec(tmp_path, _Spec(name="software_failure"), fake)

    assert "fake ffmpeg: boom" in str(exc_info.value)
    assert len(fake.calls) == 1
    assert not list((tmp_path / "out").glob(".tmp_*"))
    assert not (tmp_path / "out" / "short.mp4").exists()


def test_compose_timeout_raises_timeout_error_and_discards_partial_render(tmp_path: Path):
    fake = _FakeFFmpeg(
        outcomes=(subprocess.TimeoutExpired(cmd=["fake-ffmpeg"], timeout=7.0),),
    )
    spec = _Spec(name="timeout", kwargs={"timeout_sec": 7.0})
    with pytest.raises(TimeoutError, match=r"exceeded timeout \(7\.0s\)"):
        _run_spec(tmp_path, spec, fake)

    assert [timeout for _, timeout in fake.calls] == [7.0]
    assert not list((tmp_path / "out").glob(".tmp_*"))
    assert not (tmp_path / "out" / "short.mp4").exists()


def test_compose_success_without_output_file_raises(tmp_path: Path):
    fake = _FakeFFmpeg(outcomes=(0,), write_output=False)
    with pytest.raises(RuntimeError, match="output file was not created"):
        _run_spec(tmp_path, _Spec(name="no_output"), fake)
    assert not (tmp_path / "out" / "short.mp4").exists()


def test_compose_rejects_missing_inputs_before_running_ffmpeg(tmp_path: Path):
    comp = _compositor()
    fake = _FakeFFmpeg()
    spec = _Spec(name="missing_inputs")
    sub = tmp_path / "subs.ass"
    sub.write_text("[Script Info]\n", encoding="utf-8")
    out = tmp_path / "out" / "short.mp4"

    with _stubbed_environment(tmp_path, comp, fake, spec):
        with pytest.raises(FileNotFoundError, match="Source clip not found"):
            comp.compose(
                job_id="job_missing_src",
                source_clip=tmp_path / "absent.mp4",
                edl=None,
                background_path=None,
                subtitles_path=sub,
                output_path=out,
            )
        src = tmp_path / "source.mp4"
        src.write_bytes(b"source")
        with pytest.raises(FileNotFoundError, match="Subtitles file not found"):
            comp.compose(
                job_id="job_missing_sub",
                source_clip=src,
                edl=None,
                background_path=None,
                subtitles_path=tmp_path / "absent.ass",
                output_path=out,
            )

    assert fake.calls == []


def test_compose_public_signature_is_unchanged():
    """compose() keeps its parameter names, order and defaults for existing callers."""
    params = inspect.signature(FFmpegCompositor.compose).parameters
    required = inspect.Parameter.empty
    observed = [(name, p.default) for name, p in params.items()]
    assert observed == [
        ("self", required),
        ("job_id", required),
        ("source_clip", required),
        ("edl", required),
        ("background_path", required),
        ("subtitles_path", required),
        ("output_path", required),
        ("dopamine_level", None),
        ("progress_callback", None),
        ("timeout_sec", 300.0),
        ("layout_mode", None),
        ("color_grading", None),
        ("watermark_text", None),
        ("watermark_position", None),
        ("watermark_opacity", None),
        ("progress_bar_position", None),
        ("progress_bar_color", None),
        ("voice_boost", None),
        ("target_lufs", None),
        ("bg_music_volume", None),
        ("crop_focus", "center"),
        ("split_separator", None),
        ("bg_music_path", None),
        ("bgm_path", None),
        ("bgm_volume", None),
        ("rhythm_ducking_release", None),
        ("background_offset", None),
        ("enable_chyron", None),
        ("chyron_badge", None),
        ("chyron_text", None),
        ("chyron_style", None),
        ("kwargs", required),
    ]
