"""Tests for free-licence country photo sourcing. All HTTP is mocked; no network access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from autoshorts.longform import country_photos as cp

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 2048
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048


def _page(
    title: str,
    *,
    licence: str = "CC BY-SA 4.0",
    mime: str = "image/jpeg",
    width: int = 2000,
    artist: str = '<a href="https://example.org/u">Jane &amp; Co</a>',
    index: int = 1,
) -> dict[str, Any]:
    name = title.replace("File:", "")
    return {
        "title": title,
        "index": index,
        "imageinfo": [
            {
                "url": f"https://upload.wikimedia.org/full/{name}",
                "descriptionurl": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
                "thumburl": f"https://upload.wikimedia.org/thumb/{name}",
                "thumbwidth": min(width, cp.THUMB_WIDTH),
                "thumbheight": 800,
                "width": width,
                "height": 1000,
                "mime": mime,
                "extmetadata": {
                    "LicenseShortName": {"value": licence},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                    "Artist": {"value": artist},
                },
            }
        ],
    }


class _FakeCommons:
    """Routes API and file requests to canned responses and counts file downloads."""

    def __init__(self, pages: list[dict[str, Any]], body: bytes | None = None) -> None:
        self.pages = pages
        self.body = body  # optional override for every file response
        self.api_calls: list[dict[str, str]] = []
        self.file_gets: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers.get("user-agent") == "AutoShorts/1.0 (free-licence photo sourcing)"
        if request.url.host == "commons.wikimedia.org":
            self.api_calls.append(dict(request.url.params))
            return httpx.Response(200, json={"query": {"pages": self.pages}})
        if request.url.host == "upload.wikimedia.org":
            self.file_gets.append(str(request.url))
            is_png = str(request.url).endswith(".png")
            ctype = "image/png" if is_png else "image/jpeg"
            default = PNG_BYTES if is_png else JPEG_BYTES
            return httpx.Response(
                200, content=self.body or default, headers={"content-type": ctype}
            )
        raise AssertionError(f"unexpected host in test: {request.url}")


def _client(fake: _FakeCommons) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fake), headers=cp._HEADERS)


def _offline(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"network must not be used: {request.url}")


# --- licence filter -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "family"),
    [
        ("CC0", "CC0"),
        ("CC0 1.0", "CC0"),
        ("Public domain", "Public domain"),
        ("CC BY 4.0", "CC BY"),
        ("CC BY 2.0", "CC BY"),
        ("CC BY-SA 4.0", "CC BY-SA"),
        ("CC BY-SA 3.0", "CC BY-SA"),
        ("cc-by-sa-2.5", "CC BY-SA"),
    ],
)
def test_licence_accepted(value: str, family: str) -> None:
    assert cp.classify_licence(value) == family


@pytest.mark.parametrize(
    "value",
    [
        "CC BY-NC-SA 4.0",
        "CC BY-NC 2.0",
        "CC BY-ND 4.0",
        "CC BY-NC-ND 3.0",
        "Fair use",
        "All rights reserved",
        "Unknown",
        "",
        None,
    ],
)
def test_licence_rejected(value: str | None) -> None:
    assert cp.classify_licence(value) is None


def test_candidate_rejects_nc_nd_and_fair_use() -> None:
    for licence_name in ("CC BY-NC-SA 4.0", "CC BY-ND 4.0", "Fair use"):
        assert cp._candidate_from_page(_page("File:A.jpg", licence=licence_name)) is None


def test_candidate_accepts_cc_by_sa_with_attribution() -> None:
    candidate = cp._candidate_from_page(_page("File:Hospital_front.jpg"))
    assert candidate is not None
    assert candidate["licence_family"] == "CC BY-SA"
    assert candidate["licence_short"] == "CC BY-SA 4.0"
    assert candidate["artist"] == "Jane & Co"  # HTML stripped, entities decoded
    assert candidate["page_url"] == "https://commons.wikimedia.org/wiki/File:Hospital_front.jpg"
    assert "Jane & Co" in candidate["attribution"]
    assert "CC BY-SA 4.0" in candidate["attribution"]
    assert candidate["page_url"] in candidate["attribution"]


def test_candidate_rejects_unknown_artist_license_missing() -> None:
    page = _page("File:B.jpg")
    page["imageinfo"][0]["extmetadata"] = {}
    assert cp._candidate_from_page(page) is None


# --- size and mime filter -------------------------------------------------------------------


def test_size_filter_requires_1280_wide() -> None:
    assert cp._candidate_from_page(_page("File:Small.jpg", width=1279)) is None
    assert cp._candidate_from_page(_page("File:Exact.jpg", width=1280)) is not None


def test_mime_filter_accepts_jpeg_and_png_only() -> None:
    assert cp._candidate_from_page(_page("File:P.png", mime="image/png")) is not None
    assert cp._candidate_from_page(_page("File:G.gif", mime="image/gif")) is None
    assert cp._candidate_from_page(_page("File:T.tif", mime="image/tiff")) is None


# --- search, manifest and cache -------------------------------------------------------------


def test_search_downloads_writes_manifest_and_cache_hit_is_offline(tmp_path: Path) -> None:
    fake = _FakeCommons(
        [
            _page("File:Canada hospital.jpg", index=1),
            _page("File:Nonfree.jpg", licence="CC BY-NC 4.0", index=2),
            _page("File:Tiny.jpg", width=800, index=3),
            _page("File:Fair.png", licence="Fair use", mime="image/png", index=4),
            _page("File:Second.png", licence="CC0", mime="image/png", index=5),
        ]
    )
    with _client(fake) as client:
        records = cp.search_country_photos(
            "Canada", ["hospital"], limit=5, root=tmp_path, client=client
        )

    assert [r["title"] for r in records] == ["File:Canada hospital.jpg", "File:Second.png"]
    assert len(fake.file_gets) == 2
    for record in records:
        assert Path(record["path"]).is_file()
        assert Path(record["path"]).stat().st_size == record["bytes"]

    manifest_path = tmp_path / "canada" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["country"] == "Canada"
    assert {f["licence_short"] for f in manifest["files"]} == {"CC BY-SA 4.0", "CC0"}
    assert all(f["attribution"] and f["licence_url"] is not None for f in manifest["files"])
    assert "Canada hospital" in manifest["searches"]

    # Cache hit: the manifest answers the same search with no HTTP at all.
    offline = httpx.Client(transport=httpx.MockTransport(_offline))
    cached = cp.search_country_photos(
        "Canada", ["hospital"], limit=5, root=tmp_path, client=offline
    )
    assert [r["title"] for r in cached] == [r["title"] for r in records]
    assert len(fake.api_calls) == 1
    assert len(fake.file_gets) == 2


def test_file_already_on_disk_with_matching_size_is_not_redownloaded(tmp_path: Path) -> None:
    fake = _FakeCommons([_page("File:Keep.jpg")])
    with _client(fake) as client:
        cp.search_country_photos("Canada", "hospital", 1, root=tmp_path, client=client)
        # Drop the search cache entry so the API is queried again; the file must be reused.
        manifest_path = tmp_path / "canada" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["searches"] = {}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        records = cp.search_country_photos("Canada", "hospital", 1, root=tmp_path, client=client)

    assert len(fake.api_calls) == 2
    assert len(fake.file_gets) == 1
    assert len(records) == 1


def test_size_mismatch_forces_redownload(tmp_path: Path) -> None:
    fake = _FakeCommons([_page("File:Broken.jpg")])
    with _client(fake) as client:
        first = cp.search_country_photos("Canada", "hospital", 1, root=tmp_path, client=client)
        Path(first[0]["path"]).write_bytes(b"truncated")
        manifest_path = tmp_path / "canada" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["searches"] = {}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        cp.search_country_photos("Canada", "hospital", 1, root=tmp_path, client=client)

    assert len(fake.file_gets) == 2


def test_download_rejects_payload_that_is_not_the_declared_image(tmp_path: Path) -> None:
    fake = _FakeCommons([_page("File:Fake.jpg")], body=b"<html>not an image</html>")
    with _client(fake) as client:
        records = cp.search_country_photos("Canada", "hospital", 1, root=tmp_path, client=client)
    assert records == []
    assert not any((tmp_path / "canada").glob("*.part"))


def test_network_failure_returns_empty_without_raising(tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with httpx.Client(transport=httpx.MockTransport(boom)) as client:
        records = cp.search_country_photos("Canada", "hospital", 3, root=tmp_path, client=client)
    assert records == []


def test_limit_zero_or_blank_country_makes_no_request(tmp_path: Path) -> None:
    client = httpx.Client(transport=httpx.MockTransport(_offline))
    assert cp.search_country_photos("Canada", "hospital", 0, root=tmp_path, client=client) == []
    assert cp.search_country_photos("  ", "hospital", 3, root=tmp_path, client=client) == []


# --- pick_photo -----------------------------------------------------------------------------


def _seed_library(root: Path) -> None:
    fake = _FakeCommons(
        [
            _page("File:One.jpg", index=1),
            _page("File:Two.png", mime="image/png", index=2),
            _page("File:Three.jpg", index=3),
        ]
    )
    with _client(fake) as client:
        cp.search_country_photos("New Zealand", ["clinic"], 3, root=root, client=client)


def test_pick_photo_is_deterministic_for_a_seed(tmp_path: Path) -> None:
    _seed_library(tmp_path)
    first = cp.pick_photo("New Zealand", 42, root=tmp_path)
    assert first is not None and first.is_file()
    assert first.parent == (tmp_path / "new-zealand").resolve()
    assert cp.pick_photo("New Zealand", 42, root=tmp_path) == first


def test_pick_photo_varies_across_seeds(tmp_path: Path) -> None:
    _seed_library(tmp_path)
    picks = {cp.pick_photo("New Zealand", seed, root=tmp_path) for seed in range(30)}
    assert len(picks) > 1


def test_pick_photo_returns_none_when_empty(tmp_path: Path) -> None:
    assert cp.pick_photo("Canada", 1, root=tmp_path) is None


def test_pick_photo_returns_none_when_files_are_missing(tmp_path: Path) -> None:
    _seed_library(tmp_path)
    for path in (tmp_path / "new-zealand").glob("*.jpg"):
        path.unlink()
    for path in (tmp_path / "new-zealand").glob("*.png"):
        path.unlink()
    assert cp.pick_photo("New Zealand", 7, root=tmp_path) is None


def test_pick_photo_never_raises_on_corrupt_manifest(tmp_path: Path) -> None:
    folder = tmp_path / "canada"
    folder.mkdir()
    (folder / "manifest.json").write_text("{not json", encoding="utf-8")
    assert cp.pick_photo("Canada", 1, root=tmp_path) is None
    assert cp.pick_photo("", 1, root=tmp_path) is None
