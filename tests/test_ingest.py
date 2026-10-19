"""Comprehensive unit tests for AutoShorts Track D (ingest & downloader).

Covers:
- URL validation and normalization (watch, youtu.be, shorts, embed, music, tracking stripping)
- Instruction parsing (per-link overrides, colon syntax, textarea, file, CLI list, dedupe)
- yt-dlp section argument formatting (*MM:SS-MM:SS)
- Metadata extraction (yt-dlp dump-json with oEmbed fallback)
- Transcript extraction (youtube-transcript-api preference, yt-dlp fallback, NO_CAPTIONS signal)
- Downloader protocol conformance, yt-dlp args, retries, and audio-only download

Strictly offline: all external subprocesses and HTTP requests are mocked.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.core.config import JobDefaultsConfig, Settings
from autoshorts.core.contracts import Downloader as DownloaderProtocol
from autoshorts.core.exceptions import (
    DownloadError,
    InvalidURLError,
    MetadataFetchError,
    TranscriptNotFoundError,
)
from autoshorts.core.models import VideoMetadata
from autoshorts.discovery.metadata_fetcher import MetadataFetcher, fetch_video_metadata
from autoshorts.discovery.url_validator import (
    ParsedLink,
    extract_video_id,
    is_valid_youtube_url,
    normalize_youtube_url,
    parse_link_line,
    parse_links_input,
)
from autoshorts.media.downloader import (
    DEFAULT_FORMAT_SPEC,
    Downloader,
    format_section_arg,
)
from autoshorts.transcript.fetcher import (
    NO_CAPTIONS,
    TranscriptFetcher,
    TranscriptSegment,
    fetch_transcript,
    fetch_transcript_or_signal,
    parse_json3_dict,
    parse_vtt_text,
)


# =============================================================================
# 1. URL Validation & Normalization Tests
# =============================================================================

@pytest.mark.parametrize(
    "url_input,expected_id",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("http://youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ?si=abcdef12345", "dQw4w9WgXcQ"),
        ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_extract_video_id_valid(url_input: str, expected_id: str):
    """Test video ID extraction across various supported YouTube URL variations."""
    vid = extract_video_id(url_input)
    assert vid == expected_id
    assert is_valid_youtube_url(url_input) is True


def test_normalize_youtube_url_strips_tracking():
    """Verify normalize_youtube_url strips tracking, playlist, and timestamp parameters."""
    dirty_url = (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        "&si=XYZ123&feature=share&t=45s&list=PL999&index=3&fbclid=abc&utm_source=twitter"
    )
    canonical = normalize_youtube_url(dirty_url)
    assert canonical == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert "si=" not in canonical
    assert "t=" not in canonical
    assert "list=" not in canonical


@pytest.mark.parametrize(
    "invalid_input",
    [
        "",
        "   ",
        "https://vimeo.com/12345678",
        "https://tiktok.com/@user/video/1234567890",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=short",  # Invalid ID length
        "https://youtube.com/watch?v=toolongvideoidentifier12345",  # Invalid length
        "https://youtube.com/invalid_page",
    ],
)
def test_extract_video_id_invalid(invalid_input: str):
    """Verify InvalidURLError is raised for unsupported hosts and malformed IDs."""
    with pytest.raises(InvalidURLError):
        extract_video_id(invalid_input)
    assert is_valid_youtube_url(invalid_input) is False


# =============================================================================
# 2. Instruction Parsing & Typed Links Parser Tests
# =============================================================================

def test_parse_link_line_space_separated_syntax():
    """Verify parsing per-link instruction syntax like: URL | max 45s | focus funny | clips 3 | bg minecraft."""
    line = "https://youtu.be/dQw4w9WgXcQ | max 45s | focus funny reactions | clips 3 | bg minecraft"
    parsed = parse_link_line(line)

    assert isinstance(parsed, ParsedLink)
    assert parsed.video_id == "dQw4w9WgXcQ"
    assert parsed.canonical_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert parsed.max_duration_seconds == 45
    assert parsed.focus_prompt == "funny reactions"
    assert parsed.clips_per_video == 3
    assert parsed.background_style == "minecraft"


def test_parse_link_line_colon_syntax():
    """Verify parsing colon syntax: URL | clips: 2 | max_s: 45 | focus: tech talk | bg: subway | dopamine: high."""
    line = "https://www.youtube.com/watch?v=dQw4w9WgXcQ | clips: 2 | max_s: 45 | focus: tech talk | bg: subway | dopamine: high"
    parsed = parse_link_line(line)

    assert parsed.clips_per_video == 2
    assert parsed.max_duration_seconds == 45
    assert parsed.focus_prompt == "tech talk"
    assert parsed.background_style == "subway"
    assert parsed.dopamine_level == "high"


def test_parse_link_line_with_global_defaults():
    """Verify defaults from JobDefaultsConfig are applied when overrides are not specified."""
    defaults = JobDefaultsConfig(
        clips_per_video=2,
        min_duration_seconds=25,
        max_duration_seconds=40,
        focus_prompt="Insightful moments",
        background_category="subway",
    )
    line = "https://youtu.be/dQw4w9WgXcQ | max 35s"
    parsed = parse_link_line(line, defaults=defaults, global_instructions="Global high retention")

    # max was overridden
    assert parsed.max_duration_seconds == 35
    # clips and bg inherited from defaults
    assert parsed.clips_per_video == 2
    assert parsed.min_duration_seconds == 25
    assert parsed.background_style == "subway"
    # global instructions present
    assert "Global high retention" in (parsed.custom_instructions or "")


def test_parse_links_input_multiline_and_dedupe():
    """Verify multiline textarea parsing with deduplication."""
    textarea = """
    # First link with overrides
    https://www.youtube.com/watch?v=dQw4w9WgXcQ | clips 2 | bg subway
    
    # Duplicate link with different override
    https://youtu.be/dQw4w9WgXcQ | max 40s
    
    # Second unique link
    https://www.youtube.com/watch?v=jNQXAC9IVRw | clips 1 | bg minecraft
    """
    results = parse_links_input(textarea, dedupe=True)
    assert len(results) == 2
    assert results[0].video_id == "dQw4w9WgXcQ"
    assert results[0].clips_per_video == 2
    assert results[0].background_style == "subway"
    assert results[1].video_id == "jNQXAC9IVRw"


def test_parse_links_input_from_file(tmp_path: Path):
    """Verify reading links from a file on disk."""
    links_file = tmp_path / "links.txt"
    links_file.write_text(
        "https://youtu.be/dQw4w9WgXcQ | focus funny\n"
        "https://youtu.be/jNQXAC9IVRw | focus science\n",
        encoding="utf-8",
    )
    results = parse_links_input(links_file)
    assert len(results) == 2
    assert results[0].focus_prompt == "funny"
    assert results[1].focus_prompt == "science"


def test_parse_links_input_cli_list():
    """Verify parsing a CLI sequence of link strings."""
    cli_args = [
        "https://youtu.be/dQw4w9WgXcQ | clips 3",
        "https://youtu.be/jNQXAC9IVRw | max 45s",
    ]
    results = parse_links_input(cli_args)
    assert len(results) == 2
    assert results[0].clips_per_video == 3
    assert results[1].max_duration_seconds == 45


# =============================================================================
# 3. Section Timestamp Formatting & Downloader Protocol Tests
# =============================================================================

@pytest.mark.parametrize(
    "start,end,expected",
    [
        (0.0, 45.0, "*00:00-00:45"),
        (65.0, 110.0, "*01:05-01:50"),
        (125.5, 170.2, "*02:05-02:50"),
        (3665.0, 3710.0, "*01:01:05-01:01:50"),
    ],
)
def test_format_section_arg(start: float, end: float, expected: str):
    """Verify yt-dlp section argument formatting matches *MM:SS-MM:SS requirement."""
    assert format_section_arg(start, end) == expected


def test_downloader_implements_protocol():
    """Verify Downloader satisfies the runtime_checkable contracts.Downloader protocol."""
    dl = Downloader()
    assert isinstance(dl, DownloaderProtocol)


def test_downloader_section_args_and_atomic_execution(tmp_path: Path):
    """Verify download_section calls yt-dlp with required flags and renames atomically."""
    work_dir = tmp_path / "work" / "dQw4w9WgXcQ"
    work_dir.mkdir(parents=True)
    target_mp4 = work_dir / "raw.mp4"

    def fake_subprocess_run(cmd, **kwargs):
        # Locate the -o argument
        o_idx = cmd.index("-o")
        temp_out_template = cmd[o_idx + 1]
        # Simulate yt-dlp replacing %(ext)s with mp4
        actual_temp = Path(temp_out_template.replace("%(ext)s", "mp4"))
        actual_temp.write_bytes(b"dummy mp4 data")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    dl = Downloader(cookies_from_browser="chrome")
    with patch("subprocess.run", side_effect=fake_subprocess_run) as mock_run:
        result_path = dl.download_section(
            url="https://youtu.be/dQw4w9WgXcQ",
            start_sec=65.0,
            end_sec=110.0,
            output_path=target_mp4,
        )

        assert result_path == target_mp4
        assert target_mp4.is_file()
        assert target_mp4.read_bytes() == b"dummy mp4 data"

        # Verify command arguments
        called_cmd = mock_run.call_args[0][0]
        assert "--download-sections" in called_cmd
        sec_idx = called_cmd.index("--download-sections")
        assert called_cmd[sec_idx + 1] == "*01:05-01:50"
        assert "--force-keyframes-at-cuts" in called_cmd
        assert "--cookies-from-browser" in called_cmd
        assert "chrome" in called_cmd
        assert "-f" in called_cmd


def test_downloader_download_audio_only(tmp_path: Path):
    """Verify download_audio_only calls yt-dlp with audio extraction flags."""
    audio_path = tmp_path / "work" / "audio.mp3"

    def fake_subprocess_audio(cmd, **kwargs):
        o_idx = cmd.index("-o")
        temp_out = Path(cmd[o_idx + 1].replace("%(ext)s", "mp3"))
        temp_out.write_bytes(b"dummy audio data")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    dl = Downloader()
    with patch("subprocess.run", side_effect=fake_subprocess_audio) as mock_run:
        result = dl.download_audio_only("https://youtu.be/dQw4w9WgXcQ", audio_path)
        assert result == audio_path
        assert audio_path.is_file()

        called_cmd = mock_run.call_args[0][0]
        assert "-f" in called_cmd
        assert "bestaudio/best" in called_cmd
        assert "-x" in called_cmd


def test_downloader_retries_on_failure(tmp_path: Path):
    """Verify download_section retries on transient errors and raises DownloadError if exhausted."""
    dl = Downloader(max_retries=2, retry_delay_sec=0.01)
    target = tmp_path / "fail.mp4"

    mock_fail = MagicMock(returncode=1, stderr="Network timeout", stdout="")
    with patch("subprocess.run", return_value=mock_fail) as mock_run:
        with pytest.raises(DownloadError):
            dl.download_section("https://youtu.be/dQw4w9WgXcQ", 0.0, 45.0, target)
        assert mock_run.call_count == 2


# =============================================================================
# 4. Metadata Extraction Tests (yt-dlp with oEmbed Fallback)
# =============================================================================

def test_metadata_fetcher_ytdlp_success():
    """Verify metadata retrieval from simulated yt-dlp --dump-json."""
    fake_json = json.dumps({
        "id": "dQw4w9WgXcQ",
        "title": "Rick Astley - Never Gonna Give You Up",
        "uploader": "RickAstleyVEVO",
        "uploader_id": "RickAstleyVEVO",
        "channel_url": "https://www.youtube.com/@RickAstleyVEVO",
        "duration": 212.0,
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "description": "The official video for Never Gonna Give You Up.",
    })

    fetcher = MetadataFetcher()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=fake_json, stderr=""
        )
        meta = fetcher.fetch("https://youtu.be/dQw4w9WgXcQ")

        assert isinstance(meta, VideoMetadata)
        assert meta.title == "Rick Astley - Never Gonna Give You Up"
        assert meta.channel == "RickAstleyVEVO"
        assert meta.channel_url == "https://www.youtube.com/@RickAstleyVEVO"
        assert meta.duration == 212.0
        assert meta.video_id == "dQw4w9WgXcQ"


def test_metadata_fetcher_fallback_to_oembed():
    """Verify fallback to oEmbed when yt-dlp fails."""
    fetcher = MetadataFetcher()

    oembed_payload = {
        "title": "oEmbed Video Title",
        "author_name": "oEmbed Creator",
        "author_url": "https://www.youtube.com/@oembed_creator",
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = oembed_payload

    # yt-dlp fails
    with patch("subprocess.run", side_effect=FileNotFoundError("yt-dlp missing")):
        with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
            meta = fetcher.fetch("https://youtu.be/dQw4w9WgXcQ")

            assert isinstance(meta, VideoMetadata)
            assert meta.title == "oEmbed Video Title"
            assert meta.channel == "oEmbed Creator"
            assert meta.channel_url == "https://www.youtube.com/@oembed_creator"
            assert meta.duration == 0.0
            assert meta.video_id == "dQw4w9WgXcQ"


def test_metadata_fetcher_both_fail():
    """Verify MetadataFetchError is raised when both yt-dlp and oEmbed fail."""
    fetcher = MetadataFetcher()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "Not Found"

    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "yt-dlp")):
        with patch("httpx.Client.get", return_value=mock_resp):
            with pytest.raises(MetadataFetchError):
                fetcher.fetch("https://youtu.be/dQw4w9WgXcQ")


# =============================================================================
# 5. Transcript Extraction & Caption Fallback Tests
# =============================================================================

def test_transcript_parser_vtt():
    """Verify WebVTT text parser extracts segments with timestamps."""
    vtt_text = """WEBVTT

00:00:01.000 --> 00:00:04.500
<c>Hello</c> and welcome to this video.

00:00:05.000 --> 00:00:08.250
Today we discuss YouTube Shorts automation.
"""
    segments = parse_vtt_text(vtt_text)
    assert len(segments) == 2
    assert segments[0].start == 1.0
    assert segments[0].duration == 3.5
    assert segments[0].end == 4.5
    assert segments[0].text == "Hello and welcome to this video."

    assert segments[1].start == 5.0
    assert segments[1].duration == 3.25
    assert segments[1].text == "Today we discuss YouTube Shorts automation."


def test_transcript_parser_json3():
    """Verify YouTube JSON3 caption parser extracts segments."""
    json3_data = {
        "events": [
            {
                "tStartMs": 1500,
                "dDurationMs": 2500,
                "segs": [{"utf8": "First "}, {"utf8": "segment."}],
            },
            {
                "tStartMs": 4200,
                "dDurationMs": 1800,
                "segs": [{"utf8": "Second segment."}],
            },
        ]
    }
    segments = parse_json3_dict(json3_data)
    assert len(segments) == 2
    assert segments[0].start == 1.5
    assert segments[0].duration == 2.5
    assert segments[0].text == "First segment."
    assert segments[1].start == 4.2
    assert segments[1].duration == 1.8
    assert segments[1].text == "Second segment."


def test_transcript_fetcher_prefers_manual_over_auto():
    """Verify youtube-transcript-api prefers manual captions over auto-generated."""
    import youtube_transcript_api

    mock_manual = MagicMock()
    mock_manual.is_generated = False
    mock_manual.fetch.return_value = [
        {"start": 0.0, "duration": 2.0, "text": "Manual subtitle"}
    ]

    mock_auto = MagicMock()
    mock_auto.is_generated = True
    mock_auto.fetch.return_value = [
        {"start": 0.0, "duration": 2.0, "text": "Auto subtitle"}
    ]

    mock_list = MagicMock()
    mock_list.find_manually_created_transcript.return_value = mock_manual
    mock_list.find_generated_transcript.return_value = mock_auto

    with patch.object(youtube_transcript_api.YouTubeTranscriptApi, "list", return_value=mock_list):
        fetcher = TranscriptFetcher()
        segments = fetcher.fetch("dQw4w9WgXcQ")

        assert len(segments) == 1
        assert segments[0].text == "Manual subtitle"
        mock_list.find_manually_created_transcript.assert_called_once()


def test_transcript_fetcher_ytdlp_fallback():
    """Verify fallback to yt-dlp subtitle extraction when youtube-transcript-api fails."""
    import youtube_transcript_api

    fake_vtt = """WEBVTT
00:00:00.500 --> 00:00:03.000
yt-dlp fallback captions
"""

    def fake_subprocess_subtitles(cmd, **kwargs):
        # If running dump-json:
        if "--dump-json" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps({
                    "subtitles": {},
                    "automatic_captions": {
                        "en": [{"ext": "vtt", "url": "https://example.com/captions.vtt"}]
                    }
                }),
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.text = fake_vtt

    fetcher = TranscriptFetcher()
    with patch.object(youtube_transcript_api.YouTubeTranscriptApi, "list", side_effect=Exception("API blocked")):
        with patch("subprocess.run", side_effect=fake_subprocess_subtitles):
            with patch("httpx.Client.get", return_value=mock_http_resp):
                segments = fetcher.fetch("https://youtu.be/dQw4w9WgXcQ")
                assert len(segments) == 1
                assert segments[0].text == "yt-dlp fallback captions"
                assert segments[0].start == 0.5


def test_transcript_fetcher_no_captions_signal():
    """Verify that when no captions exist, TranscriptNotFoundError is raised and NO_CAPTIONS signal is returned."""
    import youtube_transcript_api

    fetcher = TranscriptFetcher()

    with patch.object(youtube_transcript_api.YouTubeTranscriptApi, "list", side_effect=Exception("No captions")):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")):
            # Method 1: raises TranscriptNotFoundError
            with pytest.raises(TranscriptNotFoundError) as exc_info:
                fetcher.fetch("dQw4w9WgXcQ")
            assert NO_CAPTIONS in str(exc_info.value)

            # Method 2: fetch_or_signal returns (None, NO_CAPTIONS)
            segs, signal = fetcher.fetch_or_signal("dQw4w9WgXcQ")
            assert segs is None
            assert signal == NO_CAPTIONS
            assert signal == "NO_CAPTIONS"

