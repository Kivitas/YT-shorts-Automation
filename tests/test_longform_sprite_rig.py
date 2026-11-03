"""Tests for the satori part rig in autoshorts.longform.sprite_rig."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import autoshorts.longform.sprites as sprites
from autoshorts.longform.sprite_rig import CANVAS_SIZE, PART_NAMES, draw_sprite_rig, wrist_offset

# Solid boxes (x0, y0, x1, y1) per part, on the left side of the figure so that the
# midline split keeps them whole.
BOXES: dict[str, tuple[int, int, int, int]] = {
    "upper_leg": (160, 470, 196, 600),
    "lower_leg": (160, 600, 196, 720),
    "shoe": (150, 720, 196, 760),
    "torso": (160, 240, 240, 470),
    "upper_arm": (100, 250, 140, 350),
    "forearm": (100, 350, 140, 448),
    "hand": (100, 440, 140, 480),
    "head": (160, 80, 240, 220),
    "eyes": (180, 120, 196, 140),
    "eyebrows": (180, 100, 196, 110),
    "mouth": (180, 190, 196, 200),
}

CANVAS_OUT = (300, 400)
ORIGIN = (150.0, 300.0)
SCALE = 0.5


@pytest.fixture
def use_sprite_dir(monkeypatch: pytest.MonkeyPatch):
    """Point the sprite loader at a chosen folder and reset its cache around the test."""

    def use(folder: Path) -> None:
        monkeypatch.setattr(sprites, "SPRITE_DIR", folder)
        sprites.load_sprite.cache_clear()

    yield use
    sprites.load_sprite.cache_clear()


def _write_parts(folder: Path, skip: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(PART_NAMES):
        if name == skip:
            continue
        part = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        part.paste((40 + 20 * index, 200, 120, 255), BOXES[name])
        part.save(folder / f"{name}.png")
    return folder


def _blank() -> Image.Image:
    return Image.new("RGB", CANVAS_OUT, (0, 0, 0))


def _draw(joints: dict[str, float], **kwargs) -> Image.Image:
    img = _blank()
    assert draw_sprite_rig(img, joints, ORIGIN, SCALE, **kwargs) is True
    return img


def test_returns_false_when_a_part_is_missing(tmp_path: Path, use_sprite_dir) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    use_sprite_dir(empty)
    img = _blank()
    assert draw_sprite_rig(img, {}, ORIGIN, SCALE) is False
    assert img.getbbox() is None


def test_returns_false_when_one_required_part_is_missing(tmp_path: Path, use_sprite_dir) -> None:
    use_sprite_dir(_write_parts(tmp_path / "partial", skip="forearm"))
    img = _blank()
    assert draw_sprite_rig(img, {}, ORIGIN, SCALE) is False
    assert img.getbbox() is None


def test_returns_true_and_changes_pixels_with_parts(tmp_path: Path, use_sprite_dir) -> None:
    use_sprite_dir(_write_parts(tmp_path / "full"))
    img = _blank()
    assert draw_sprite_rig(img, {}, ORIGIN, SCALE) is True
    assert img.getbbox() is not None


def test_rotated_arm_changes_pixels(tmp_path: Path, use_sprite_dir) -> None:
    use_sprite_dir(_write_parts(tmp_path / "full"))
    rest = _draw({"l_upper": -14.6, "l_lower": 3.5})
    raised = _draw({"l_upper": -90.0, "l_lower": -90.0})
    assert raised.tobytes() != rest.tobytes()


def test_expressions_show_procedurally_without_variant_files(
    tmp_path: Path, use_sprite_dir
) -> None:
    use_sprite_dir(_write_parts(tmp_path / "full"))
    neutral = _draw({}, expression="neutral")
    grit = _draw({}, expression="grit")
    happy = _draw({}, expression="happy")
    assert grit.tobytes() != neutral.tobytes()
    assert happy.tobytes() != grit.tobytes()


def test_complete_variant_set_replaces_procedural_face(tmp_path: Path, use_sprite_dir) -> None:
    folder = _write_parts(tmp_path / "full")
    use_sprite_dir(folder)
    procedural = _draw({}, expression="happy")
    for name in ("eyes", "eyebrows", "mouth"):
        variant = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        variant.paste((255, 255, 255, 255), (300, 300, 330, 330))
        variant.save(folder / f"{name}_happy.png")
    use_sprite_dir(folder)  # reset the loader cache so the new variants are read
    assert _draw({}, expression="happy").tobytes() != procedural.tobytes()


def test_partial_variant_set_is_ignored(tmp_path: Path, use_sprite_dir) -> None:
    folder = _write_parts(tmp_path / "full")
    use_sprite_dir(folder)
    procedural = _draw({}, expression="happy")
    variant = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    variant.paste((255, 255, 255, 255), (300, 300, 330, 330))
    variant.save(folder / "mouth_happy.png")
    use_sprite_dir(folder)
    assert _draw({}, expression="happy").tobytes() == procedural.tobytes()


def test_back_of_head_hides_the_face(tmp_path: Path, use_sprite_dir) -> None:
    use_sprite_dir(_write_parts(tmp_path / "full"))
    faced = _draw({}, expression="happy")
    back = _draw({"back_of_head": 1.0}, expression="happy")
    assert back.tobytes() != faced.tobytes()
    # With the face hidden, the expression no longer changes the picture.
    assert _draw({"back_of_head": 1.0}, expression="grit").tobytes() == back.tobytes()


def test_wrist_offset_follows_the_arm_drop_and_scale() -> None:
    hanging = wrist_offset({}, 1.0, "l")
    dropped = wrist_offset({"drop": 10.0}, 1.0, "l")
    assert abs((dropped[1] - hanging[1]) - 10.0 * 2.4) < 1e-6
    raised = wrist_offset({"l_upper": -90.0, "l_lower": -90.0}, 1.0, "l")
    assert raised[0] < hanging[0]
    half = wrist_offset({"l_upper": -90.0, "l_lower": -90.0}, 0.5, "l")
    assert abs(half[0] - raised[0] / 2) < 1e-6


def test_mirror_flips_the_figure(tmp_path: Path, use_sprite_dir) -> None:
    use_sprite_dir(_write_parts(tmp_path / "full"))
    plain = _draw({"l_upper": -60.0})
    mirrored = _draw({"l_upper": -60.0}, mirror=True)
    assert mirrored.tobytes() != plain.tobytes()


def test_angle_zero_keeps_placement_and_scale(tmp_path: Path, use_sprite_dir) -> None:
    use_sprite_dir(_write_parts(tmp_path / "full"))
    img = _draw({})
    # The torso (index 3) is solid colour (100, 200, 120); its centre maps to output (150, 240).
    assert img.getpixel((150, 240)) == (100, 200, 120)


def test_rotated_limb_tip_is_not_clipped_to_the_art_box(tmp_path: Path, use_sprite_dir) -> None:
    use_sprite_dir(_write_parts(tmp_path / "full"))
    size = (800, 1200)
    origin = (400.0, 700.0)
    # At scale 1 the 400x800 figure box sits at x 200..600 and y 230..1030 in this output.
    box = (200, 230, 600, 1030)

    def pixels_outside_box(joints: dict[str, float]) -> int:
        img = Image.new("RGB", size, (0, 0, 0))
        assert draw_sprite_rig(img, joints, origin, 1.0) is True
        outside = img.copy()
        outside.paste((0, 0, 0), box)
        mask = outside.convert("L").point(lambda v: 255 if v else 0)
        return mask.histogram()[255]

    assert pixels_outside_box({}) == 0
    # The raised left forearm reaches past the figure's left edge (x < 200 in this output).
    assert pixels_outside_box({"l_upper": -90.0, "l_lower": -90.0}) > 0
