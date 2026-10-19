"""Transcript extraction package for AutoShorts.

Provides youtube-transcript-api and yt-dlp subtitle retrieval, timestamp normalization,
and the NO_CAPTIONS sentinel signal for fallback routing to Whisper.
"""

from autoshorts.transcript.fetcher import (
    NO_CAPTIONS,
    TranscriptFetcher,
    TranscriptNotFoundError,
    TranscriptSegment,
    fetch_transcript,
    fetch_transcript_or_signal,
    parse_json3_dict,
    parse_vtt_text,
)

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
