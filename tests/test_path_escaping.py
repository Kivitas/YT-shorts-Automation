"""Tests for Windows path escaping inside FFmpeg filtergraph expressions."""

from pathlib import Path

from autoshorts.core.paths import escape_ffmpeg_filter_path
from autoshorts.media.dopamine_effects import build_complex_filtergraph, get_dopamine_preset


def test_escape_ffmpeg_filter_path_windows_drive():
    """Verify that Windows drive letters and backslashes are properly escaped."""
    raw_path = r"C:\work\job123\subs.ass"
    escaped = escape_ffmpeg_filter_path(raw_path)

    # Must be enclosed in single quotes
    assert escaped.startswith("'") and escaped.endswith("'")

    # Colon must be escaped as \:
    assert r"C\:" in escaped

    # Backslashes must be converted to forward slashes (except the escape slash)
    inner = escaped[1:-1]
    assert r"C\:/work/job123/subs.ass" in inner


def test_escape_ffmpeg_filter_path_with_spaces():
    """Verify paths containing spaces are preserved and correctly escaped."""
    raw_path = r"C:\Users\Sirisha Rajana\Desktop\New folder\Automation\subs.ass"
    escaped = escape_ffmpeg_filter_path(raw_path)

    assert r"C\:" in escaped
    assert "Sirisha Rajana" in escaped
    assert "New folder" in escaped
    assert escaped.startswith("'") and escaped.endswith("'")


def test_escape_ffmpeg_filter_path_accepts_pathlib_object():
    """Verify Path objects are accepted identically to strings."""
    path_obj = Path("work") / "subtitles.ass"
    escaped = escape_ffmpeg_filter_path(path_obj)
    assert escaped.startswith("'") and escaped.endswith("'")
    assert "subtitles.ass" in escaped


def test_filtergraph_contains_escaped_path():
    """Verify that build_complex_filtergraph generates subtitles filter with escaped path."""
    subs_path = r"C:\work\sample.ass"
    preset = get_dopamine_preset("medium")

    filtergraph, out_v, out_a = build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path=subs_path,
        duration_sec=30.0,
        dopamine_preset=preset,
    )

    # Subtitles filter must contain escaped path
    assert "subtitles=" in filtergraph
    assert r"C\:/work/sample.ass" in filtergraph
    assert out_v == "[outv]"
    assert out_a == "[master_a]"
