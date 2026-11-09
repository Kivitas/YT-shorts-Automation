"""Local Wojak character library.

Real Wojak images live in assets/wojak_library/ and are indexed by manifest.json, a JSON
list of entries tagged by archetype, expression, gender, race, age and prop. A scene's
context is matched against those tags; the best-scoring image wins, with a seeded
hash breaking ties so the same scene always gets the same character.

Loaded images have their flat background keyed out to alpha so they composite cleanly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

LIBRARY_DIR: Final[Path] = Path(__file__).resolve().parents[3] / "assets" / "wojak_library"
MANIFEST_NAME: Final[str] = "manifest.json"
BG_TOLERANCE: Final[int] = 24

_ARCHETYPE_KEYWORDS: Final[dict[str, str]] = {
    "chad": "chad",
    "buff": "chad",
    "doomer": "doomer",
    "sad": "doomer",
    "depressed": "doomer",
    "soyjak": "soyjak",
    "smug": "soyjak",
    "npc": "npc",
    "rage": "rage",
    "angry": "rage",
    "brainlet": "brainlet",
    "female": "female",
}


def archetype_from_expression(expression: str | None) -> str | None:
    """Map a free-text expression to a library archetype, or None if no keyword matches."""
    if not expression:
        return None
    for token in re.findall(r"[a-z]+", expression.lower()):
        if token in _ARCHETYPE_KEYWORDS:
            return _ARCHETYPE_KEYWORDS[token]
    return None


def _normalise(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip().lower()
    return cleaned or None


def _load_manifest(library_dir: Path) -> list[dict[str, Any]]:
    manifest = library_dir / MANIFEST_NAME
    if not manifest.is_file():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read Wojak library manifest %s: %s", manifest, exc)
        return []
    if not isinstance(data, list):
        logger.warning("Wojak library manifest %s is not a JSON list", manifest)
        return []
    return [
        entry
        for entry in data
        if isinstance(entry, dict)
        and isinstance(entry.get("id"), str)
        and isinstance(entry.get("file"), str)
    ]


def _entry_tag_values(entry: dict[str, Any], field: str) -> set[str]:
    tags = entry.get("tags")
    if not isinstance(tags, dict):
        return set()
    raw = tags.get(field)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return set()
    return {str(value).strip().lower() for value in raw if str(value).strip()}


def select_library_character(
    *,
    expression: str | None,
    gender: str | None,
    race: str | None,
    age: str | None,
    archetype: str | None,
    seed: int = 0,
    library_dir: Path | None = None,
) -> Path | None:
    """Pick the library image whose tags best match the requested scene context.

    Each requested field scores one point when the entry carries that value in its tags
    (case-insensitive). An entry missing a tag is neutral. Only entries with a score above
    zero whose file exists are candidates. Ties are broken by sha256(f"{seed}:{id}").

    Returns:
        Absolute path to the selected image, or None if nothing matches.
    """
    directory = LIBRARY_DIR if library_dir is None else Path(library_dir)
    requested = {
        field: value
        for field, value in (
            ("expression", _normalise(expression)),
            ("gender", _normalise(gender)),
            ("race", _normalise(race)),
            ("age", _normalise(age)),
            ("archetype", _normalise(archetype)),
        )
        if value is not None
    }
    if not requested:
        return None

    candidates: list[tuple[int, str, Path]] = []
    for entry in _load_manifest(directory):
        score = sum(
            1 for field, value in requested.items() if value in _entry_tag_values(entry, field)
        )
        if score <= 0:
            continue
        path = (directory / entry["file"]).resolve()
        if not path.is_file():
            continue
        digest = hashlib.sha256(f"{seed}:{entry['id']}".encode()).hexdigest()
        candidates.append((-score, digest, path))

    if not candidates:
        return None
    return min(candidates)[2]


def _key_out_background(rgba: np.ndarray) -> np.ndarray:
    """Set alpha to 0 for background pixels connected to the image border.

    The background colour is the most common colour along the border. A pixel is background
    when every channel is within BG_TOLERANCE of it. The flood fill starts from border pixels
    only, so interior pixels of the same colour (eyes, highlights) stay opaque.
    """
    height, width = rgba.shape[:2]
    if height == 0 or width == 0:
        return rgba

    rgb = rgba[:, :, :3].astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]).reshape(-1, 3)
    colours, counts = np.unique(border, axis=0, return_counts=True)
    background = colours[int(np.argmax(counts))]
    near_background = np.all(np.abs(rgb - background) <= BG_TOLERANCE, axis=2)

    todo = bytearray(near_background.ravel().astype(np.uint8).tobytes())
    keyed = bytearray(height * width)

    seeds: list[int] = []
    seeds.extend(range(width))
    seeds.extend((height - 1) * width + col for col in range(width))
    seeds.extend(row * width for row in range(height))
    seeds.extend(row * width + width - 1 for row in range(height))

    stack: list[int] = []
    for index in seeds:
        if todo[index]:
            todo[index] = 0
            keyed[index] = 1
            stack.append(index)

    while stack:
        index = stack.pop()
        row, col = divmod(index, width)
        neighbours = []
        if row > 0:
            neighbours.append(index - width)
        if row < height - 1:
            neighbours.append(index + width)
        if col > 0:
            neighbours.append(index - 1)
        if col < width - 1:
            neighbours.append(index + 1)
        for other in neighbours:
            if todo[other]:
                todo[other] = 0
                keyed[other] = 1
                stack.append(other)

    background_mask = np.frombuffer(bytes(keyed), dtype=np.uint8).reshape(height, width) == 1
    result = rgba.copy()
    result[background_mask, 3] = 0
    return result


def load_library_character(path: Path, size: tuple[int, int] | None = None) -> Image.Image:
    """Load a library image as RGBA with its border-connected background keyed out.

    Args:
        path: Image file to load.
        size: Optional (width, height) to LANCZOS-resize to after keying. Original size otherwise.
    """
    with Image.open(path) as opened:
        rgba = np.array(opened.convert("RGBA"))
    image = Image.fromarray(_key_out_background(rgba))
    if size is not None:
        image = image.resize(size, Image.Resampling.LANCZOS)
    return image


def resolve_character_image(
    *,
    expression: str | None,
    gender: str | None,
    race: str | None,
    age: str | None,
    archetype: str | None,
    spec: Callable[[], Image.Image],
    seed: int = 0,
    library_dir: Path | None = None,
) -> Image.Image:
    """Return an RGBA character image from the library, or from spec() when nothing matches.

    A library file that exists but cannot be decoded also falls back to spec().
    """
    path = select_library_character(
        expression=expression,
        gender=gender,
        race=race,
        age=age,
        archetype=archetype,
        seed=seed,
        library_dir=library_dir,
    )
    if path is not None:
        try:
            return load_library_character(path)
        except (OSError, ValueError) as exc:
            logger.warning("Could not load Wojak library image %s: %s", path, exc)
    return spec().convert("RGBA")
