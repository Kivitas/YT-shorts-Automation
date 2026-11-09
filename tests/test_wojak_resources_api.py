from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autoshorts.dashboard import wojak_resources_api
from autoshorts.dashboard.wojak_resources_api import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_resources_counts_and_classifies_links(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    links = tmp_path / "wojak_resource_links.txt"
    links.write_text(
        "# comment line\n"
        "\n"
        "https://drive.google.com/drive/folders/abc\n"
        "https://photos.google.com/share/xyz\n"
        "https://web.archive.org/web/2024/https://example.org/\n"
        "https://www.reddit.com/r/WojakCompass/comments/1/\n"
        "https://pngimg.com/images/memes/wojak\n"
        "https://unknown-site.example/page\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(wojak_resources_api, "links_file_path", lambda: links)

    body = client.get("/api/wojak/resources").json()

    assert body["count"] == 6
    sources = {s["url"]: s for s in body["sources"]}
    assert len(sources) == 6

    drive = sources["https://drive.google.com/drive/folders/abc"]
    assert drive["host"] == "drive.google.com"
    assert drive["kind"] == "google_drive"
    assert drive["note"] == "public folder, no licence stated, use at your own risk"

    photos = sources["https://photos.google.com/share/xyz"]
    assert photos["kind"] == "google_photos"
    assert photos["note"] == "no licence checked"

    archive = sources["https://web.archive.org/web/2024/https://example.org/"]
    assert archive["kind"] == "web_archive"
    assert archive["note"] == "archived copy, no licence stated"

    reddit = sources["https://www.reddit.com/r/WojakCompass/comments/1/"]
    assert reddit["host"] == "reddit.com"
    assert reddit["kind"] == "image_site"
    assert reddit["note"] == "robots.txt disallows crawling; paste links manually"

    pngimg = sources["https://pngimg.com/images/memes/wojak"]
    assert pngimg["kind"] == "image_site"
    assert pngimg["note"] == "CC BY-NC 4.0, non-commercial, attribution required"

    other = sources["https://unknown-site.example/page"]
    assert other["kind"] == "image_site"
    assert other["note"] == "no licence checked"


def test_missing_file_returns_empty_list(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "does_not_exist.txt"
    monkeypatch.setattr(wojak_resources_api, "links_file_path", lambda: missing)

    response = client.get("/api/wojak/resources")

    assert response.status_code == 200
    assert response.json() == {"count": 0, "sources": []}


def test_categories_returns_twelve_items(client: TestClient) -> None:
    response = client.get("/api/wojak/resources/categories")

    assert response.status_code == 200
    categories = response.json()
    assert len(categories) == 12
    assert "Brainiaks" in categories
    assert "Soyjaks" in categories
