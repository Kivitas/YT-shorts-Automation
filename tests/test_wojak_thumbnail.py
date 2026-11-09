"""Unit tests for Wojak high-CTR thumbnail generation."""

from pathlib import Path

from PIL import Image

from autoshorts.longform.wojak_thumbnail import (
    THUMBNAIL_HEIGHT,
    THUMBNAIL_WIDTH,
    _create_character_halo,
    _draw_giant_hospital_bill,
    generate_wojak_thumbnail,
)


def test_giant_hospital_bill():
    bill = _draw_giant_hospital_bill(width=400, height=500, total_amount="$1,000,000")
    assert bill.mode == "RGBA"
    assert bill.width > 400
    assert bill.height > 500


def test_character_halo():
    char = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
    # Draw a simple box in the center
    for x in range(50, 150):
        for y in range(50, 250):
            char.putpixel((x, y), (200, 50, 50, 255))
    halo = _create_character_halo(char, halo_color=(255, 255, 255), width=8)
    assert halo.size == (200, 300)
    assert halo.mode == "RGBA"


def test_generate_wojak_thumbnail(tmp_path: Path):
    out_path = tmp_path / "thumb.jpg"
    thumb = generate_wojak_thumbnail(
        topic_title="EVERY COUNTRY'S HOSPITAL BILL",
        left_badge="USA: $2,500,000?!",
        right_badge="VS FREE?!",
        output_path=out_path,
    )
    assert thumb.size == (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
    assert out_path.is_file()
    assert out_path.stat().st_size > 5000
