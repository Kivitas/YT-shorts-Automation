import wave
from pathlib import Path

import pytest

from autoshorts.longform.drawer import POSES, interpolate_pose, render_scene_frames
from autoshorts.longform.thumbnail import make_thumbnail
from autoshorts.longform.types import MAX_VIDEO_SECONDS, Section, SectionAudio


def _wav(path: Path, seconds: float = 1.0) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


def _sections(tmp_path: Path, n: int = 2, duration: float = 1.0) -> list[SectionAudio]:
    out = []
    for i in range(n):
        words = [{"word": f"w{k}", "start": k * 0.2, "end": k * 0.2 + 0.18} for k in range(5)]
        out.append(
            SectionAudio(
                section=Section(heading=f"Part {i + 1}", narration="w0 w1 w2 w3 w4"),
                audio_path=_wav(tmp_path / f"s{i}.wav", duration),
                words=words,
                duration=duration,
            )
        )
    return out


def test_frames_size_and_count(tmp_path):
    secs = _sections(tmp_path)
    frames = list(render_scene_frames(secs, "Test Title", max_seconds_per_section=0.2))
    assert len(frames) == 2 * 6  # 0.2 s * 30 fps per section
    assert all(len(f) == 1920 * 1080 * 3 for f in frames)


def test_frame_count_full_section(tmp_path):
    secs = _sections(tmp_path, n=1)
    gen = render_scene_frames(secs, "Test Title")
    assert sum(1 for _ in gen) == 30


def test_duration_cap_raises(tmp_path):
    secs = _sections(tmp_path, n=1, duration=1.0)
    secs[0].duration = MAX_VIDEO_SECONDS + 1
    with pytest.raises(ValueError):
        render_scene_frames(secs, "Too long")


def test_thumbnail_png(tmp_path):
    from PIL import Image

    p = make_thumbnail("Why Sleep Matters More Than You Think", tmp_path / "t.png")
    with Image.open(p) as im:
        assert im.size == (1280, 720)
        assert im.format == "PNG"


def test_pose_interpolation_midpoint():
    mid = interpolate_pose("stand", "point", 0.5)
    a, b = POSES["stand"]["r_hand"], POSES["point"]["r_hand"]
    assert min(a[0], b[0]) <= mid["r_hand"][0] <= max(a[0], b[0])
    assert mid["r_hand"][0] == pytest.approx((a[0] + b[0]) / 2)
    assert interpolate_pose("stand", "point", 0.0) == {k: POSES["stand"][k] for k in mid}
