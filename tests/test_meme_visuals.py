"""Unit tests for Manga/Anime Visual & Animation Engine and Thumbnail Maker."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from autoshorts.meme.thumbnail import make_meme_thumbnail
from autoshorts.meme.types import MemeScript, MemeSegment
from autoshorts.meme.visuals import (
    convert_to_manga_screentone,
    draw_anime_character,
    draw_background,
    draw_manga_speed_lines,
    draw_speech_bubble,
    get_resolution,
    render_meme_frame,
)


def test_get_resolution() -> None:
    assert get_resolution("9:16") == (1080, 1920)
    assert get_resolution("16:9") == (1920, 1080)
    # Default fallback
    assert get_resolution("custom") == (1080, 1920)


def test_draw_backgrounds() -> None:
    for bg_theme in (
        "train_station",
        "classroom",
        "supermarket",
        "snowy_winter",
        "gaming_room",
        "street_city",
        "gym",
    ):
        img = draw_background(640, 360, theme=bg_theme, visual_style="anime_color")
        assert isinstance(img, Image.Image)
        assert img.size == (640, 360)


def test_manga_screentone_conversion() -> None:
    base = Image.new("RGBA", (200, 200), (128, 128, 128, 255))
    manga_img = convert_to_manga_screentone(base, dot_spacing=6)
    assert manga_img.size == (200, 200)
    assert manga_img.mode in ("L", "RGBA", "RGB")


def test_draw_anime_character_poses_and_expressions() -> None:
    poses = [
        "sleeping_desk",
        "surrounded_books",
        "gaming_intense",
        "hiding_corner",
        "dramatic_heroic",
        "smug_crossed_arms",
        "shocked_facepalm",
        "screaming_run",
        "peace_sign",
    ]
    expressions = [
        "shocked",
        "smug",
        "deadpan",
        "panicked",
        "screaming",
        "sleeping",
        "crying_laughing",
        "sparkling_eyes",
    ]

    for pose, expr in zip(poses, expressions, strict=False):
        char_img = draw_anime_character(
            width=500,
            height=500,
            pose=pose,
            expression=expr,
            visual_style="anime_color",
            time_sec=1.0,
            blink=False,
            breathing_offset=0.0,
        )
        assert char_img.size == (500, 500)
        assert isinstance(char_img, Image.Image)


def test_draw_speech_bubble_and_speed_lines() -> None:
    speedlines = draw_manga_speed_lines(640, 360, num_lines=24)
    assert speedlines.size == (640, 360)

    canvas = Image.new("RGBA", (640, 360), (255, 255, 255, 255))
    draw_speech_bubble(
        canvas, "NANI?!", anchor=(200, 100), mouth_pos=(250, 200), visual_style="anime_color"
    )
    assert canvas.size == (640, 360)


def test_render_meme_frame_both_modes_and_resolutions() -> None:
    seg = MemeSegment(
        id="japan",
        name="Japan",
        heading="Japan",
        narration="The train arrived 3 seconds late so the driver apologized.",
        scenario="Train conductor apologizes profusely",
        character_description="Conductor anime reaction",
        pose="shocked_facepalm",
        expression="shocked",
        background_theme="train_station",
        animation_type="zoom_in",
        music_mood="quirky",
        sfx_cue="record_scratch",
        subtitle_text="The train was 3 seconds late...",
        speech_bubble_text="GOMENASAI!!",
    )

    # 1. 9:16 vertical in colored anime
    frame_9_16 = render_meme_frame(
        segment=seg,
        frame_idx=10,
        total_frames=90,
        fps=30,
        width=540,  # test on half res
        height=960,
        visual_style="anime_color",
        mode="country_meme",
    )
    assert frame_9_16.size == (540, 960)

    # 2. 16:9 horizontal in manga black & white
    frame_16_9 = render_meme_frame(
        segment=seg,
        frame_idx=25,
        total_frames=90,
        fps=30,
        width=960,
        height=540,
        visual_style="manga_bw",
        mode="types_of",
    )
    assert frame_16_9.size == (960, 540)


def test_make_meme_thumbnail(tmp_path: Path) -> None:
    seg = MemeSegment(
        id="gamer",
        name="The Rage Quitter",
        heading="Types of Gamers",
        narration="Throws controller into another dimension.",
        scenario="Gamer breaks keyboard",
        character_description="Angry gamer",
        pose="screaming_run",
        expression="screaming",
        background_theme="gaming_room",
        animation_type="shake",
        music_mood="suspense",
        sfx_cue="cartoon_impact",
        subtitle_text="Lag caused everything!",
        speech_bubble_text="IT WAS LAG!!",
    )

    script = MemeScript(
        mode="types_of",
        category="relatable",
        topic="Types of Gamers",
        title="Types of Gamers Be Like",
        hook="Which gamer are you?",
        intro="We all know that one friend...",
        segments=[seg],
        ending="Drop your gamer tag below!",
        hashtags=["#AnimeMemes", "#GamingMemes", "#Shorts"],
        estimated_duration=30.0,
    )

    thumb_path = tmp_path / "test_thumb.png"
    out = make_meme_thumbnail(script, thumb_path, aspect_ratio="9:16", visual_style="anime_color")
    assert out.exists()
    assert out.stat().st_size > 1024

    with Image.open(out) as img:
        assert img.size == (1080, 1920)
