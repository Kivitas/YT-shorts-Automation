"""Backend selector for the Wojak scene-still renderer.

The Wojak pipeline draws every frame with PIL (``render_wojak_scene_frame``). This module adds a
selector so a faster compositor (RAEA or Remotion) can be plugged in later without changing the
pipeline's frame loop. PIL stays the default and the always-working fallback.

Status of the fast paths (be explicit about this when wiring):

* ``pil``      Implemented. Renders each scene still with ``render_wojak_scene_frame``.
* ``raea``     Selector only. RAEA's ``raea/short.tsx`` composition places video panels
               (split / full / pip / cam layouts) plus captions, hook, chyron, progress bar and
               watermark. It has no scene card, country header, punchline card or still-image
               background, so it cannot draw a Wojak scene from a PNG. Not wired.
* ``remotion`` Selector only. The Remotion project (``remotion/src/Root.tsx``) registers
               CaptionOverlay, TitleCard, LowerThird and StatCard. None is a Wojak scene. A new
               composition that shows a still PNG with a Ken-Burns push would be the realistic
               fast path. Not wired.

``render_scene_stills`` therefore returns PIL stills for every backend today and logs when a fast
backend was requested. The interface is final; only the body for ``raea``/``remotion`` changes.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

from PIL import Image

from autoshorts.longform.wojak_compositor import render_wojak_scene_frame
from autoshorts.media.raea_renderer import find_raea_binary
from autoshorts.media.remotion_overlay import is_remotion_ready

logger = logging.getLogger(__name__)


class FastBackendName(StrEnum):
    """Names accepted by ``resolve_fast_backend``."""

    PIL = "pil"
    RAEA = "raea"
    REMOTION = "remotion"
    AUTO = "auto"


_DEFAULT_BACKEND = FastBackendName.PIL.value


def _raea_available() -> bool:
    try:
        return find_raea_binary() is not None
    except Exception as exc:  # the selector must never raise
        logger.debug("raea availability check failed: %s", exc)
        return False


def _remotion_available() -> bool:
    try:
        return bool(is_remotion_ready())
    except Exception as exc:  # the selector must never raise
        logger.debug("remotion availability check failed: %s", exc)
        return False


def resolve_fast_backend(requested: str | None) -> str:
    """Return the backend name to use: ``"pil"``, ``"raea"`` or ``"remotion"``.

    * ``None`` or empty -> ``"pil"`` (default).
    * ``"auto"`` -> ``"raea"`` if the binary exists, else ``"remotion"`` if its CLI is installed,
      else ``"pil"``.
    * An explicit name is honored when that backend is available; otherwise a warning is logged
      and ``"pil"`` is returned.
    * An unknown name logs a warning and returns ``"pil"``.

    Never raises.
    """
    name = (requested or "").strip().lower() or _DEFAULT_BACKEND
    try:
        choice = FastBackendName(name)
    except ValueError:
        logger.warning("Unknown Wojak fast backend %r; using pil", requested)
        return FastBackendName.PIL.value

    if choice is FastBackendName.PIL:
        return FastBackendName.PIL.value

    if choice is FastBackendName.AUTO:
        if _raea_available():
            return FastBackendName.RAEA.value
        if _remotion_available():
            return FastBackendName.REMOTION.value
        return FastBackendName.PIL.value

    if choice is FastBackendName.RAEA:
        if _raea_available():
            return FastBackendName.RAEA.value
        logger.warning("Wojak fast backend 'raea' requested but raea binary not found; using pil")
        return FastBackendName.PIL.value

    # choice is FastBackendName.REMOTION
    if _remotion_available():
        return FastBackendName.REMOTION.value
    logger.warning(
        "Wojak fast backend 'remotion' requested but Remotion is not installed; using pil"
    )
    return FastBackendName.PIL.value


def _render_pil_still(scene: Mapping[str, Any], index: int) -> Image.Image:
    """Render one scene still with the existing PIL compositor."""
    if "background_img" not in scene:
        raise KeyError(f"scene {index} has no 'background_img' (resolve it before rendering)")
    return render_wojak_scene_frame(
        country=str(scene.get("country", "")),
        caption=str(scene.get("caption", "")),
        background_img=scene["background_img"],
        character_img=scene.get("character_img"),
        character_side=str(scene.get("character_side", "left")),
        progress=0.0,
    )


def render_scene_stills(scenes: list[dict], out_dir: Path, backend: str) -> list[Path]:
    """Write one PNG per scene into ``out_dir`` and return the paths in scene order.

    Each scene dict needs ``country``, ``caption`` and ``background_img`` (PIL image). It may also
    carry ``character_img`` (RGBA PIL image or None) and ``character_side`` ("left" or "right").
    The caller resolves backgrounds and characters, as the pipeline does today, so this function
    has no network or Stable Diffusion side effects.

    ``backend`` is passed through ``resolve_fast_backend`` first. Only the PIL path renders stills
    today. For ``"raea"`` or ``"remotion"`` the fast composition is NOT wired yet, so PIL stills are
    written and a log line says so. Output names are ``scene_000.png``, ``scene_001.png``, ...
    """
    chosen = resolve_fast_backend(backend)
    if chosen != FastBackendName.PIL.value:
        logger.info(
            "Wojak fast backend %r selected, but the %s scene composition is not yet wired; "
            "rendering PIL stills.",
            chosen,
            chosen,
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, scene in enumerate(scenes):
        frame = _render_pil_still(scene, index)
        dest = out_dir / f"scene_{index:03d}.png"
        frame.save(dest, format="PNG")
        paths.append(dest)
    logger.info("Wrote %d scene still(s) to %s", len(paths), out_dir)
    return paths
