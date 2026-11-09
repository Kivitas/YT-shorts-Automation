"""Read-only API exposing the Wojak resource links collected for the character library.

The links live in ``assets/research/wojak_resource_links.txt`` (one URL per line,
``#`` lines are comments). Nothing here fetches the URLs; the licence/usage notes
are a fixed table maintained by hand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter

router = APIRouter()

# Subfolders of the public Drive folder that holds the Wojak character library.
# These are the categories the owner sorted the character images into.
WOJAK_CATEGORIES: list[str] = [
    "Brainiaks",
    "Brainlets",
    "Chads",
    "Doomers",
    "Female wojaks",
    "Mask wojaks",
    "More characters",
    "NPCs",
    "Political wojaks",
    "Rage wojaks",
    "Regular wojaks",
    "Soyjaks",
]

# Host (without "www.") -> licence/usage note shown next to each source.
_HOST_NOTES: dict[str, str] = {
    "drive.google.com": "public folder, no licence stated, use at your own risk",
    "pngimg.com": "CC BY-NC 4.0, non-commercial, attribution required",
    "web.archive.org": "archived copy, no licence stated",
    "reddit.com": "robots.txt disallows crawling; paste links manually",
    "wojak-studio.com": "robots.txt disallows all; not fetched",
    "pngmart.com": "personal use only; not fetched",
    "wojakdb.com": "CC BY-NC badge only",
}
_DEFAULT_NOTE = "no licence checked"


def links_file_path() -> Path:
    """Return the path of the Wojak resource links file under the project root."""
    return Path(__file__).resolve().parents[3] / "assets" / "research" / "wojak_resource_links.txt"


def _host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _kind_of(host: str) -> str:
    if host == "drive.google.com":
        return "google_drive"
    if host == "photos.google.com":
        return "google_photos"
    if host == "web.archive.org":
        return "web_archive"
    return "image_site"


def _read_urls(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return []
    urls: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


@router.get("/api/wojak/resources")
def list_wojak_resources() -> dict[str, Any]:
    """List the Wojak resource links with their kind and licence note."""
    sources: list[dict[str, str]] = []
    for url in _read_urls(links_file_path()):
        host = _host_of(url)
        sources.append(
            {
                "url": url,
                "host": host,
                "kind": _kind_of(host),
                "note": _HOST_NOTES.get(host, _DEFAULT_NOTE),
            }
        )
    return {"count": len(sources), "sources": sources}


@router.get("/api/wojak/resources/categories")
def list_wojak_categories() -> list[str]:
    """Return the static Wojak category list.

    These are subfolders of a public Google Drive folder, not fetched at runtime.
    """
    return list(WOJAK_CATEGORIES)
