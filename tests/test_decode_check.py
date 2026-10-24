"""The decode check must tell a clean render from a damaged one."""

from __future__ import annotations

import subprocess

from autoshorts.media.compositor import count_decode_errors
from autoshorts.media.encoder_probe import get_ffmpeg_path


def test_clean_video_has_no_decode_errors(tmp_path):
    clean = tmp_path / "clean.mp4"
    subprocess.run(
        [
            get_ffmpeg_path(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x568:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(clean),
        ],
        check=True,
    )
    assert count_decode_errors(clean) == 0


def test_damaged_video_is_reported(tmp_path):
    clean = tmp_path / "clean.mp4"
    subprocess.run(
        [
            get_ffmpeg_path(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x568:rate=30",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clean),
        ],
        check=True,
    )
    data = bytearray(clean.read_bytes())
    middle = len(data) // 2
    data[middle : middle + 4000] = b"\xff" * 4000  # overwrite a slice of the bitstream
    damaged = tmp_path / "damaged.mp4"
    damaged.write_bytes(bytes(data))
    assert count_decode_errors(damaged) > 0
