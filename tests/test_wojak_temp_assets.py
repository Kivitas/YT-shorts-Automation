"""Tests for on-demand single-image Wojak assets (no network, no disk cache)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autoshorts.longform import wojak_temp_assets as wta
from autoshorts.longform.wojak_temp_assets import (
    CATEGORIES,
    PUBLIC_FOLDER_ID,
    category_from_context,
    choose_category_with_llm,
    temporary_wojak_asset,
)

FOLDER_MIME = "application/vnd.google-apps.folder"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class FakeProvider:
    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.prompts: list[str] = []

    def complete(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.reply


def _fake_drive(
    *,
    subfolder: str = "Chads",
    files: list[dict[str, Any]] | None = None,
) -> tuple[Any, Any, list[str]]:
    """Return (listing, file_fetch, calls) emulating the public folder layout."""
    tree: dict[str, list[dict[str, Any]]] = {
        PUBLIC_FOLDER_ID: [
            {"id": "folder-chads", "name": subfolder, "mimeType": FOLDER_MIME},
            {"id": "folder-npc", "name": "NPCs", "mimeType": FOLDER_MIME},
        ],
        "folder-chads": files
        if files is not None
        else [{"id": "img-1", "name": "chad.png", "mimeType": "image/png", "size": "70"}],
        "folder-npc": [],
    }
    calls: list[str] = []

    def listing(folder_id: str) -> list[dict[str, Any]]:
        calls.append(folder_id)
        return list(tree.get(folder_id, []))

    def fetch(file_id: str) -> bytes:
        calls.append(file_id)
        return PNG_BYTES

    return listing, fetch, calls


@pytest.mark.parametrize(
    ("expression", "archetype", "gender", "build", "expected"),
    [
        (None, "chad", None, None, "Chads"),
        ("buff", None, None, None, "Chads"),
        (None, "doomer", None, None, "Doomers"),
        ("sad", None, None, None, "Doomers"),
        ("smug", None, None, None, "Soyjaks"),
        (None, "soyjak", None, None, "Soyjaks"),
        (None, "npc", None, None, "NPCs"),
        ("rage", None, None, None, "Rage wojaks"),
        ("angry", None, None, None, "Rage wojaks"),
        (None, "brainlet", None, None, "Brainlets"),
        (None, None, "female", None, "Female wojaks"),
        (None, "tradwife", None, None, "Female wojaks"),
        (None, "doomer_girl", None, None, "Female wojaks"),
        ("mask", None, None, None, "Mask wojaks"),
        ("political", None, None, None, "Political wojaks"),
    ],
)
def test_category_from_context_keyword_mapping(
    expression: str | None,
    archetype: str | None,
    gender: str | None,
    build: str | None,
    expected: str,
) -> None:
    assert category_from_context(expression, archetype, gender, build) == expected


def test_category_from_context_returns_none_without_match() -> None:
    assert category_from_context(None, None, None, None) is None
    assert category_from_context("", "", "", "") is None
    assert category_from_context("thoughtful", "philosopher", "male", "slim") is None


def test_category_from_context_only_returns_known_categories() -> None:
    samples = ["chad", "doomer", "soyjak", "npc", "rage", "brainlet", "tradwife", "mask"]
    for sample in samples:
        result = category_from_context(sample, None, None, None)
        assert result in CATEGORIES


def test_choose_category_accepts_case_and_whitespace_insensitive_match() -> None:
    assert choose_category_with_llm("a buff man flexing", FakeProvider(" chads ")) == "Chads"
    assert choose_category_with_llm("scene", FakeProvider('"female wojaks"')) == "Female wojaks"


def test_choose_category_rejects_unknown_reply() -> None:
    assert choose_category_with_llm("scene", FakeProvider("banana")) is None
    assert choose_category_with_llm("scene", FakeProvider("Chads and Doomers")) is None
    assert choose_category_with_llm("scene", FakeProvider(None)) is None


def test_choose_category_returns_none_when_provider_raises() -> None:
    provider = FakeProvider(error=RuntimeError("rate limited"))
    assert choose_category_with_llm("scene", provider) is None


def test_choose_category_prompt_lists_every_category() -> None:
    provider = FakeProvider("Chads")
    choose_category_with_llm("a buff man", provider)
    assert len(provider.prompts) == 1
    assert all(name in provider.prompts[0] for name in CATEGORIES)


def test_choose_category_returns_none_without_any_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wta, "_build_default_provider", lambda: None)
    assert choose_category_with_llm("scene") is None


def test_temporary_wojak_asset_yields_file_and_cleans_up(tmp_path: Path) -> None:
    listing, fetch, calls = _fake_drive()
    with temporary_wojak_asset(
        "Chads",
        fetch_listing=listing,
        fetch_file=fetch,
        temp_root=tmp_path,
    ) as asset:
        assert asset is not None
        assert asset.is_file()
        assert asset.name == "wojak.png"
        assert asset.read_bytes() == PNG_BYTES
        assert asset.parent.parent == tmp_path
        temp_dir = asset.parent
    assert not temp_dir.exists()
    assert list(tmp_path.iterdir()) == []
    # Exactly one listing per level and one download.
    assert calls == [PUBLIC_FOLDER_ID, "folder-chads", "img-1"]


def test_temporary_wojak_asset_cleans_up_when_body_raises(tmp_path: Path) -> None:
    listing, fetch, _ = _fake_drive()
    with pytest.raises(RuntimeError, match="boom"):
        with temporary_wojak_asset(
            "Chads", fetch_listing=listing, fetch_file=fetch, temp_root=tmp_path
        ) as asset:
            assert asset is not None
            raise RuntimeError("boom")
    assert list(tmp_path.iterdir()) == []


def test_temporary_wojak_asset_yields_none_for_unknown_category(tmp_path: Path) -> None:
    listing, fetch, calls = _fake_drive()
    with temporary_wojak_asset(
        "Banana", fetch_listing=listing, fetch_file=fetch, temp_root=tmp_path
    ) as asset:
        assert asset is None
    assert calls == []


def test_temporary_wojak_asset_yields_none_when_subfolder_missing(tmp_path: Path) -> None:
    listing, fetch, calls = _fake_drive(subfolder="Chads")
    with temporary_wojak_asset(
        "Soyjaks", fetch_listing=listing, fetch_file=fetch, temp_root=tmp_path
    ) as asset:
        assert asset is None
    assert "img-1" not in calls


def test_oversize_listing_entry_is_rejected(tmp_path: Path) -> None:
    too_big = str(wta.MAX_IMAGE_BYTES + 1)
    files = [{"id": "big", "name": "huge.png", "mimeType": "image/png", "size": too_big}]
    listing, fetch, calls = _fake_drive(files=files)
    with temporary_wojak_asset(
        "Chads", fetch_listing=listing, fetch_file=fetch, temp_root=tmp_path
    ) as asset:
        assert asset is None
    assert "big" not in calls
    assert list(tmp_path.iterdir()) == []


def test_oversize_download_is_rejected(tmp_path: Path) -> None:
    files = [{"id": "img-1", "name": "chad.png", "mimeType": "image/png"}]
    listing, _, _ = _fake_drive(files=files)

    def oversize(_file_id: str) -> bytes:
        return b"\x00" * (wta.MAX_IMAGE_BYTES + 1)

    with temporary_wojak_asset(
        "Chads", fetch_listing=listing, fetch_file=oversize, temp_root=tmp_path
    ) as asset:
        assert asset is None
    assert list(tmp_path.iterdir()) == []


def test_non_image_mime_is_rejected(tmp_path: Path) -> None:
    files = [
        {"id": "vid", "name": "clip.mp4", "mimeType": "video/mp4", "size": "10"},
        {"id": "pdf", "name": "sheet.pdf", "mimeType": "application/pdf", "size": "10"},
        {"id": "gif", "name": "anim.gif", "mimeType": "image/gif", "size": "10"},
    ]
    listing, fetch, calls = _fake_drive(files=files)
    with temporary_wojak_asset(
        "Chads", fetch_listing=listing, fetch_file=fetch, temp_root=tmp_path
    ) as asset:
        assert asset is None
    assert not any(c in {"vid", "pdf", "gif"} for c in calls)


def test_fetcher_exception_yields_none_and_cleans_up(tmp_path: Path) -> None:
    listing, _, _ = _fake_drive()

    def broken(_file_id: str) -> bytes:
        raise OSError("network down")

    with temporary_wojak_asset(
        "Chads", fetch_listing=listing, fetch_file=broken, temp_root=tmp_path
    ) as asset:
        assert asset is None
    assert list(tmp_path.iterdir()) == []


def test_browser_unavailable_yields_none(monkeypatch) -> None:
    from contextlib import contextmanager

    @contextmanager
    def _broken():
        raise RuntimeError("no browser")
        yield  # pragma: no cover

    monkeypatch.setattr(wta, "_browser_context", _broken)
    with wta.temporary_wojak_asset("Chads") as path:
        assert path is None


def test_row_to_entry_classifies_folders_and_images() -> None:
    folder = wta._row_to_entry({"id": "f1", "name": "chads", "tips": "chads Shared folder"})
    assert folder is not None and folder["mimeType"] == wta.DRIVE_FOLDER_MIME
    image = wta._row_to_entry({"id": "i1", "name": "gigachad.PNG", "tips": "Size: 117 KB"})
    assert image is not None
    assert image["mimeType"] == "image/png"
    assert image["size"] == 117 * 1024


def test_row_to_entry_skips_non_image_files() -> None:
    assert wta._row_to_entry({"id": "d1", "name": "forward.docx", "tips": "Size: 8 KB"}) is None
    assert wta._row_to_entry({"id": "", "name": "x.png", "tips": ""}) is None


def test_parse_size_units() -> None:
    assert wta._parse_size("Size: 1.5 MB") == int(1.5 * 1024 * 1024)
    assert wta._parse_size("no size here") is None


def test_fetch_category_tree_walks_nested_folders_and_writes_index(tmp_path) -> None:
    tree = {
        wta.PUBLIC_FOLDER_ID: [
            {"id": "cat1", "name": "Chads", "mimeType": wta.DRIVE_FOLDER_MIME},
            {"id": "doc1", "name": "notes.docx", "mimeType": "application/msword"},
        ],
        "cat1": [
            {"id": "img1", "name": "gigachad.png", "mimeType": "image/png", "size": 10},
            {"id": "sub1", "name": "extra", "mimeType": wta.DRIVE_FOLDER_MIME},
            {"id": "img1", "name": "gigachad.png", "mimeType": "image/png", "size": 10},
        ],
        "sub1": [{"id": "img2", "name": "deep.jpg", "mimeType": "image/jpeg", "size": 10}],
    }
    files = {"img1": b"\x89PNG" + b"0" * 20, "img2": b"\xff\xd8\xff" + b"0" * 20}

    records = wta.fetch_category_tree(
        tmp_path / "run",
        listing=lambda folder_id: tree.get(folder_id, []),
        file_fetch=lambda file_id: files[file_id],
    )

    assert {r["id"] for r in records} == {"img1", "img2"}
    by_id = {r["id"]: r for r in records}
    assert by_id["img1"]["category"] == "Chads"
    assert by_id["img2"]["subpath"] == "Chads/extra"
    for record in records:
        assert (tmp_path / "run" / record["path"]).is_file()
    import json

    index = json.loads((tmp_path / "run" / "index.json").read_text(encoding="utf-8"))
    assert len(index) == 2


def test_fetch_category_tree_skips_oversize_and_failed_downloads(tmp_path) -> None:
    tree = {
        wta.PUBLIC_FOLDER_ID: [
            {"id": "big", "name": "huge.png", "mimeType": "image/png", "size": 999_999_999},
            {"id": "ok", "name": "fine.png", "mimeType": "image/png", "size": 10},
            {"id": "bad", "name": "broken.png", "mimeType": "image/png", "size": 10},
        ]
    }

    def file_fetch(file_id):
        if file_id == "bad":
            raise OSError("network down")
        return b"\x89PNG" + b"0" * 20

    records = wta.fetch_category_tree(
        tmp_path / "run",
        listing=lambda folder_id: tree.get(folder_id, []),
        file_fetch=file_fetch,
    )
    assert [r["id"] for r in records] == ["ok"]
