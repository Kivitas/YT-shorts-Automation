"""Regression tests for the yt-dlp download pipeline.

All yt-dlp and ffmpeg invocations are patched; nothing touches the network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.core import paths
from autoshorts.core.exceptions import DownloadError, InvalidURLError
from autoshorts.discovery.metadata_fetcher import MetadataFetcher
from autoshorts.discovery.url_validator import YOUTUBE_VIDEO_ID_REGEX, extract_video_id
from autoshorts.media.downloader import (
    Downloader,
    SectionValidation,
    format_section_arg,
    run_command_with_cleanup,
)
from autoshorts.transcript.fetcher import TranscriptFetcher, TranscriptSegment

VIDEO_URL = "https://youtu.be/dQw4w9WgXcQ"
CANONICAL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VALID = SectionValidation(ok=True, reason="ok", has_video=True, has_audio=True, duration=40.0)


def _template_token(cmd: list[str]) -> tuple[Path, str]:
    """Return the output template's directory and the unique token embedded in it."""
    template = Path(cmd[cmd.index("-o") + 1])
    name = template.name
    token = name[len(".tmp_") : -len("_%(ext)s")]
    return template.parent, token


# ---------------------------------------------------------------------------
# Section timestamp formatting
# ---------------------------------------------------------------------------


def test_section_end_is_ceiled_so_cut_keeps_last_frames():
    """A fractional end must round outward; truncating dropped up to 1s from every clip."""
    assert format_section_arg(10.0001, 20.0001) == "*00:10-00:20.001"


def test_section_keeps_millisecond_precision_for_hour_form():
    assert format_section_arg(3600.25, 3600.5) == "*01:00:00.250-01:00:00.500"


def test_section_negative_start_clamps_to_zero():
    assert format_section_arg(-3.0, 10.0) == "*00:00-00:10"


@pytest.mark.parametrize("start,end", [(10.0, 10.0), (20.0, 5.0), (0.0, 0.0)])
def test_section_empty_or_inverted_range_is_rejected(start: float, end: float):
    with pytest.raises(ValueError):
        format_section_arg(start, end)


def test_download_section_reports_invalid_range_as_download_error(tmp_path: Path):
    dl = Downloader(max_retries=1, retry_delay_sec=0.01)
    with patch("subprocess.run") as mock_run:
        with pytest.raises(DownloadError):
            dl.download_section(VIDEO_URL, 30.0, 30.0, tmp_path / "x.mp4")
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# URL / video ID validation
# ---------------------------------------------------------------------------


def test_video_id_regex_rejects_trailing_newline():
    """'$' matches before a trailing newline; the ID must be anchored with \\Z instead."""
    assert YOUTUBE_VIDEO_ID_REGEX.match("dQw4w9WgXcQ\n") is None
    assert YOUTUBE_VIDEO_ID_REGEX.match("dQw4w9WgXcQ") is not None


def test_percent_encoded_newline_in_v_param_is_rejected():
    with pytest.raises(InvalidURLError):
        extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ%0A")


# ---------------------------------------------------------------------------
# Section download: argv safety, output selection, failure classification
# ---------------------------------------------------------------------------


def test_section_command_terminates_options_before_url(tmp_path: Path):
    dl = Downloader(max_retries=1, retry_delay_sec=0.01)
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        directory, token = _template_token(cmd)
        (directory / f".tmp_{token}_mp4").write_bytes(b"mp4")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch("autoshorts.media.downloader.validate_section_output", return_value=VALID),
    ):
        dl.download_section(VIDEO_URL, 0.0, 30.0, tmp_path / "out.mp4")

    cmd = captured[0]
    assert cmd[-2:] == ["--", CANONICAL]
    assert "--no-progress" in cmd


def test_stale_destination_is_not_accepted_as_download(tmp_path: Path):
    """yt-dlp exit 0 with no new output must fail, not pass an old file from a prior job."""
    dest = tmp_path / "cut.mp4"
    dest.write_bytes(b"stale cut from another job")
    dl = Downloader(max_retries=1, retry_delay_sec=0.01)

    with (
        patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")),
        patch("autoshorts.media.downloader.validate_section_output", return_value=VALID) as v,
    ):
        with pytest.raises(DownloadError, match="no finished file|not found"):
            dl.download_section(VIDEO_URL, 0.0, 30.0, dest)
        v.assert_not_called()


def test_partial_file_is_never_selected_and_leftovers_are_removed(tmp_path: Path):
    dl = Downloader(max_retries=1, retry_delay_sec=0.01)
    dest = tmp_path / "cut.mp4"

    def fake_run(cmd, **kwargs):
        directory, token = _template_token(cmd)
        # A larger partial file sorts ahead by size; it must still lose to the finished one.
        (directory / f".tmp_{token}_mp4.part").write_bytes(b"x" * 500)
        (directory / f".tmp_{token}_mp4").write_bytes(b"final")
        (directory / f".tmp_{token}_mp4.f137.mp4").write_bytes(b"fragment")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch("autoshorts.media.downloader.validate_section_output", return_value=VALID),
    ):
        result = dl.download_section(VIDEO_URL, 0.0, 30.0, dest)

    assert result == dest
    assert dest.read_bytes() == b"final"
    assert [p for p in tmp_path.iterdir() if p.name.startswith(".tmp_")] == []


def test_permanent_video_error_is_not_retried(tmp_path: Path):
    dl = Downloader(max_retries=3, retry_delay_sec=0.01)
    stderr = "ERROR: [youtube] dQw4w9WgXcQ: Private video. Sign in if you've been granted access."
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, "", stderr),
    ) as mock_run:
        with pytest.raises(DownloadError, match="Private video"):
            dl.download_section(VIDEO_URL, 0.0, 30.0, tmp_path / "x.mp4")
        assert mock_run.call_count == 1


def test_transient_error_is_still_retried(tmp_path: Path):
    dl = Downloader(max_retries=3, retry_delay_sec=0.01)
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, "", "HTTP Error 503: try later"),
    ) as mock_run:
        with pytest.raises(DownloadError):
            dl.download_section(VIDEO_URL, 0.0, 30.0, tmp_path / "x.mp4")
        assert mock_run.call_count == 3


def test_missing_binary_fails_fast_without_retries(tmp_path: Path):
    dl = Downloader(max_retries=3, retry_delay_sec=0.01)
    with patch("subprocess.run", side_effect=FileNotFoundError(2, "No such file", "yt-dlp")) as m:
        with pytest.raises(DownloadError):
            dl.download_section(VIDEO_URL, 0.0, 30.0, tmp_path / "x.mp4")
        assert m.call_count == 1


def test_audio_download_terminates_options_and_rejects_stale_output(tmp_path: Path):
    dest = tmp_path / "audio.mp3"
    dest.write_bytes(b"old audio")
    dl = Downloader(max_retries=1, retry_delay_sec=0.01)
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as m:
        with pytest.raises(DownloadError):
            dl.download_audio_only(VIDEO_URL, dest)
        cmd = m.call_args[0][0]
    assert cmd[-2:] == ["--", CANONICAL]


# ---------------------------------------------------------------------------
# Subprocess handling
# ---------------------------------------------------------------------------


def test_run_command_closes_stdin_and_never_signals_after_normal_exit():
    proc = MagicMock(spec=subprocess.Popen)
    proc.pid = 4242
    proc.communicate.return_value = ("", "boom")
    proc.returncode = 1

    with (
        patch("subprocess.Popen", return_value=proc) as mock_popen,
        patch("autoshorts.media.downloader.terminate_process_tree") as mock_kill,
    ):
        result = run_command_with_cleanup(["yt-dlp", "--version"], timeout=5)

    assert result.returncode == 1
    assert mock_popen.call_args.kwargs["stdin"] == subprocess.DEVNULL
    mock_kill.assert_not_called()


# ---------------------------------------------------------------------------
# Metadata and transcript parsing
# ---------------------------------------------------------------------------


def test_metadata_accepts_multiple_json_lines_and_uses_first_entry():
    stdout = json.dumps({"title": "First", "duration": 5}) + "\n" + json.dumps({"title": "Second"})
    fetcher = MetadataFetcher(yt_dlp_binary="yt-dlp")
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout, "")):
        meta = fetcher._fetch_via_ytdlp(CANONICAL, "dQw4w9WgXcQ")
    assert meta.title == "First"
    assert meta.duration == 5.0


def test_metadata_rejects_non_json_output():
    from autoshorts.core.exceptions import MetadataFetchError

    fetcher = MetadataFetcher(yt_dlp_binary="yt-dlp")
    with (
        patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "oops", "")),
        pytest.raises(MetadataFetchError),
    ):
        fetcher._fetch_via_ytdlp(CANONICAL, "dQw4w9WgXcQ")


def test_transcript_json3_failure_falls_through_to_vtt():
    subs = {
        "subtitles": {
            "en": [
                {"ext": "json3", "url": "https://subs.example/json3"},
                {"ext": "vtt", "url": "https://subs.example/vtt"},
            ]
        },
        "automatic_captions": {},
    }
    fetcher = TranscriptFetcher(languages=("en",))
    vtt_segments = [TranscriptSegment(start=0.0, duration=1.0, text="hello")]
    with (
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(subs), ""),
        ),
        patch.object(fetcher, "_fetch_and_parse_json3", side_effect=ValueError("bad json")),
        patch.object(fetcher, "_fetch_and_parse_vtt", return_value=vtt_segments),
    ):
        segments = fetcher._fetch_via_ytdlp(CANONICAL, "dQw4w9WgXcQ")
    assert segments == vtt_segments


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def _exe_name() -> str:
    return "yt-dlp.exe" if os.name == "nt" else "yt-dlp"


def test_resolve_prefers_environment_binary_over_stale_path_copy(tmp_path, monkeypatch):
    scripts = tmp_path / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir()
    (scripts / _exe_name()).write_text("")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(paths.shutil, "which", lambda *a, **k: "/stale/system/yt-dlp")

    resolved = paths.resolve_yt_dlp_binary("yt-dlp")

    assert Path(resolved) == (scripts / _exe_name()).resolve()


def test_resolve_falls_back_to_name_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(paths.shutil, "which", lambda *a, **k: None)

    assert paths.resolve_yt_dlp_binary("yt-dlp") == "yt-dlp"


def test_select_audio_file_accepts_extracted_name_and_rejects_partials(tmp_path: Path):
    """yt-dlp -x writes <prefix>webm.opus; partial downloads must never be picked."""
    from autoshorts.media.downloader import _select_audio_file

    prefix = ".tmp_audio_abc12345_"
    (tmp_path / f"{prefix}webm.opus.part").write_bytes(b"partial")
    (tmp_path / f"{prefix}webm.f251.webm").write_bytes(b"fragment")
    finished = tmp_path / f"{prefix}webm.opus"
    finished.write_bytes(b"audio")
    picked = _select_audio_file(list(tmp_path.glob(f"{prefix}*")), prefix)
    assert picked == finished

    finished.unlink()
    assert _select_audio_file(list(tmp_path.glob(f"{prefix}*")), prefix) is None
