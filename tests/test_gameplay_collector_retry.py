"""Tests for download retry and candidate skipping in collect_category_gameplay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoshorts.media import gameplay_collector as gc


def _candidates(n: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"vid{i}",
            "title": f"Title {i}",
            "url": f"https://example.com/watch?v=vid{i}",
            "channel": "chan",
        }
        for i in range(1, n + 1)
    ]


def _fake_clip(filename: str, source_id: str) -> dict[str, Any]:
    return {
        "id": Path(filename).stem,
        "filename": filename,
        "path": f"assets/backgrounds/minecraft/{filename}",
        "source_id": source_id,
        "source_title": "t",
        "source_url": "u",
        "channel": "c",
        "duration": 60.0,
        "license_note": "n",
    }


def test_skips_failed_download_until_max_videos_collected(tmp_path, monkeypatch):
    cands = _candidates(4)
    monkeypatch.setattr(gc, "search_category_videos", lambda **kw: cands)

    def fake_download(url_or_id, output_path, **kw):
        if url_or_id.endswith("vid1"):
            return None  # first candidate always fails
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x" * 2048)
        return output_path

    cut_calls: list[str] = []

    def fake_cut(source_path, video_id, **kw):
        cut_calls.append(video_id)
        return [_fake_clip(f"{video_id}_01.mp4", video_id)]

    monkeypatch.setattr(gc, "download_source_video", fake_download)
    monkeypatch.setattr(gc, "cut_video_segments", fake_cut)

    result = gc.collect_category_gameplay(category="minecraft", max_videos=2, assets_dir=tmp_path)

    assert result["videos_processed"] == 2
    assert len(cut_calls) == 2
    assert "vid1" not in cut_calls


def test_retries_failed_download_once_and_counts_candidate(tmp_path, monkeypatch):
    cands = _candidates(1)
    monkeypatch.setattr(gc, "search_category_videos", lambda **kw: cands)

    attempts: list[str] = []

    def fake_download(url_or_id, output_path, **kw):
        attempts.append(url_or_id)
        if len(attempts) == 1:
            return None  # first attempt fails
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x" * 2048)
        return output_path

    monkeypatch.setattr(gc, "download_source_video", fake_download)
    monkeypatch.setattr(
        gc,
        "cut_video_segments",
        lambda source_path, video_id, **kw: [_fake_clip(f"{video_id}_01.mp4", video_id)],
    )

    result = gc.collect_category_gameplay(category="minecraft", max_videos=1, assets_dir=tmp_path)

    assert len(attempts) == 2
    assert result["videos_processed"] == 1
    assert result["segments_created"] == 1


def test_requests_max_videos_plus_three_results(tmp_path, monkeypatch):
    seen: dict[str, Any] = {}

    def fake_search(**kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(gc, "search_category_videos", fake_search)

    gc.collect_category_gameplay(category="minecraft", max_videos=2, assets_dir=tmp_path)

    assert seen["max_results"] == 2 + 3
