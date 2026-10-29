"""Unit tests for background assets auto-fetcher, inventory reporting, and API endpoints."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoshorts.core.state_manager import StateManager
from autoshorts.dashboard.app import create_app
from autoshorts.media.background_fetcher import (
    CURATED_BACKGROUND_SOURCES,
    download_preset_backgrounds,
    ensure_backgrounds_available,
    get_backgrounds_inventory,
    normalize_category,
    resolve_yt_dlp_binary,
)
from autoshorts.media.dopamine_effects import pick_background_asset

# -----------------------------------------------------------------------------
# 1. Normalization & Configuration Tests
# -----------------------------------------------------------------------------


def test_normalize_category():
    """Verify category normalization maps aliases correctly."""
    assert normalize_category("minecraft") == "minecraft"
    assert normalize_category("mc") == "minecraft"
    assert normalize_category("MINECRAFT") == "minecraft"
    assert normalize_category("subway") == "subway"
    assert normalize_category("subwaysurfers") == "subway"
    assert normalize_category("subway_surfers") == "subway"
    assert normalize_category("gta") == "gta"
    assert normalize_category("gtav") == "gta"
    assert normalize_category(None) == "minecraft"
    assert normalize_category("custom_theme") == "custom_theme"


def test_curated_sources_configured():
    """Verify curated sources dictionary contains required presets."""
    assert "minecraft" in CURATED_BACKGROUND_SOURCES
    assert "subway" in CURATED_BACKGROUND_SOURCES
    assert len(CURATED_BACKGROUND_SOURCES["minecraft"]) > 0
    assert len(CURATED_BACKGROUND_SOURCES["subway"]) > 0

    for sources in CURATED_BACKGROUND_SOURCES.values():
        for src in sources:
            assert "url" in src
            assert "title" in src
            assert src["url"].startswith("http")


def test_resolve_yt_dlp_binary():
    """Verify yt-dlp binary resolution returns a non-empty string."""
    bin_name = resolve_yt_dlp_binary("yt-dlp")
    assert isinstance(bin_name, str)
    assert len(bin_name) > 0


# -----------------------------------------------------------------------------
# 2. Inventory Reporting Tests
# -----------------------------------------------------------------------------


def test_inventory_reporting_empty(tmp_path: Path):
    """Verify inventory reporting when background folders are empty."""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True)

    inv = get_backgrounds_inventory(assets_dir=assets_dir)
    assert "minecraft" in inv
    assert "subway" in inv
    assert inv["minecraft"] == []
    assert inv["subway"] == []


def test_inventory_reporting_populated(tmp_path: Path):
    """Verify inventory reporting when background folders contain media files."""
    assets_dir = tmp_path / "assets"
    mc_dir = assets_dir / "backgrounds" / "minecraft"
    mc_dir.mkdir(parents=True)

    dummy_clip = mc_dir / "minecraft_bg_01.mp4"
    dummy_clip.write_bytes(b"A" * 1024 * 1024)  # 1 MB

    with patch("autoshorts.media.background_fetcher.probe_media_duration", return_value=60.5):
        inv = get_backgrounds_inventory(assets_dir=assets_dir)

    assert len(inv["minecraft"]) == 1
    clip_info = inv["minecraft"][0]
    assert clip_info["filename"] == "minecraft_bg_01.mp4"
    assert clip_info["duration"] == 60.5
    assert clip_info["size_bytes"] == 1024 * 1024
    assert clip_info["size_mb"] == 1.0
    assert clip_info["category"] == "minecraft"
    assert str(dummy_clip.resolve()) == clip_info["path"]


# -----------------------------------------------------------------------------
# 3. Ensure Backgrounds Available Tests
# -----------------------------------------------------------------------------


def test_ensure_backgrounds_available_already_present(tmp_path: Path):
    """Verify existing clips are returned without triggering any downloads."""
    assets_dir = tmp_path / "assets"
    mc_dir = assets_dir / "backgrounds" / "minecraft"
    mc_dir.mkdir(parents=True)

    clip = mc_dir / "minecraft_bg_01.mp4"
    clip.write_bytes(b"dummy clip bytes")

    with patch("autoshorts.media.background_fetcher.download_clip_with_ytdlp") as mock_dl:
        result = ensure_backgrounds_available(
            category="minecraft", min_clips=1, assets_dir=assets_dir
        )
        assert len(result) == 1
        assert result[0] == clip
        mock_dl.assert_not_called()


def test_ensure_backgrounds_available_auto_download(tmp_path: Path):
    """Verify download is triggered when folder is empty and succeeds."""
    assets_dir = tmp_path / "assets"
    mc_dir = assets_dir / "backgrounds" / "minecraft"
    mc_dir.mkdir(parents=True)

    def fake_download(url, output_path, **kwargs):
        Path(output_path).write_bytes(b"downloaded video content")
        return Path(output_path)

    with patch(
        "autoshorts.media.background_fetcher.download_clip_with_ytdlp", side_effect=fake_download
    ) as mock_dl:
        result = ensure_backgrounds_available(
            category="minecraft", min_clips=1, assets_dir=assets_dir
        )
        assert len(result) >= 1
        assert result[0].name.startswith("minecraft_bg_")
        assert result[0].is_file()
        assert mock_dl.called


def test_ensure_backgrounds_available_handles_download_failure(tmp_path: Path):
    """Verify graceful handling when download fails (returns empty list)."""
    assets_dir = tmp_path / "assets"
    sw_dir = assets_dir / "backgrounds" / "subway"
    sw_dir.mkdir(parents=True)

    with patch(
        "autoshorts.media.background_fetcher.download_clip_with_ytdlp", return_value=None
    ) as mock_dl:
        result = ensure_backgrounds_available(category="subway", min_clips=1, assets_dir=assets_dir)
        assert result == []
        assert mock_dl.called


def test_download_preset_backgrounds(tmp_path: Path):
    """Verify download_preset_backgrounds returns path mapping for categories."""
    assets_dir = tmp_path / "assets"

    def fake_download(url, output_path, **kwargs):
        Path(output_path).write_bytes(b"video bytes")
        return Path(output_path)

    with patch(
        "autoshorts.media.background_fetcher.download_clip_with_ytdlp", side_effect=fake_download
    ):
        presets = download_preset_backgrounds(
            categories=["minecraft", "subway"], assets_dir=assets_dir
        )
        assert "minecraft" in presets
        assert "subway" in presets
        assert len(presets["minecraft"]) >= 1
        assert len(presets["subway"]) >= 1


# -----------------------------------------------------------------------------
# 4. Integration with pick_background_asset
# -----------------------------------------------------------------------------


def test_pick_background_asset_triggers_auto_fetch(tmp_path: Path):
    """Verify pick_background_asset triggers ensure_backgrounds_available on empty folder."""
    assets_dir = tmp_path / "assets"
    mc_dir = assets_dir / "backgrounds" / "minecraft"
    mc_dir.mkdir(parents=True)

    mock_clip = mc_dir / "minecraft_bg_01.mp4"

    def fake_ensure(category="minecraft", min_clips=1, assets_dir=None, **kwargs):
        mock_clip.write_bytes(b"gameplay video bytes")
        return [mock_clip]

    with patch(
        "autoshorts.media.background_fetcher.ensure_backgrounds_available", side_effect=fake_ensure
    ) as mock_ensure:
        chosen, offset = pick_background_asset(category="minecraft", assets_dir=assets_dir)
        assert chosen == mock_clip
        assert offset >= 0.0
        assert mock_ensure.called


def test_pick_background_asset_auto_category(tmp_path: Path):
    """Verify pick_background_asset handles 'auto' category when no files exist."""
    assets_dir = tmp_path / "assets"
    mc_dir = assets_dir / "backgrounds" / "minecraft"
    mc_dir.mkdir(parents=True)

    mock_clip = mc_dir / "minecraft_bg_01.mp4"

    def fake_ensure(category="minecraft", min_clips=1, assets_dir=None, **kwargs):
        mock_clip.write_bytes(b"gameplay video bytes")
        return [mock_clip]

    with patch(
        "autoshorts.media.background_fetcher.ensure_backgrounds_available", side_effect=fake_ensure
    ) as mock_ensure:
        chosen, offset = pick_background_asset(category="auto", assets_dir=assets_dir)
        assert chosen == mock_clip
        assert offset >= 0.0
        assert mock_ensure.called


def test_pick_background_asset_fallback_on_total_failure(tmp_path: Path):
    """Verify pick_background_asset falls back to (None, 0.0) if auto-fetch yields no clips."""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True)

    with patch("autoshorts.media.background_fetcher.ensure_backgrounds_available", return_value=[]):
        chosen, offset = pick_background_asset(category="minecraft", assets_dir=assets_dir)
        assert chosen is None
        assert offset == 0.0


# -----------------------------------------------------------------------------
# 5. Dashboard API Endpoints Tests
# -----------------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path: Path):
    """Create a test client with isolated state and pipeline mocks."""
    db_path = tmp_path / "test.db"
    state_mgr = StateManager(db_path=db_path)
    mock_pipe = MagicMock()
    mock_pipe.state_manager = state_mgr

    app = create_app(pipeline=mock_pipe, state_manager=state_mgr)
    return TestClient(app, base_url="http://localhost:8765")


def test_api_get_backgrounds(api_client: TestClient):
    """Verify GET /api/backgrounds returns inventory and categories."""
    resp = api_client.get("/api/backgrounds")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "inventory" in data
    assert "categories" in data
    assert "minecraft" in data["inventory"]
    assert "subway" in data["inventory"]


def test_api_post_backgrounds_download(api_client: TestClient):
    """Verify POST /api/backgrounds/download triggers background download."""
    with patch("autoshorts.media.background_fetcher.download_preset_backgrounds"):
        resp = api_client.post(
            "/api/backgrounds/download",
            json={"categories": ["minecraft", "subway"], "min_clips": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "downloading"
        assert "minecraft" in data["categories"]


def test_api_post_backgrounds_upload_success(api_client: TestClient, tmp_path: Path):
    """Verify POST /api/backgrounds/upload saves MP4 into category folder."""
    assets_dir = tmp_path / "assets"
    fake_video_bytes = b"\x00\x00\x00 ftypisom" + b"\x00" * 100

    with (
        patch("autoshorts.dashboard.app.get_assets_dir", return_value=assets_dir),
        patch("autoshorts.media.compositor.probe_media_duration", return_value=42.0),
    ):
        files = {"file": ("my_gameplay.mp4", io.BytesIO(fake_video_bytes), "video/mp4")}
        data = {"category": "minecraft"}
        resp = api_client.post("/api/backgrounds/upload", files=files, data=data)

        assert resp.status_code == 200
        res_data = resp.json()
        assert res_data["success"] is True
        assert res_data["filename"] == "my_gameplay.mp4"
        assert res_data["category"] == "minecraft"
        assert res_data["duration"] == 42.0

        saved_file = assets_dir / "backgrounds" / "minecraft" / "my_gameplay.mp4"
        assert saved_file.is_file()
        assert saved_file.read_bytes() == fake_video_bytes


def test_api_post_backgrounds_upload_invalid_extension(api_client: TestClient):
    """Verify POST /api/backgrounds/upload rejects non-video files."""
    files = {"file": ("malicious.txt", io.BytesIO(b"malicious text"), "text/plain")}
    data = {"category": "minecraft"}
    resp = api_client.post("/api/backgrounds/upload", files=files, data=data)
    assert resp.status_code == 400
    assert "video files" in resp.json()["detail"].lower()


def test_api_post_backgrounds_upload_path_traversal(api_client: TestClient):
    """Verify POST /api/backgrounds/upload rejects invalid category names with path traversal."""
    files = {"file": ("clip.mp4", io.BytesIO(b"video bytes"), "video/mp4")}
    data = {"category": "../evil"}
    resp = api_client.post("/api/backgrounds/upload", files=files, data=data)
    assert resp.status_code == 400
    assert "invalid category" in resp.json()["detail"].lower()


def _fake_ytdlp(write_bytes: bytes, returncode: int = 0):
    """subprocess.run stand-in that writes the file named after '-o'."""

    def run(cmd, **kwargs):
        out = Path(cmd[cmd.index("-o") + 1])
        out.write_bytes(write_bytes)
        return MagicMock(returncode=returncode, stdout="", stderr="boom")

    return run


def test_download_clip_discards_partial_file_on_failure(tmp_path: Path):
    from autoshorts.media import background_fetcher as bf

    dest = tmp_path / "minecraft_bg_01.mp4"
    with patch.object(bf.subprocess, "run", _fake_ytdlp(b"partial", returncode=1)):
        assert bf.download_clip_with_ytdlp("https://x/y", dest) is None
    assert list(tmp_path.iterdir()) == []


def test_download_clip_rejects_unreadable_video_and_never_caches_it(tmp_path: Path):
    from autoshorts.media import background_fetcher as bf

    dest = tmp_path / "minecraft_bg_01.mp4"
    with (
        patch.object(bf.subprocess, "run", _fake_ytdlp(b"not a video")),
        patch.object(bf, "has_video_stream", return_value=False),
    ):
        assert bf.download_clip_with_ytdlp("https://x/y", dest) is None
    assert not dest.exists() and list(tmp_path.iterdir()) == []


def test_download_clip_publishes_validated_file(tmp_path: Path):
    from autoshorts.media import background_fetcher as bf

    dest = tmp_path / "minecraft_bg_01.mp4"
    with (
        patch.object(bf.subprocess, "run", _fake_ytdlp(b"video-bytes")),
        patch.object(bf, "has_video_stream", return_value=True),
    ):
        assert bf.download_clip_with_ytdlp("https://x/y", dest) == dest.resolve()
    assert dest.read_bytes() == b"video-bytes"
    assert [p.name for p in tmp_path.iterdir()] == [dest.name]
