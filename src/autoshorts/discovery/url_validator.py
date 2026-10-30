"""YouTube URL validation, normalization, and custom instruction parsing.

Handles diverse YouTube URL variations (standard watch, youtu.be, shorts, embed,
music, live, nocookie), strips tracking and extraneous parameters, enforces
11-character video ID validation, and parses per-link and global instruction overrides.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autoshorts.core.config import JobDefaultsConfig
from autoshorts.core.exceptions import InvalidURLError

# YouTube 11-character video ID regex (alphanumeric, underscores, hyphens)
# \A and \Z, not ^ and $: "$" also matches before a trailing newline, which let an ID
# such as "dQw4w9WgXcQ\n" (from a percent-encoded query) through into the canonical URL.
YOUTUBE_VIDEO_ID_REGEX = re.compile(r"\A[a-zA-Z0-9_-]{11}\Z")

# Regex to match or find YouTube URLs embedded within text or with query params
YOUTUBE_URL_FINDER_REGEX = re.compile(
    r"(?:https?://)?(?:[a-zA-Z0-9-]+\.)?(?:youtube\.com/(?:watch\?(?:[^&\s]+&)*v=|shorts/|live/|embed/|v/)|youtu\.be/)([a-zA-Z0-9_-]{11})"
)

# Recognized YouTube domain host suffixes
ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "gaming.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


class ParsedLink(BaseModel):
    """Structured representation of a validated YouTube link with instruction overrides."""

    video_id: str = Field(..., description="YouTube 11-char video ID")
    canonical_url: str = Field(
        ..., description="Clean canonical URL (https://www.youtube.com/watch?v=...)"
    )
    raw_input: str = Field(..., description="Original raw input line")
    clips_per_video: int = Field(
        default=1, ge=1, description="Number of clips to cut from this video"
    )
    min_duration_seconds: int = Field(
        default=30, ge=10, description="Minimum clip duration in seconds"
    )
    max_duration_seconds: int = Field(
        default=50, le=60, description="Maximum clip duration in seconds"
    )
    window_stride_seconds: int = Field(
        default=15, ge=5, description="Window stride step in seconds"
    )
    focus_prompt: str | None = Field(
        default=None, description="Topic, humor, or editorial focus angle"
    )
    background_style: str | None = Field(
        default=None, description="Theme (e.g. minecraft, subway, auto)"
    )
    dopamine_level: str | None = Field(
        default=None, description="Compositor energy preset: low, medium, high"
    )
    custom_instructions: str | None = Field(default=None, description="Combined user instructions")
    extra_options: dict[str, Any] = Field(
        default_factory=dict, description="Additional unmapped options"
    )


def extract_video_id(url_or_id: str) -> str:
    """Extract and validate the 11-character YouTube video ID from a URL or raw ID string.

    Supports:
        - Direct ID: 'dQw4w9WgXcQ'
        - Standard watch: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
        - Shortened: 'https://youtu.be/dQw4w9WgXcQ'
        - Shorts: 'https://www.youtube.com/shorts/dQw4w9WgXcQ'
        - Embed: 'https://www.youtube.com/embed/dQw4w9WgXcQ'
        - Live: 'https://www.youtube.com/live/dQw4w9WgXcQ'
        - Music: 'https://music.youtube.com/watch?v=dQw4w9WgXcQ'
        - Mobile: 'https://m.youtube.com/watch?v=dQw4w9WgXcQ'

    Strips tracking queries (si, feature, utm_*, fbclid, etc.).

    Args:
        url_or_id: Input YouTube URL or 11-character ID.

    Returns:
        The validated 11-character video ID string.

    Raises:
        InvalidURLError: If the input is not a recognized YouTube URL or ID is invalid.
    """
    if not url_or_id or not isinstance(url_or_id, str):
        raise InvalidURLError("Input YouTube URL or ID cannot be empty")

    cleaned = url_or_id.strip()

    # Direct 11-char ID check
    if YOUTUBE_VIDEO_ID_REGEX.match(cleaned):
        return cleaned

    # Direct regex match for any embedded YouTube URL
    match = YOUTUBE_URL_FINDER_REGEX.search(cleaned)
    if match:
        return match.group(1)

    # Ensure URL has scheme for parsing if domain is provided
    if not cleaned.startswith(("http://", "https://")):
        cleaned = "https://" + cleaned

    try:
        parsed = urllib.parse.urlparse(cleaned)
    except Exception as e:
        raise InvalidURLError(f"Failed to parse URL '{url_or_id}': {e}") from e

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise InvalidURLError(f"Missing hostname in URL: '{url_or_id}'")

    # Verify domain is YouTube
    is_valid_domain = any(
        hostname == allowed or hostname.endswith("." + allowed)
        for allowed in ("youtube.com", "youtu.be", "youtube-nocookie.com")
    )
    if not is_valid_domain:
        raise InvalidURLError(f"URL domain '{hostname}' is not a recognized YouTube domain")

    video_id: str | None = None
    path = parsed.path.rstrip("/")
    path_segments = [p for p in path.split("/") if p]

    # Handle youtu.be/<id>
    if "youtu.be" in hostname:
        if path_segments:
            video_id = path_segments[0]

    # Handle youtube.com/shorts/<id>, /embed/<id>, /live/<id>, /v/<id>
    elif any(path.startswith(f"/{prefix}/") for prefix in ("shorts", "embed", "live", "v")):
        for prefix in ("shorts", "embed", "live", "v"):
            if path.startswith(f"/{prefix}/") and len(path_segments) >= 2:
                video_id = path_segments[1]
                break

    # Handle standard watch query: ?v=<id>
    elif "watch" in path_segments or not path_segments or path == "":
        query_params = urllib.parse.parse_qs(parsed.query)
        v_list = query_params.get("v")
        if v_list and len(v_list) > 0:
            video_id = v_list[0]

    # Fallback: check query param v on any path
    if not video_id:
        query_params = urllib.parse.parse_qs(parsed.query)
        v_list = query_params.get("v")
        if v_list and len(v_list) > 0:
            video_id = v_list[0]

    if not video_id or not YOUTUBE_VIDEO_ID_REGEX.match(video_id):
        raise InvalidURLError(
            f"Could not extract a valid 11-character YouTube video ID from '{url_or_id}'"
        )

    return video_id


def normalize_youtube_url(url_or_id: str) -> str:
    """Normalize any YouTube URL or video ID into a canonical watch URL.

    Canonical format: 'https://www.youtube.com/watch?v={video_id}'.
    Strips all tracking, timestamp, and playlist query parameters.

    Args:
        url_or_id: Input YouTube URL or 11-char ID.

    Returns:
        Canonical watch URL string.
    """
    vid = extract_video_id(url_or_id)
    return f"https://www.youtube.com/watch?v={vid}"


def is_valid_youtube_url(url_or_id: str) -> bool:
    """Return True if the input is a valid YouTube URL or video ID, False otherwise."""
    try:
        extract_video_id(url_or_id)
        return True
    except (InvalidURLError, ValueError, TypeError):
        return False


def _parse_time_seconds(val_str: str) -> int:
    """Parse a time string like '45s', '45 sec', '45', '1m' into integer seconds."""
    s = val_str.lower().strip()
    if s.endswith(("sec", "sec.")):
        s = s.rstrip(".sec").strip()
    elif s.endswith("s"):
        s = s[:-1].strip()
    elif s.endswith(("m", "min")):
        s = s.rstrip("min").rstrip("m").strip()
        return int(float(s) * 60)
    return int(float(s))


def parse_link_line(
    line: str,
    defaults: JobDefaultsConfig | dict[str, Any] | None = None,
    global_instructions: str | None = None,
) -> ParsedLink:
    """Parse a single line containing a URL and optional custom instruction overrides.

    Supported syntax formats:
        - 'https://youtube.com/watch?v=XYZ | clips 2 | max 45s | focus funny | bg subway'
        - 'https://youtu.be/XYZ | clips: 2 | max_s: 45 | focus: funny reactions | bg: subway | dopamine: high'
        - 'https://youtube.com/shorts/XYZ | focus on technical details'

    Args:
        line: Raw line string.
        defaults: Optional JobDefaultsConfig or dictionary of fallback defaults.
        global_instructions: Optional global instruction string to merge.

    Returns:
        Validated ParsedLink instance.

    Raises:
        InvalidURLError: If the URL component cannot be validated.
    """
    raw_line = line.strip()
    if not raw_line:
        raise InvalidURLError("Cannot parse empty line")

    if " : " in raw_line and "|" not in raw_line:
        parts = raw_line.split(" : ", 1)
        raw_line = f"{parts[0].strip()} | focus: {parts[1].strip()}"

    # Split on pipe '|'
    tokens = [t.strip() for t in raw_line.split("|")]
    url_token = tokens[0]
    override_tokens = tokens[1:]

    prefix_label = None
    vid_match = YOUTUBE_URL_FINDER_REGEX.search(url_token)
    if vid_match:
        vid = vid_match.group(1)
        pre = url_token[:vid_match.start()].strip()
        post = url_token[vid_match.end():].strip()
        if pre and not pre.startswith(("http://", "https://")):
            prefix_label = pre
        if post and not post.startswith("?"):
            override_tokens.append(post)
    else:
        vid = extract_video_id(url_token)
    canonical = f"https://www.youtube.com/watch?v={vid}"

    # Extract base defaults
    def_clips = 1
    def_min_s = 30
    def_max_s = 50
    def_stride = 15
    def_focus: str | None = None
    def_bg: str | None = None
    def_dopamine: str | None = None

    if defaults:
        if isinstance(defaults, JobDefaultsConfig):
            def_clips = defaults.clips_per_video
            def_min_s = defaults.min_duration_seconds
            def_max_s = defaults.max_duration_seconds
            def_stride = defaults.window_stride_seconds
            def_focus = defaults.focus_prompt
            def_bg = getattr(
                defaults, "background_category", getattr(defaults, "background_style", "minecraft")
            )
            def_dopamine = defaults.dopamine_level
        elif isinstance(defaults, dict):
            def_clips = int(defaults.get("clips_per_video", def_clips))
            def_min_s = int(defaults.get("min_duration_seconds", def_min_s))
            def_max_s = int(defaults.get("max_duration_seconds", def_max_s))
            def_stride = int(defaults.get("window_stride_seconds", def_stride))
            def_focus = defaults.get("focus_prompt", def_focus)
            def_bg = (
                defaults.get("background_category") or defaults.get("background_style") or def_bg
            )
            def_dopamine = defaults.get("dopamine_level", def_dopamine)

    clips = def_clips
    min_s = def_min_s
    max_s = def_max_s
    stride = def_stride
    focus = def_focus
    bg = def_bg
    dopamine = def_dopamine
    unhandled_instructions: list[str] = []
    extra_options: dict[str, Any] = {}

    for token in override_tokens:
        if not token:
            continue

        # Check for key: value or key=value or key value
        key = ""
        val = ""
        if ":" in token:
            parts = token.split(":", 1)
            key = parts[0].strip().lower()
            val = parts[1].strip()
        elif "=" in token:
            parts = token.split("=", 1)
            key = parts[0].strip().lower()
            val = parts[1].strip()
        else:
            parts = token.split(None, 1)
            if len(parts) == 2:
                key = parts[0].strip().lower()
                val = parts[1].strip()
            else:
                unhandled_instructions.append(token)
                continue

        # Match known keys
        if key in ("clips", "clips_per_video", "clip_count", "clips_count"):
            try:
                clips = int(val)
            except (ValueError, OverflowError):
                unhandled_instructions.append(token)
        elif key in ("max", "max_s", "max_duration", "max_sec", "max_duration_seconds"):
            try:
                max_s = _parse_time_seconds(val)
            except (ValueError, OverflowError):
                unhandled_instructions.append(token)
        elif key in ("min", "min_s", "min_duration", "min_sec", "min_duration_seconds"):
            try:
                min_s = _parse_time_seconds(val)
            except (ValueError, OverflowError):
                unhandled_instructions.append(token)
        elif key in ("stride", "window_stride", "window_stride_seconds", "stride_seconds"):
            try:
                stride = _parse_time_seconds(val)
            except (ValueError, OverflowError):
                unhandled_instructions.append(token)
        elif key in ("focus", "focus_prompt", "angle", "prompt", "topic"):
            focus = val
        elif key in ("bg", "background", "background_style", "background_category", "theme"):
            bg = val
        elif key in ("dopamine", "dopamine_level", "energy"):
            dopamine = val.lower()
        else:
            # Stash in extra options or unhandled
            extra_options[key] = val
            unhandled_instructions.append(token)

    # Compose custom instructions text
    combined_notes: list[str] = []
    if global_instructions and global_instructions.strip():
        combined_notes.append(global_instructions.strip())
    if unhandled_instructions:
        combined_notes.extend(unhandled_instructions)

    if prefix_label:
        if not focus:
            focus = prefix_label
        elif prefix_label not in combined_notes:
            combined_notes.insert(0, prefix_label)

    final_custom_instructions = " | ".join(combined_notes) if combined_notes else None

    return ParsedLink(
        video_id=vid,
        canonical_url=canonical,
        raw_input=raw_line,
        clips_per_video=max(1, clips),
        min_duration_seconds=min(max(10, min_s), 60),
        max_duration_seconds=min(60, max(min_s, max_s)),
        window_stride_seconds=max(5, stride),
        focus_prompt=focus,
        background_style=bg,
        dopamine_level=dopamine,
        custom_instructions=final_custom_instructions,
        extra_options=extra_options,
    )


def parse_links_input(
    input_data: str | Sequence[str] | Path | os.PathLike[str],
    defaults: JobDefaultsConfig | dict[str, Any] | None = None,
    global_instructions: str | None = None,
    dedupe: bool = True,
) -> list[ParsedLink]:
    """Parse multiple links and custom instructions from textarea string, file, or list.

    Args:
        input_data: Multiline string, sequence of lines, or Path to links file.
        defaults: Optional JobDefaultsConfig or dictionary of fallback defaults.
        global_instructions: Global instructions applied across all parsed items.
        dedupe: If True, duplicate video IDs are removed (first occurrence preserved).

    Returns:
        List of validated ParsedLink objects.
    """
    raw_lines: list[str] = []

    if isinstance(input_data, (Path, os.PathLike)):
        p = Path(input_data).resolve()
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            raw_lines = content.splitlines()
        else:
            raise InvalidURLError(f"Links file not found: {p}")
    elif isinstance(input_data, str):
        # Check if it's a file path
        possible_path = Path(input_data.strip())
        if possible_path.is_file():
            content = possible_path.read_text(encoding="utf-8")
            raw_lines = content.splitlines()
        else:
            # Could be multiline or comma-separated
            lines = [line.strip() for line in input_data.splitlines() if line.strip()]
            if len(lines) == 1 and "," in lines[0] and "|" not in lines[0]:
                raw_lines = [item.strip() for item in lines[0].split(",") if item.strip()]
            else:
                raw_lines = lines
    elif isinstance(input_data, Sequence):
        raw_lines = [str(item).strip() for item in input_data if str(item).strip()]
    else:
        raise InvalidURLError(f"Unsupported input type for links parser: {type(input_data)}")

    results: list[ParsedLink] = []
    seen_ids: set[str] = set()

    for line in raw_lines:
        line_clean = line.strip()
        # Skip empty lines and comments
        if not line_clean or line_clean.startswith("#"):
            continue

        # Extract hashtag lines or instruction lines that contain no URLs
        url_matches = list(YOUTUBE_URL_FINDER_REGEX.finditer(line_clean))
        if not url_matches:
            # Check for hashtag tokens to merge into global instructions
            tags = [t for t in line_clean.split() if t.startswith("#")]
            if tags:
                tag_str = " ".join(tags)
                global_instructions = (
                    f"{global_instructions} | tags: {tag_str}"
                    if global_instructions
                    else f"tags: {tag_str}"
                )
            continue

        if len(url_matches) == 1:
            try:
                parsed_link = parse_link_line(
                    line_clean,
                    defaults=defaults,
                    global_instructions=global_instructions,
                )
                if dedupe:
                    if parsed_link.video_id in seen_ids:
                        continue
                    seen_ids.add(parsed_link.video_id)
                results.append(parsed_link)
            except InvalidURLError:
                continue
        else:
            # Multiple YouTube URLs on a single line (e.g. 'HAIX https://... https://...')
            pre_label = line_clean[:url_matches[0].start()].strip()
            for m in url_matches:
                vid = m.group(1)
                canonical = f"https://www.youtube.com/watch?v={vid}"
                sub_line = f"{canonical} | focus: {pre_label}" if pre_label else canonical
                try:
                    parsed_link = parse_link_line(
                        sub_line,
                        defaults=defaults,
                        global_instructions=global_instructions,
                    )
                    if dedupe:
                        if parsed_link.video_id in seen_ids:
                            continue
                        seen_ids.add(parsed_link.video_id)
                    results.append(parsed_link)
                except InvalidURLError:
                    continue

    if not results:
        raise InvalidURLError("No valid YouTube URLs found in input.")

    return results


__all__ = [
    "ALLOWED_YOUTUBE_HOSTS",
    "YOUTUBE_VIDEO_ID_REGEX",
    "YOUTUBE_URL_FINDER_REGEX",
    "InvalidURLError",
    "ParsedLink",
    "extract_video_id",
    "is_valid_youtube_url",
    "normalize_youtube_url",
    "parse_link_line",
    "parse_links_input",
]
