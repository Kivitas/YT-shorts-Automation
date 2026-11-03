"""PNG sprite loader and compositor for long-form explainer scenes (Pillow).

Sprites live in ``assets/longform/sprites/<name>.png`` at the repository root and
are produced by an external generator. Every function here tolerates missing
files: a missing sprite is never an error, it simply reports ``None`` / ``False``
so callers can fall back to the procedural drawing in ``props`` / ``rig``.

Conventions:
- ``scale`` multiplies both dimensions of the sprite.
- ``centre`` is the ``(x, y)`` pixel position of the sprite's centre on the target.
- ``rotation_deg`` is counter-clockwise, applied after scaling and flipping.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# src/autoshorts/longform/sprites.py -> repository root is parents[3].
SPRITE_DIR: Path = Path(__file__).resolve().parents[3] / "assets" / "longform" / "sprites"


@functools.cache
def load_sprite(name: str) -> Image.Image | None:
    """Return the sprite ``name`` as an RGBA image, or ``None`` if unavailable.

    Results (including ``None``) are cached per name. A missing or unreadable
    file logs a single warning the first time that name is requested.
    """
    path = SPRITE_DIR / f"{name}.png"
    if not path.is_file():
        logger.warning("Sprite %r not found at %s; callers will fall back", name, path)
        return None
    try:
        with Image.open(path) as handle:
            return handle.convert("RGBA")
    except (OSError, ValueError) as exc:
        logger.warning("Sprite %r could not be read from %s: %s", name, path, exc)
        return None


def has_sprite(name: str) -> bool:
    """Return True when the sprite ``name`` can be loaded."""
    return load_sprite(name) is not None


def _scaled_size(src: Image.Image, scale: float) -> tuple[int, int]:
    width = max(1, round(src.width * scale))
    height = max(1, round(src.height * scale))
    return width, height


def sprite_size(name: str, scale: float = 1.0) -> tuple[int, int] | None:
    """Return the ``(width, height)`` of ``name`` at ``scale`` (before rotation)."""
    src = load_sprite(name)
    if src is None:
        return None
    return _scaled_size(src, scale)


def paste_sprite(
    img: Image.Image,
    name: str,
    centre: tuple[float, float],
    scale: float = 1.0,
    rotation_deg: float = 0.0,
    flip: bool = False,
    alpha: float = 1.0,
) -> bool:
    """Alpha-composite sprite ``name`` onto ``img`` with its centre at ``centre``.

    ``img`` may be RGB or RGBA and is modified in place. Returns ``True`` when the
    sprite was drawn (including ``alpha=0``, a no-op), and ``False`` when the sprite
    is missing so the caller can fall back to procedural drawing.
    """
    src = load_sprite(name)
    if src is None:
        return False

    sprite = src.resize(_scaled_size(src, scale), Image.Resampling.LANCZOS)
    if flip:
        sprite = sprite.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if rotation_deg % 360.0:
        sprite = sprite.rotate(
            rotation_deg,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )

    opacity = min(1.0, max(0.0, alpha))
    if opacity < 1.0:
        mask = sprite.getchannel("A").point(lambda v: round(v * opacity))
        sprite.putalpha(mask)

    sw, sh = sprite.size
    left = round(centre[0] - sw / 2)
    top = round(centre[1] - sh / 2)

    # Composite only the covered region so full-frame canvases stay cheap.
    region = img.crop((left, top, left + sw, top + sh)).convert("RGBA")
    composed = Image.alpha_composite(region, sprite)
    if img.mode == "RGBA":
        img.paste(composed, (left, top))
    else:
        img.paste(composed.convert(img.mode), (left, top))
    return True
