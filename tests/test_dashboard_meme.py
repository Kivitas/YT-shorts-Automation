"""Integration tests for Dashboard Meme Studio API and Web UI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from autoshorts.dashboard.app import create_app
from autoshorts.meme.types import MemeResult, MemeScript

BASE_URL = "http://localhost:8765"


def test_dashboard_html_contains_meme_studio() -> None:
    html_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "autoshorts"
        / "dashboard"
        / "templates"
        / "index.html"
    )
    js_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "autoshorts"
        / "dashboard"
        / "static"
        / "app.js"
    )

    html = html_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")

    assert 'id="meme-mode-select"' in html
    assert 'value="country_meme"' in html and 'value="types_of"' in html
    assert 'id="meme-category-select"' in html
    assert 'id="meme-preset-select"' in html
    assert 'id="meme-generate-btn"' in html
    assert "function generateMemeVideo" in js
    assert "function fetchMemeTopicIdea" in js
    assert "function onMemeModeChange" in js


def test_dashboard_api_meme_categories() -> None:
    app = create_app()
    client = TestClient(app, base_url=BASE_URL)

    res = client.get("/api/meme/categories")
    assert res.status_code == 200
    data = res.json()
    assert "categories" in data
    assert "countries" in data["categories"]
    assert "relatable" in data["categories"]
    assert "school" in data["categories"]
    assert "country_topics" in data
    assert len(data["country_topics"]) >= 5
    assert "types_of_topics" in data
    assert len(data["types_of_topics"]) >= 5
    assert data["hashtag_library_count"] == 25


def test_dashboard_api_meme_idea() -> None:
    app = create_app()
    client = TestClient(app, base_url=BASE_URL)

    res = client.get("/api/meme/idea?category=school&mode=types_of")
    assert res.status_code == 200
    data = res.json()
    assert "topic" in data
    assert len(data["topic"]) > 3
    assert data["category"] == "school"


def test_dashboard_api_meme_quota_and_list() -> None:
    app = create_app()
    client = TestClient(app, base_url=BASE_URL)

    q_res = client.get("/api/meme/quota")
    assert q_res.status_code == 200
    q_data = q_res.json()
    assert "cap" in q_data
    assert "used_today" in q_data
    assert "remaining_today" in q_data

    l_res = client.get("/api/meme")
    assert l_res.status_code == 200
    l_data = l_res.json()
    assert "files" in l_data


@patch("autoshorts.meme.render_meme")
def test_dashboard_api_create_meme_video(mock_render: MagicMock, tmp_path: Path) -> None:
    dummy_video = tmp_path / "meme.mp4"
    dummy_video.write_bytes(b"mp4")
    dummy_thumb = tmp_path / "thumb.png"
    dummy_thumb.write_bytes(b"png")

    mock_script = MemeScript(
        mode="country_meme",
        category="countries",
        topic="Every Country's Public Transport",
        title="Every Country's Public Transport Be Like",
        hook="Which train are you catching?",
        intro="Let's take a ride across the world!",
        segments=[],
        ending="Which country did we miss?",
        hashtags=["#CountryMemes", "#Shorts", "#AnimeMemes"],
        estimated_duration=45.0,
    )

    mock_render.return_value = MemeResult(
        script=mock_script,
        video_path=dummy_video,
        thumbnail_path=dummy_thumb,
        title=mock_script.title,
        description="Description",
        hashtags=mock_script.hashtags,
        duration=45.0,
        aspect_ratio="9:16",
        qc_passed=True,
        youtube_url="https://youtu.be/dummy123",
    )

    app = create_app()
    client = TestClient(app, base_url=BASE_URL)

    payload = {
        "mode": "country_meme",
        "topic": "Every Country's Public Transport",
        "category": "countries",
        "segment_count": 6,
        "aspect_ratio": "9:16",
        "visual_style": "anime_color",
        "publish": False,
    }

    res = client.post("/api/meme", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["title"] == "Every Country's Public Transport Be Like"
    assert data["aspect_ratio"] == "9:16"
    assert data["qc_passed"] is True
    assert data["youtube_url"] == "https://youtu.be/dummy123"
