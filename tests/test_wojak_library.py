"""Tests for the local Wojak character library. No network access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from autoshorts.longform import wojak_library as wl


def _png(path: Path, size: tuple[int, int] = (40, 60)) -> None:
    """Write a white image with a black square in the middle."""
    image = Image.new("RGB", size, (255, 255, 255))
    image.paste((0, 0, 0), (10, 20, 30, 40))
    image.save(path)


def _entry(entry_id: str, file: str, **tags: list[str]) -> dict[str, Any]:
    base: dict[str, list[str]] = {
        "archetype": [],
        "expression": [],
        "gender": [],
        "race": [],
        "age": [],
        "prop": [],
    }
    base.update(tags)
    return {
        "id": entry_id,
        "file": file,
        "source_url": "https://example.invalid/source",
        "source_folder": "test",
        "license_note": "test fixture",
        "tags": base,
    }


def _library(tmp_path: Path, entries: list[dict[str, Any]], files: list[str]) -> Path:
    library = tmp_path / "library"
    library.mkdir()
    for name in files:
        _png(library / name)
    (library / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")
    return library


def test_library_dir_resolves_to_project_assets() -> None:
    assert wl.LIBRARY_DIR.parts[-2:] == ("assets", "wojak_library")
    assert wl.LIBRARY_DIR.parent.parent == Path(wl.__file__).resolve().parents[3]


def test_scoring_prefers_entry_with_more_matching_tags(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [
            _entry("partial", "a.png", expression=["doomer"]),
            _entry("full", "b.png", expression=["doomer"], gender=["male"], race=["white"]),
            _entry("none", "c.png", expression=["chad"]),
        ],
        ["a.png", "b.png", "c.png"],
    )

    picked = wl.select_library_character(
        expression="Doomer",
        gender="MALE",
        race="white",
        age=None,
        archetype=None,
        library_dir=library,
    )

    assert picked == (library / "b.png").resolve()


def test_missing_tag_is_neutral_not_a_mismatch(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [_entry("untagged", "a.png"), _entry("tagged", "b.png", age=["old"])],
        ["a.png", "b.png"],
    )

    picked = wl.select_library_character(
        expression=None,
        gender=None,
        race=None,
        age="old",
        archetype=None,
        library_dir=library,
    )

    assert picked == (library / "b.png").resolve()


def test_no_match_and_empty_request_return_none(tmp_path: Path) -> None:
    library = _library(tmp_path, [_entry("a", "a.png", expression=["doomer"])], ["a.png"])

    assert (
        wl.select_library_character(
            expression="rage", gender=None, race=None, age=None, archetype=None, library_dir=library
        )
        is None
    )
    assert (
        wl.select_library_character(
            expression=None, gender=None, race=None, age=None, archetype=None, library_dir=library
        )
        is None
    )


def test_missing_manifest_returns_none(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    _png(empty / "a.png")

    assert (
        wl.select_library_character(
            expression="doomer", gender=None, race=None, age=None, archetype=None, library_dir=empty
        )
        is None
    )


def test_empty_manifest_returns_none(tmp_path: Path) -> None:
    library = _library(tmp_path, [], [])

    assert (
        wl.select_library_character(
            expression="doomer",
            gender=None,
            race=None,
            age=None,
            archetype=None,
            library_dir=library,
        )
        is None
    )


def test_entry_with_missing_file_is_skipped(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [
            _entry("ghost", "missing.png", expression=["doomer"], gender=["male"]),
            _entry("real", "real.png", expression=["doomer"]),
        ],
        ["real.png"],
    )

    picked = wl.select_library_character(
        expression="doomer",
        gender="male",
        race=None,
        age=None,
        archetype=None,
        library_dir=library,
    )

    assert picked == (library / "real.png").resolve()


def test_ties_are_deterministic_per_seed_and_vary_across_seeds(tmp_path: Path) -> None:
    names = ["a.png", "b.png", "c.png"]
    library = _library(
        tmp_path,
        [_entry(f"id-{name}", name, expression=["npc"]) for name in names],
        names,
    )

    def pick(seed: int) -> Path | None:
        return wl.select_library_character(
            expression="npc",
            gender=None,
            race=None,
            age=None,
            archetype=None,
            seed=seed,
            library_dir=library,
        )

    assert pick(7) == pick(7)
    assert pick(0) == pick(0)
    picks = {pick(seed) for seed in range(30)}
    assert len(picks) > 1


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("chad", "chad"),
        ("Buff guy", "chad"),
        ("doomer", "doomer"),
        ("sad", "doomer"),
        ("depressed", "doomer"),
        ("soyjak", "soyjak"),
        ("smug", "soyjak"),
        ("npc", "npc"),
        ("rage", "rage"),
        ("angry", "rage"),
        ("brainlet", "brainlet"),
        ("female", "female"),
        ("happy", None),
        ("", None),
        (None, None),
    ],
)
def test_archetype_from_expression(expression: str | None, expected: str | None) -> None:
    assert wl.archetype_from_expression(expression) == expected


def test_load_keys_out_white_border_and_keeps_interior_opaque(tmp_path: Path) -> None:
    path = tmp_path / "wojak.png"
    _png(path)

    image = wl.load_library_character(path)

    assert image.mode == "RGBA"
    assert image.size == (40, 60)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((39, 59))[3] == 0
    assert image.getpixel((20, 30)) == (0, 0, 0, 255)


def test_load_resizes_when_size_given(tmp_path: Path) -> None:
    path = tmp_path / "wojak.png"
    _png(path)

    image = wl.load_library_character(path, size=(20, 30))

    assert image.size == (20, 30)
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0


def test_resolve_uses_library_image_when_matched(tmp_path: Path) -> None:
    library = _library(tmp_path, [_entry("a", "a.png", expression=["rage"])], ["a.png"])

    def spec() -> Image.Image:
        raise AssertionError("spec should not be called when the library matches")

    image = wl.resolve_character_image(
        expression="rage",
        gender=None,
        race=None,
        age=None,
        archetype=None,
        spec=spec,
        library_dir=library,
    )

    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0


def test_resolve_falls_back_to_spec_when_library_empty(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    calls: list[int] = []

    def spec() -> Image.Image:
        calls.append(1)
        return Image.new("RGB", (8, 8), (255, 0, 0))

    image = wl.resolve_character_image(
        expression="doomer",
        gender=None,
        race=None,
        age=None,
        archetype=None,
        spec=spec,
        library_dir=empty,
    )

    assert calls == [1]
    assert image.mode == "RGBA"
    assert image.size == (8, 8)
    assert image.getpixel((0, 0)) == (255, 0, 0, 255)


def test_resolve_falls_back_to_spec_when_library_file_is_corrupt(tmp_path: Path) -> None:
    library = _library(tmp_path, [_entry("a", "a.png", expression=["rage"])], [])
    (library / "a.png").write_bytes(b"not an image")

    image = wl.resolve_character_image(
        expression="rage",
        gender=None,
        race=None,
        age=None,
        archetype=None,
        spec=lambda: Image.new("RGB", (4, 4), (0, 0, 255)),
        library_dir=library,
    )

    assert image.mode == "RGBA"
    assert image.size == (4, 4)
