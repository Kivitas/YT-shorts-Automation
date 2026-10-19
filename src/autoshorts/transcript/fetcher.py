"""YouTube caption and transcript extraction with tiered fallback and Whisper signaling.

Primary: youtube-transcript-api (prefers manually created over automated captions).
Secondary: yt-dlp subtitle download and JSON3/VTT parsing.
Signal: NO_CAPTIONS sentinel and TranscriptNotFoundError when captions are missing,
prompting the orchestrator to invoke Whisper audio-only transcription.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence

import httpx
from pydantic import BaseModel, Field

from autoshorts.core.exceptions import TranscriptNotFoundError
from autoshorts.discovery.url_validator import extract_video_id, normalize_youtube_url

logger = logging.getLogger(__name__)

# Explicit sentinel signal for orchestrator / pipeline to route to Whisper audio transcription
NO_CAPTIONS = "NO_CAPTIONS"


class TranscriptSegment(BaseModel):
    """Normalized timed transcript segment."""

    start: float = Field(..., ge=0.0, description="Start time in seconds")
    duration: float = Field(..., ge=0.0, description="Duration in seconds")
    text: str = Field(..., description="Spoken transcript text")

    @property
    def end(self) -> float:
        """End timestamp in seconds."""
        return round(self.start + self.duration, 3)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation matching contracts.Transcriber schema."""
        return {
            "start": self.start,
            "duration": self.duration,
            "text": self.text,
            "end": self.end,
        }


def _parse_vtt_timestamp(ts_str: str) -> float:
    """Parse a WebVTT timestamp string (HH:MM:SS.mmm or MM:SS.mmm) into seconds."""
    parts = ts_str.strip().split(":")
    if len(parts) == 3:
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        return hours * 3600.0 + minutes * 60.0 + seconds
    elif len(parts) == 2:
        minutes = float(parts[0])
        seconds = float(parts[1])
        return minutes * 60.0 + seconds
    return float(parts[0])


def parse_vtt_text(vtt_content: str) -> list[TranscriptSegment]:
    """Parse raw WebVTT content into normalized TranscriptSegment objects."""
    segments: list[TranscriptSegment] = []
    lines = vtt_content.splitlines()

    time_pattern = re.compile(
        r"(\d{1,2}:)?\d{2}:\d{2}\.\d{3}\s+-->\s+(\d{1,2}:)?\d{2}:\d{2}\.\d{3}"
    )

    idx = 0
    num_lines = len(lines)
    while idx < num_lines:
        line = lines[idx].strip()
        match = time_pattern.search(line)
        if match:
            time_parts = line.split("-->")
            start_ts = _parse_vtt_timestamp(time_parts[0].split()[0])
            end_ts = _parse_vtt_timestamp(time_parts[1].split()[0])
            dur = max(0.0, round(end_ts - start_ts, 3))

            # Collect subsequent text lines until next timestamp or blank line
            text_lines: list[str] = []
            idx += 1
            while idx < num_lines and lines[idx].strip() and not time_pattern.search(lines[idx]):
                clean_text = re.sub(r"<[^>]+>", "", lines[idx].strip())  # Strip VTT styling tags
                if clean_text:
                    text_lines.append(clean_text)
                idx += 1

            full_text = " ".join(text_lines).strip()
            if full_text:
                segments.append(
                    TranscriptSegment(
                        start=round(start_ts, 3),
                        duration=dur,
                        text=full_text,
                    )
                )
            continue
        idx += 1

    return segments


def parse_json3_dict(data: dict[str, Any]) -> list[TranscriptSegment]:
    """Parse YouTube JSON3 captions dictionary into TranscriptSegment objects."""
    segments: list[TranscriptSegment] = []
    events = data.get("events", [])
    for event in events:
        if "segs" not in event or "tStartMs" not in event:
            continue
        start_sec = event["tStartMs"] / 1000.0
        dur_sec = event.get("dDurationMs", 0) / 1000.0
        text_pieces = [s.get("utf8", "") for s in event["segs"] if "utf8" in s]
        text = "".join(text_pieces).strip()
        if text and text != "\n":
            segments.append(
                TranscriptSegment(
                    start=round(start_sec, 3),
                    duration=round(dur_sec, 3),
                    text=text,
                )
            )
    return segments


class TranscriptFetcher:
    """Manages transcript retrieval across youtube-transcript-api and yt-dlp subtitles."""

    def __init__(
        self,
        languages: Sequence[str] = ("en", "en-US", "en-GB"),
        yt_dlp_binary: str = "yt-dlp",
        timeout_sec: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Initialize TranscriptFetcher.

        Args:
            languages: Sequence of language codes in descending order of preference.
            yt_dlp_binary: Executable name/path for yt-dlp.
            timeout_sec: Timeout for fallback operations.
            http_client: Optional httpx.Client for fetching subtitle URLs.
        """
        self.languages = list(languages)
        self.yt_dlp_binary = yt_dlp_binary
        self.timeout_sec = timeout_sec
        self._client = http_client

    def fetch(self, url_or_video_id: str) -> list[TranscriptSegment]:
        """Fetch transcript segments, preferring manual then auto captions.

        Tries youtube-transcript-api first, then yt-dlp subtitle download.
        If no captions are discovered, raises TranscriptNotFoundError.

        Args:
            url_or_video_id: YouTube video URL or 11-char ID.

        Returns:
            List of normalized TranscriptSegment objects.

        Raises:
            TranscriptNotFoundError: If no transcripts can be found.
        """
        vid = extract_video_id(url_or_video_id)
        canonical_url = normalize_youtube_url(url_or_video_id)

        # 1. Primary Attempt: youtube-transcript-api
        try:
            segments = self._fetch_via_transcript_api(vid)
            if segments:
                logger.info("Successfully fetched %d transcript segments via youtube-transcript-api for %s", len(segments), vid)
                return segments
        except Exception as exc:
            logger.info("youtube-transcript-api failed for %s (%s). Attempting yt-dlp subtitle fallback.", vid, exc)

        # 2. Secondary Attempt: yt-dlp subtitles
        try:
            segments = self._fetch_via_ytdlp(canonical_url, vid)
            if segments:
                logger.info("Successfully fetched %d transcript segments via yt-dlp for %s", len(segments), vid)
                return segments
        except Exception as exc:
            logger.warning("yt-dlp subtitle fallback failed for %s (%s).", vid, exc)

        # 3. No captions available: Signal NO_CAPTIONS
        logger.warning("No captions found for video %s. Signaling NO_CAPTIONS for Whisper fallback.", vid)
        raise TranscriptNotFoundError(
            f"No captions available for YouTube video {vid}. Signaling {NO_CAPTIONS} for Whisper fallback."
        )

    def fetch_or_signal(
        self,
        url_or_video_id: str,
    ) -> tuple[list[TranscriptSegment] | None, str | None]:
        """Convenience method returning (segments, None) or (None, NO_CAPTIONS).

        Allows callers to cleanly inspect the return value rather than catching exceptions.
        """
        try:
            segments = self.fetch(url_or_video_id)
            return segments, None
        except TranscriptNotFoundError:
            return None, NO_CAPTIONS

    def _fetch_via_transcript_api(self, video_id: str) -> list[TranscriptSegment]:
        """Use youtube-transcript-api, prioritizing manually created over generated captions."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError as e:
            raise RuntimeError(
                "youtube-transcript-api is not installed. Please install it with 'pip install youtube-transcript-api'."
            ) from e

        # Support both modern (api = YouTubeTranscriptApi(); api.list(video_id))
        # and legacy (YouTubeTranscriptApi.list_transcripts(video_id))
        if hasattr(YouTubeTranscriptApi, "list_transcripts"):
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        elif hasattr(YouTubeTranscriptApi, "list"):
            try:
                transcript_list = YouTubeTranscriptApi().list(video_id)
            except TypeError:
                transcript_list = YouTubeTranscriptApi.list(video_id)
        else:
            transcript_list = YouTubeTranscriptApi().list(video_id)

        chosen_transcript = None

        # Priority 1: Manually created transcript in preferred languages
        try:
            chosen_transcript = transcript_list.find_manually_created_transcript(self.languages)
        except Exception:
            pass

        # Priority 2: Auto-generated transcript in preferred languages
        if chosen_transcript is None:
            try:
                chosen_transcript = transcript_list.find_generated_transcript(self.languages)
            except Exception:
                pass

        # Priority 3: Any transcript in preferred languages
        if chosen_transcript is None:
            try:
                chosen_transcript = transcript_list.find_transcript(self.languages)
            except Exception:
                pass

        # Priority 4: First available manual or auto transcript
        if chosen_transcript is None:
            for t in transcript_list:
                if not getattr(t, "is_generated", False):
                    chosen_transcript = t
                    break
            if chosen_transcript is None:
                for t in transcript_list:
                    chosen_transcript = t
                    break

        if chosen_transcript is None:
            raise TranscriptNotFoundError(f"No transcripts found in transcript list for {video_id}")

        raw_items = chosen_transcript.fetch()
        segments: list[TranscriptSegment] = []
        for item in raw_items:
            if isinstance(item, dict):
                text = (item.get("text") or "").strip()
                start = float(item.get("start", 0.0))
                duration = float(item.get("duration", 0.0))
            else:
                text = (getattr(item, "text", "") or "").strip()
                start = float(getattr(item, "start", 0.0))
                duration = float(getattr(item, "duration", 0.0))

            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    start=round(start, 3),
                    duration=round(duration, 3),
                    text=text,
                )
            )

        if not segments:
            raise TranscriptNotFoundError(f"Transcript fetched for {video_id} was empty")

        return segments

    def _fetch_via_ytdlp(self, canonical_url: str, video_id: str) -> list[TranscriptSegment]:
        """Extract subtitles via yt-dlp metadata or subtitle export."""
        cmd = [
            self.yt_dlp_binary,
            "--dump-json",
            "--skip-download",
            "--no-playlist",
            "--no-warnings",
            canonical_url,
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout_sec,
            check=False,
        )
        if proc.returncode != 0:
            raise TranscriptNotFoundError(f"yt-dlp command failed with code {proc.returncode}")

        data = json.loads(proc.stdout)
        subs = data.get("subtitles", {})
        auto_subs = data.get("automatic_captions", {})

        # Find target subtitle entry (manual preferred over auto)
        target_entry: list[dict[str, Any]] | None = None
        for lang in self.languages:
            if lang in subs:
                target_entry = subs[lang]
                break
        if target_entry is None:
            for lang in self.languages:
                if lang in auto_subs:
                    target_entry = auto_subs[lang]
                    break

        # Fallback to any en language code
        if target_entry is None:
            for k, v in subs.items():
                if k.startswith("en"):
                    target_entry = v
                    break
        if target_entry is None:
            for k, v in auto_subs.items():
                if k.startswith("en"):
                    target_entry = v
                    break

        # If a subtitle format URL is found, download and parse
        if target_entry:
            # Prefer json3 or vtt
            json3_url = next((f["url"] for f in target_entry if f.get("ext") == "json3" and "url" in f), None)
            vtt_url = next((f["url"] for f in target_entry if f.get("ext") == "vtt" and "url" in f), None)

            if json3_url:
                segments = self._fetch_and_parse_json3(json3_url)
                if segments:
                    return segments
            if vtt_url:
                segments = self._fetch_and_parse_vtt(vtt_url)
                if segments:
                    return segments

        # If URL parsing wasn't possible, attempt subprocess subtitle download
        return self._fetch_via_ytdlp_file_download(canonical_url)

    def _fetch_and_parse_json3(self, url: str) -> list[TranscriptSegment]:
        """Fetch JSON3 from URL and parse into segments."""
        def _get(c: httpx.Client) -> httpx.Response:
            return c.get(url, timeout=self.timeout_sec)

        if self._client:
            resp = _get(self._client)
        else:
            with httpx.Client() as client:
                resp = _get(client)

        if resp.status_code == 200:
            return parse_json3_dict(resp.json())
        return []

    def _fetch_and_parse_vtt(self, url: str) -> list[TranscriptSegment]:
        """Fetch WebVTT from URL and parse into segments."""
        def _get(c: httpx.Client) -> httpx.Response:
            return c.get(url, timeout=self.timeout_sec)

        if self._client:
            resp = _get(self._client)
        else:
            with httpx.Client() as client:
                resp = _get(client)

        if resp.status_code == 200:
            return parse_vtt_text(resp.text)
        return []

    def _fetch_via_ytdlp_file_download(self, canonical_url: str) -> list[TranscriptSegment]:
        """Run yt-dlp to download .vtt subtitle file into temp directory and parse."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            out_template = str(tmp_path / "sub")

            cmd = [
                self.yt_dlp_binary,
                "--skip-download",
                "--write-sub",
                "--write-auto-sub",
                "--sub-lang", ",".join(self.languages),
                "--sub-format", "vtt",
                "-o", out_template,
                canonical_url,
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
            vtt_files = list(tmp_path.glob("*.vtt"))
            if vtt_files:
                vtt_text = vtt_files[0].read_text(encoding="utf-8")
                segments = parse_vtt_text(vtt_text)
                if segments:
                    return segments

        raise TranscriptNotFoundError(f"yt-dlp failed to download subtitles for {canonical_url}")


def fetch_transcript(
    url_or_video_id: str,
    languages: Sequence[str] = ("en", "en-US", "en-GB"),
) -> list[TranscriptSegment]:
    """Convenience helper to fetch transcript segments directly."""
    fetcher = TranscriptFetcher(languages=languages)
    return fetcher.fetch(url_or_video_id)


def fetch_transcript_or_signal(
    url_or_video_id: str,
    languages: Sequence[str] = ("en", "en-US", "en-GB"),
) -> tuple[list[TranscriptSegment] | None, str | None]:
    """Convenience helper returning (segments, None) or (None, NO_CAPTIONS)."""
    fetcher = TranscriptFetcher(languages=languages)
    return fetcher.fetch_or_signal(url_or_video_id)


__all__ = [
    "NO_CAPTIONS",
    "TranscriptFetcher",
    "TranscriptNotFoundError",
    "TranscriptSegment",
    "fetch_transcript",
    "fetch_transcript_or_signal",
    "parse_json3_dict",
    "parse_vtt_text",
]
