"""Phase 0 gameplay background tests: validity rule, manifest keys, fallback logging."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.media import background_fetcher
from autoshorts.media.background_fetcher import (
    MIN_BACKGROUND_BYTES,
    is_valid_background_file,
    repair_manifest_backgrounds,
)
from autoshorts.media.compositor import FFmpegCompositor
from autoshorts.pipeline.stages_media import select_gameplay_background

REAL_SIZE = b"0" * MIN_BACKGROUND_BYTES
PLACEHOLDER = b"placeholder"


def _write_clip(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_validity_rule_rejects_small_files_without_probing(tmp_path: Path):
    tiny = _write_clip(tmp_path / "tiny.mp4", PLACEHOLDER)
    with patch.object(background_fetcher, "has_video_stream", return_value=True) as probe:
        assert is_valid_background_file(tiny) is False
        probe.assert_not_called()
        assert is_valid_background_file(tmp_path / "missing.mp4") is False


def test_validity_rule_requires_video_stream(tmp_path: Path):
    big = _write_clip(tmp_path / "big.mp4", REAL_SIZE)
    with patch.object(background_fetcher, "has_video_stream", return_value=False):
        assert is_valid_background_file(big) is False
    with patch.object(background_fetcher, "has_video_stream", return_value=True):
        assert is_valid_background_file(big) is True


def test_select_reads_clips_manifest_key(tmp_path: Path):
    assets = tmp_path / "assets"
    bg_dir = assets / "backgrounds" / "minecraft"
    _write_clip(bg_dir / "good_01.mp4", REAL_SIZE)
    _write_clip(bg_dir / "bad_01.mp4", PLACEHOLDER)
    manifest = {
        "category": "minecraft",
        "clips": [{"filename": "good_01.mp4"}, {"filename": "bad_01.mp4"}],
    }
    (bg_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with patch.object(background_fetcher, "has_video_stream", return_value=True):
        chosen, offset = select_gameplay_background(
            category="minecraft", assets_dir=assets, job_index=0
        )

    assert chosen == bg_dir / "good_01.mp4"
    assert 0.0 <= offset <= 120.0


def test_select_none_category_returns_no_background(tmp_path: Path):
    assert select_gameplay_background(category="none", assets_dir=tmp_path) == (None, 0.0)


def test_repair_redownloads_only_invalid_manifest_entries(tmp_path: Path):
    assets = tmp_path / "assets"
    bg_dir = assets / "backgrounds" / "minecraft"
    _write_clip(bg_dir / "ok_01.mp4", REAL_SIZE)
    _write_clip(bg_dir / "ph_02.mp4", PLACEHOLDER)
    manifest = {
        "clips": [
            {"filename": "ok_01.mp4", "source_url": "https://example.test/a", "segment_index": 1},
            {"filename": "ph_02.mp4", "source_url": "https://example.test/b", "segment_index": 2},
        ]
    }
    (bg_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    calls: list[dict] = []

    def fake_download(url, output_path, section_start, section_end, **kwargs):
        calls.append(
            {
                "url": url,
                "output": Path(output_path).name,
                "start": section_start,
                "end": section_end,
            }
        )
        return None

    with (
        patch.object(background_fetcher, "has_video_stream", return_value=True),
        patch.object(background_fetcher, "download_clip_with_ytdlp", side_effect=fake_download),
    ):
        repair_manifest_backgrounds("minecraft", assets_dir=assets)

    assert calls == [
        {"url": "https://example.test/b", "output": "ph_02.mp4", "start": "01:00", "end": "02:00"}
    ]


def _run_compose(tmp_path: Path, background: Path | None, offset: float | None, has_video):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"dummy source")
    subs = tmp_path / "subs.ass"
    subs.write_text("[Script Info]\n", encoding="utf-8")
    out = tmp_path / "out.mp4"
    captured: list[list[str]] = []

    def mock_run(cmd, *args, **kwargs):
        captured.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"rendered")
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        proc.stdout = ""
        return proc

    kwargs = {"background_offset": offset} if offset is not None else {}
    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=300.0),
        patch("autoshorts.media.compositor.has_audio_stream", return_value=True),
        patch("autoshorts.media.compositor.has_video_stream", side_effect=has_video),
        patch("autoshorts.media.compositor.pick_background_asset", return_value=(None, 0.0)),
    ):
        FFmpegCompositor().compose(
            job_id="job_phase0",
            source_clip=src,
            edl=None,
            background_path=background,
            subtitles_path=subs,
            output_path=out,
            dopamine_level="medium",
            **kwargs,
        )
    return captured[-1]


def test_compose_warns_with_path_and_reason_on_gradient_fallback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    bad_bg = _write_clip(tmp_path / "bg.mp4", REAL_SIZE)
    src = tmp_path / "source.mp4"

    def has_video(p):
        return Path(p).resolve() == src.resolve()

    with caplog.at_level(logging.WARNING, logger="autoshorts.media.compositor"):
        cmd = _run_compose(tmp_path, bad_bg, None, has_video)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        bad_bg.name in r.getMessage() and "no video stream" in r.getMessage() for r in warnings
    )
    assert "lavfi" in cmd


def test_compose_uses_background_offset_from_selection(tmp_path: Path):
    bg = _write_clip(tmp_path / "bg.mp4", REAL_SIZE)
    cmd = _run_compose(tmp_path, bg, 12.5, lambda p: True)
    assert "12.500" in cmd
