"""Unit and integration tests for high-dopamine gameplay collector.

Uses mocked subprocesses to test:
- Category folder creation & normalization
- Manifest reading, updating, and atomic writing
- Quality and watermark/copyright heuristics
- yt-dlp candidate search and parsing
- Video download and FFmpeg segmentation
- Idempotency (skips existing files)
- Network failure tolerance and timeouts
- CLI argument parsing and execution
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.media.gameplay_collector import (
    DEFAULT_CATEGORY_QUERIES,
    ClipManifestEntry,
    collect_category_gameplay,
    collect_gameplay,
    cut_video_segments,
    download_source_video,
    get_categories_summary,
    is_acceptable_candidate,
    load_manifest,
    normalize_category_name,
    save_manifest,
    score_candidate,
    search_category_videos,
)


class TestCategoryNormalization:
    """Test category naming normalization and aliases."""

    def test_default_categories(self) -> None:
        assert normalize_category_name("minecraft") == "minecraft"
        assert normalize_category_name("subway") == "subway"
        assert normalize_category_name("gta") == "gta"
        assert normalize_category_name("hydraulic_press") == "hydraulic_press"
        assert normalize_category_name("slime") == "slime"
        assert normalize_category_name("temple_run") == "temple_run"
        assert normalize_category_name("car_drift") == "car_drift"

    def test_aliases(self) -> None:
        assert normalize_category_name("mc") == "minecraft"
        assert normalize_category_name("minecraft_parkour") == "minecraft"
        assert normalize_category_name("subway_surfers") == "subway"
        assert normalize_category_name("subwaysurfers") == "subway"
        assert normalize_category_name("gtav") == "gta"
        assert normalize_category_name("gta5") == "gta"
        assert normalize_category_name("kinetic_sand") == "slime"
        assert normalize_category_name("press") == "hydraulic_press"
        assert normalize_category_name("drift") == "car_drift"

    def test_none_or_empty_falls_back_to_minecraft(self) -> None:
        assert normalize_category_name(None) == "minecraft"
        assert normalize_category_name("") == "minecraft"


class TestCandidateFilteringAndScoring:
    """Test heuristic filtering for watermarks, duration, and copyrighted music."""

    def test_short_duration_rejected(self) -> None:
        acceptable, reason = is_acceptable_candidate(
            title="Minecraft Parkour Fast",
            duration=35.0,
            min_duration=60.0,
        )
        assert not acceptable
        assert "less than minimum" in reason

    def test_adequate_duration_accepted(self) -> None:
        acceptable, reason = is_acceptable_candidate(
            title="Minecraft Parkour Gameplay (Free To Use)",
            duration=120.0,
            min_duration=60.0,
        )
        assert acceptable
        assert reason == "OK"

    def test_negative_watermark_rejected(self) -> None:
        acceptable, reason = is_acceptable_candidate(
            title="GTA Ramp Stunts with Watermark",
            duration=180.0,
        )
        assert not acceptable
        assert "negative keyword" in reason.lower()

    def test_negated_watermark_accepted(self) -> None:
        acceptable, _reason = is_acceptable_candidate(
            title="Minecraft Parkour (No Watermark / Free To Use)",
            duration=180.0,
        )
        assert acceptable

    def test_copyright_music_rejected(self) -> None:
        acceptable, _reason = is_acceptable_candidate(
            title="Subway Surfers with Copyrighted Music",
            duration=180.0,
        )
        assert not acceptable

    def test_do_not_reuse_rejected(self) -> None:
        acceptable, _reason = is_acceptable_candidate(
            title="Epic Car Drift",
            description="Do not reuse or reupload this footage",
            duration=180.0,
        )
        assert not acceptable

    def test_score_candidate(self) -> None:
        low_score = score_candidate(title="Random video", duration=30.0)
        high_score = score_candidate(
            title="Minecraft Parkour 4K 60fps No Copyright Royalty Free Gameplay Vertical",
            duration=300.0,
        )
        assert high_score > low_score
        assert high_score >= 80


class TestManifestManagement:
    """Test manifest loading, saving, and format compliance."""

    def test_load_manifest_nonexistent(self, tmp_path: Path) -> None:
        manifest = load_manifest("minecraft", assets_dir=tmp_path)
        assert manifest["category"] == "minecraft"
        assert manifest["clips"] == []

    def test_save_and_reload_manifest(self, tmp_path: Path) -> None:
        clip = ClipManifestEntry(
            id="vid123_01",
            filename="vid123_01.mp4",
            path="assets/backgrounds/minecraft/vid123_01.mp4",
            source_id="vid123",
            source_title="Minecraft Parkour Gameplay",
            source_url="https://youtube.com/watch?v=vid123",
            channel="Test Channel",
            duration=60.0,
            license_note="Royalty free test note",
            segment_index=1,
        )
        manifest_data = {
            "category": "minecraft",
            "clips": [clip.to_dict()],
        }
        manifest_file = save_manifest(manifest_data, "minecraft", assets_dir=tmp_path)
        assert manifest_file.is_file()

        loaded = load_manifest("minecraft", assets_dir=tmp_path)
        assert loaded["category"] == "minecraft"
        assert len(loaded["clips"]) == 1
        assert loaded["clips"][0]["filename"] == "vid123_01.mp4"
        assert loaded["clips"][0]["source_title"] == "Minecraft Parkour Gameplay"
        assert loaded["clips"][0]["source_url"] == "https://youtube.com/watch?v=vid123"
        assert loaded["clips"][0]["channel"] == "Test Channel"
        assert loaded["clips"][0]["duration"] == 60.0
        assert loaded["clips"][0]["license_note"] == "Royalty free test note"
        assert "updated_at" in loaded and loaded["updated_at"] != ""


class TestSubprocessSearchAndDownload:
    """Test yt-dlp search and download routines with mocked subprocesses."""

    @patch("subprocess.run")
    def test_search_category_videos_success(self, mock_run: MagicMock) -> None:
        sample_stdout = (
            json.dumps({
                "id": "abc111",
                "title": "Minecraft Parkour Gameplay No Copyright Free To Use",
                "duration": 300,
                "channel": "Parkour Creator",
                "url": "https://youtube.com/watch?v=abc111",
            })
            + "\n"
            + json.dumps({
                "id": "abc222",
                "title": "Minecraft Short Clip with Watermarked Content",
                "duration": 20,
                "channel": "Shorts",
            })
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=sample_stdout, stderr="")

        candidates = search_category_videos("minecraft parkour", max_results=2)
        assert len(candidates) == 1
        assert candidates[0]["id"] == "abc111"
        assert candidates[0]["title"] == "Minecraft Parkour Gameplay No Copyright Free To Use"
        assert candidates[0]["duration"] == 300.0

    @patch("subprocess.run")
    def test_search_category_videos_timeout_handled(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=10.0)
        candidates = search_category_videos("query", max_results=2)
        assert candidates == []

    @patch("subprocess.run")
    def test_download_source_video_skips_if_exists(self, mock_run: MagicMock, tmp_path: Path) -> None:
        existing_file = tmp_path / "existing.mp4"
        existing_file.write_bytes(b"dummy video data")

        res = download_source_video("http://example.com/vid", existing_file)
        assert res == existing_file
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_download_source_video_subprocess_invoked(self, mock_run: MagicMock, tmp_path: Path) -> None:
        target = tmp_path / "downloaded.mp4"

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            target.write_bytes(b"fake mp4 content" * 100)
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run

        res = download_source_video("https://youtube.com/watch?v=testvid", target)
        assert res == target
        assert target.is_file()
        assert target.stat().st_size > 0
        mock_run.assert_called()


class TestSegmentCuttingAndIdempotency:
    """Test FFmpeg cutting into 30-90s vertical-friendly segments."""

    @patch("autoshorts.media.gameplay_collector.probe_video_duration")
    @patch("subprocess.run")
    def test_cut_video_segments_success(
        self,
        mock_run: MagicMock,
        mock_probe: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_probe.return_value = 180.0  # 3 minutes source video
        source_file = tmp_path / "source.mp4"
        source_file.write_bytes(b"sample" * 500)

        category_dir = tmp_path / "backgrounds" / "minecraft"

        def fake_ffmpeg(cmd: list[str], **kwargs: object) -> MagicMock:
            # Last arg is output path
            out = Path(cmd[-1])
            out.write_bytes(b"segment data" * 200)
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_ffmpeg

        clips = cut_video_segments(
            source_path=source_file,
            category_dir=category_dir,
            video_id="mc_vid1",
            source_title="MC Parkour",
            source_url="https://youtube.com/watch?v=mc_vid1",
            channel="MC Channel",
            segment_duration_sec=60.0,
            max_segments=2,
        )

        assert len(clips) == 2
        assert clips[0]["filename"] == "mc_vid1_01.mp4"
        assert clips[1]["filename"] == "mc_vid1_02.mp4"
        assert (category_dir / "mc_vid1_01.mp4").is_file()
        assert (category_dir / "mc_vid1_02.mp4").is_file()

    @patch("autoshorts.media.gameplay_collector.probe_video_duration")
    @patch("subprocess.run")
    def test_cut_video_segments_idempotent_skip(
        self,
        mock_run: MagicMock,
        mock_probe: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_probe.return_value = 180.0
        source_file = tmp_path / "source.mp4"
        source_file.write_bytes(b"sample" * 500)

        category_dir = tmp_path / "backgrounds" / "minecraft"
        category_dir.mkdir(parents=True)
        # Pre-create segment 1
        seg1 = category_dir / "mc_vid1_01.mp4"
        seg1.write_bytes(b"pre-existing segment" * 100)

        clips = cut_video_segments(
            source_path=source_file,
            category_dir=category_dir,
            video_id="mc_vid1",
            source_title="MC Parkour",
            source_url="https://youtube.com/watch?v=mc_vid1",
            channel="MC Channel",
            segment_duration_sec=60.0,
            max_segments=1,
        )

        assert len(clips) == 1
        assert clips[0]["filename"] == "mc_vid1_01.mp4"
        # Since it pre-existed, subprocess.run should not have been called to cut it
        mock_run.assert_not_called()


class TestCollectionWorkflowAndSummary:
    """Test full workflow and category inventory summaries."""

    @patch("autoshorts.media.gameplay_collector.search_category_videos")
    def test_collect_category_dry_run(self, mock_search: MagicMock, tmp_path: Path) -> None:
        mock_search.return_value = [
            {
                "id": "dry1",
                "title": "Subway Surfers Vertical Free To Use",
                "url": "https://youtube.com/watch?v=dry1",
                "duration": 150.0,
                "channel": "Subway Hero",
                "score": 60,
            }
        ]

        res = collect_category_gameplay(
            category="subway",
            max_videos=1,
            assets_dir=tmp_path,
            dry_run=True,
        )

        assert res["dry_run"] is True
        assert res["candidates_found"] == 1
        assert res["segments_created"] == 0

    @patch("autoshorts.media.gameplay_collector.search_category_videos")
    @patch("autoshorts.media.gameplay_collector.download_source_video")
    @patch("autoshorts.media.gameplay_collector.cut_video_segments")
    def test_collect_category_gameplay_full(
        self,
        mock_cut: MagicMock,
        mock_dl: MagicMock,
        mock_search: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_search.return_value = [
            {
                "id": "vid_gta",
                "title": "GTA V Mega Ramp Free To Use",
                "url": "https://youtube.com/watch?v=vid_gta",
                "duration": 240.0,
                "channel": "GTA Stunts",
                "score": 70,
            }
        ]
        fake_src = tmp_path / "fake_src.mp4"
        fake_src.write_bytes(b"data" * 100)
        mock_dl.return_value = fake_src

        mock_cut.return_value = [
            {
                "id": "vid_gta_01",
                "filename": "vid_gta_01.mp4",
                "path": "assets/backgrounds/gta/vid_gta_01.mp4",
                "source_id": "vid_gta",
                "source_title": "GTA V Mega Ramp Free To Use",
                "source_url": "https://youtube.com/watch?v=vid_gta",
                "channel": "GTA Stunts",
                "duration": 60.0,
                "license_note": "Royalty-free",
                "segment_index": 1,
            }
        ]

        res = collect_category_gameplay(
            category="gta",
            max_videos=1,
            assets_dir=tmp_path,
            dry_run=False,
        )

        assert res["category"] == "gta"
        assert res["videos_processed"] == 1
        assert res["segments_created"] == 1
        assert res["total_clips"] == 1

        manifest = load_manifest("gta", assets_dir=tmp_path)
        assert len(manifest["clips"]) == 1
        assert manifest["clips"][0]["filename"] == "vid_gta_01.mp4"

    @patch("autoshorts.media.gameplay_collector.collect_category_gameplay")
    def test_collect_gameplay_multi_category_failure_tolerance(
        self, mock_collect_cat: MagicMock, tmp_path: Path
    ) -> None:
        def side_effect(category: str, **kwargs: object) -> dict:
            if category == "subway":
                raise ConnectionError("Network unreachable")
            return {
                "category": category,
                "videos_processed": 1,
                "segments_created": 2,
                "total_clips": 2,
            }

        mock_collect_cat.side_effect = side_effect

        results = collect_gameplay(
            categories=["minecraft", "subway"],
            assets_dir=tmp_path,
        )

        assert "minecraft" in results
        assert results["minecraft"]["segments_created"] == 2
        assert "subway" in results
        assert "error" in results["subway"]
        assert "Network unreachable" in results["subway"]["error"]

    def test_get_categories_summary(self, tmp_path: Path) -> None:
        bg_dir = tmp_path / "backgrounds"
        (bg_dir / "minecraft").mkdir(parents=True)
        (bg_dir / "minecraft" / "clip1.mp4").write_bytes(b"content")

        summary = get_categories_summary(assets_dir=tmp_path)
        assert len(summary) >= len(DEFAULT_CATEGORY_QUERIES)

        mc_row = next(r for r in summary if r["category"] == "minecraft")
        assert mc_row["clips_count"] == 1
        assert mc_row["has_manifest"] is False


class TestCLICollectGameplay:
    """Test CLI parsing in scripts/collect_gameplay.py."""

    def test_cli_parse_args(self) -> None:
        from scripts.collect_gameplay import parse_args

        args = parse_args(["--categories", "minecraft", "subway", "--per-category", "3", "--segment-sec", "45", "--dry-run"])
        assert args.categories == ["minecraft", "subway"]
        assert args.per_category == 3
        assert args.segment_sec == 45.0
        assert args.dry_run is True
        assert args.list is False

    @patch("scripts.collect_gameplay.get_categories_summary")
    def test_cli_list_flag(self, mock_summary: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        from scripts.collect_gameplay import main

        mock_summary.return_value = [
            {
                "category": "minecraft",
                "clips_count": 2,
                "manifest_clips_count": 2,
                "has_manifest": True,
                "default_query": "minecraft parkour...",
            }
        ]

        rc = main(["--list"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "BACKGROUND GAMEPLAY CATEGORIES & INVENTORY" in captured.out
        assert "minecraft" in captured.out
