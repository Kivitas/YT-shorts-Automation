"""Research helpers for Wojak long-form videos.

Two layers, both standard-library plus libraries the project already ships:

1. Channel format profiles (primary). ``build_format_profile`` lists a YouTube channel's
   recent uploads with yt-dlp (flat, no downloads), samples a few captions with
   youtube-transcript-api, and summarises the format: video count, median duration,
   title templates, caption segment count, and typical caption length. Profiles are
   cached as JSON per channel.
2. Wikipedia fact cards (optional context). ``fetch_fact_card`` looks up a topic on the
   Wikipedia REST summary endpoint, with a MediaWiki search fallback.

Every network call sits behind an injectable callable so tests stay offline. Lookups
never raise for network or parsing problems; they return None or an empty result and
log the topic or channel.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import statistics
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from autoshorts.core.paths import atomic_write, get_data_dir

logger = logging.getLogger(__name__)

USER_AGENT = "AutoShortsWojak/1.0 (local tool)"
TIMEOUT_SECONDS = 6.0
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.5
MAX_RESPONSE_BYTES = 2_000_000

SUMMARY_URL_TEMPLATE = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
SEARCH_URL = "https://en.wikipedia.org/w/api.php"
PAGE_URL_TEMPLATE = "https://en.wikipedia.org/wiki/{title}"

FACT_CACHE_SUBDIR = ("cache", "wojak_fact_cards")
PROFILE_CACHE_SUBDIR = ("cache", "wojak_channel_profiles")
DEFAULT_VIDEO_LIMIT = 20
DEFAULT_CAPTION_SAMPLE = 5

# An opener takes a URL and returns the response body. It signals failure by raising:
# urllib.error.HTTPError (404 means "no such page"), OSError (network or timeout), or any
# other exception.
Opener = Callable[[str], str | bytes]
# A lister takes (channel_url, limit) and returns raw yt-dlp flat entry dicts, newest first.
VideoLister = Callable[[str, int], list[dict[str, Any]]]
# A caption fetcher takes a video id and returns the caption segment texts in order.
CaptionFetcher = Callable[[str], list[str]]

_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "no", "inc", "ltd"})
_TRAILING_JUNK = " \t\r\n,;:"


# ==============================================================================
# Channel format profiles
# ==============================================================================


@dataclass(frozen=True)
class ChannelVideo:
    """One upload from a channel listing."""

    video_id: str
    title: str
    duration_seconds: float | None


@dataclass(frozen=True)
class FormatProfile:
    """Summary of a channel's recent video format.

    Attributes:
        channel_url: Normalised ``.../videos`` URL that was listed.
        video_count: Number of uploads listed (capped by the video limit).
        median_duration_seconds: Median length of the listed uploads.
        title_patterns: ``(template, count)`` pairs, most frequent first. Templates use
            ``{X}`` for the topic slot, e.g. ``("Types of {X} Be Like", 7)``.
        segment_count_estimate: Median number of caption segments in the sampled videos.
        typical_caption_chars: Median character length of one caption segment.
        captioned_videos: How many sampled videos returned captions.
        recent_videos: The listed uploads, newest first.
    """

    channel_url: str
    video_count: int
    median_duration_seconds: float | None
    title_patterns: tuple[tuple[str, int], ...]
    segment_count_estimate: int | None
    typical_caption_chars: int | None
    captioned_videos: int
    recent_videos: tuple[ChannelVideo, ...]


# Ordered most specific first. A title counts toward the first template it matches.
_TITLE_TEMPLATES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Types of {X} Be Like", re.compile(r"^types of .+? be like\b", re.IGNORECASE)),
    ("Every Country's {X}", re.compile(r"^every country['’]s?\b", re.IGNORECASE)),
    ("Every {X} in a Nutshell", re.compile(r"^every .+? in a nutshell\b", re.IGNORECASE)),
    ("Every {X} Be Like", re.compile(r"^every .+? be like\b", re.IGNORECASE)),
    ("{X} Be Like", re.compile(r".+? be like\b", re.IGNORECASE)),
    ("Every {X}", re.compile(r"^every\b", re.IGNORECASE)),
)


def summarize_title_patterns(titles: Iterable[str]) -> tuple[tuple[str, int], ...]:
    """Count which title template each title matches, most frequent first.

    Titles that match no template are ignored. Ties keep template order.
    """
    counts = dict.fromkeys((name for name, _ in _TITLE_TEMPLATES), 0)
    for title in titles:
        for name, pattern in _TITLE_TEMPLATES:
            if pattern.search(title.strip()):
                counts[name] += 1
                break
    order = {name: index for index, (name, _) in enumerate(_TITLE_TEMPLATES)}
    ranked = sorted(
        ((name, count) for name, count in counts.items() if count),
        key=lambda item: (-item[1], order[item[0]]),
    )
    return tuple(ranked)


def normalize_channel_videos_url(channel_url: str) -> str:
    """Return the channel's uploads URL, appending ``/videos`` when no tab is given."""
    url = channel_url.strip().rstrip("/")
    if re.search(r"/(videos|shorts|streams)$", url):
        return url
    return f"{url}/videos"


class _YtDlpLogger:
    """Routes yt-dlp output into our logger so nothing hits the console encoding."""

    def debug(self, message: str) -> None:
        logger.debug("yt-dlp: %s", _ascii(message))

    def info(self, message: str) -> None:
        logger.debug("yt-dlp: %s", _ascii(message))

    def warning(self, message: str) -> None:
        logger.debug("yt-dlp warning: %s", _ascii(message))

    def error(self, message: str) -> None:
        logger.warning("yt-dlp error: %s", _ascii(message))


def _ascii(text: str) -> str:
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def _default_lister(channel_url: str, limit: int) -> list[dict[str, Any]]:
    import yt_dlp

    options: dict[str, Any] = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "playlistend": limit,
        "logger": _YtDlpLogger(),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    entries = info.get("entries") if isinstance(info, dict) else None
    return [entry for entry in (entries or []) if isinstance(entry, dict)]


def _default_caption_fetcher(video_id: str) -> list[str]:
    from youtube_transcript_api import YouTubeTranscriptApi

    transcript = YouTubeTranscriptApi().fetch(video_id, languages=("en",))
    return [snippet.text for snippet in transcript.snippets]


def _to_channel_video(entry: dict[str, Any]) -> ChannelVideo | None:
    video_id = entry.get("id")
    if not isinstance(video_id, str) or not video_id.strip():
        return None
    title = entry.get("title")
    duration = entry.get("duration")
    return ChannelVideo(
        video_id=video_id.strip(),
        title=title if isinstance(title, str) else "",
        duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
    )


def list_channel_videos(
    channel_url: str,
    *,
    limit: int = DEFAULT_VIDEO_LIMIT,
    lister: VideoLister | None = None,
) -> list[ChannelVideo]:
    """List up to ``limit`` recent uploads for a channel. Returns [] on failure."""
    url = normalize_channel_videos_url(channel_url)
    try:
        entries = (lister or _default_lister)(url, limit)
    except Exception as exc:
        logger.warning("Could not list videos for channel %s: %s", url, _ascii(str(exc)))
        return []

    videos = [video for video in (_to_channel_video(entry) for entry in entries) if video]
    if not videos:
        logger.info("No listable videos for channel %s", url)
    return videos[:limit]


def _fetch_snippets(video: ChannelVideo, fetcher: CaptionFetcher) -> list[str] | None:
    try:
        texts = fetcher(video.video_id)
    except Exception as exc:
        logger.info("No captions for video %s: %s", video.video_id, _ascii(type(exc).__name__))
        return None
    return [text for text in texts if isinstance(text, str)] or None


def _opt_median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def build_format_profile(
    channel_url: str,
    *,
    video_limit: int = DEFAULT_VIDEO_LIMIT,
    caption_sample: int = DEFAULT_CAPTION_SAMPLE,
    cache_dir: Path | None = None,
    force_refresh: bool = False,
    lister: VideoLister | None = None,
    caption_fetcher: CaptionFetcher | None = None,
) -> FormatProfile | None:
    """Build (or load from cache) the format profile for a channel.

    Args:
        channel_url: Channel URL such as ``https://www.youtube.com/@SapX``.
        video_limit: How many recent uploads to list.
        caption_sample: How many of the newest uploads to fetch captions for.
        cache_dir: Profile cache directory. Defaults to ``data/cache/wojak_channel_profiles``.
        force_refresh: Ignore the cached profile and rebuild it.
        lister: Injectable replacement for the yt-dlp listing.
        caption_fetcher: Injectable replacement for the caption fetch.

    Returns:
        The profile, or None when the channel cannot be listed.
    """
    if not isinstance(channel_url, str) or not channel_url.strip():
        logger.info("Skipping channel profile: empty channel URL")
        return None
    url = normalize_channel_videos_url(channel_url)

    try:
        directory = (
            Path(cache_dir)
            if cache_dir is not None
            else get_data_dir().joinpath(*PROFILE_CACHE_SUBDIR)
        )
        cache_path = directory / f"{_url_key(url)}.json"
        if not force_refresh:
            cached = _read_profile(cache_path, url)
            if cached is not None:
                return cached

        videos = list_channel_videos(url, limit=video_limit, lister=lister)
        if not videos:
            return None

        fetcher = caption_fetcher or _default_caption_fetcher
        segment_counts: list[float] = []
        caption_lengths: list[float] = []
        captioned = 0
        for video in videos[: max(caption_sample, 0)]:
            snippets = _fetch_snippets(video, fetcher)
            if snippets is None:
                continue
            captioned += 1
            segment_counts.append(float(len(snippets)))
            caption_lengths.extend(float(len(text.strip())) for text in snippets if text.strip())

        median_segments = _opt_median(segment_counts)
        median_chars = _opt_median(caption_lengths)
        profile = FormatProfile(
            channel_url=url,
            video_count=len(videos),
            median_duration_seconds=_opt_median(
                [video.duration_seconds for video in videos if video.duration_seconds]
            ),
            title_patterns=summarize_title_patterns(video.title for video in videos),
            segment_count_estimate=None if median_segments is None else round(median_segments),
            typical_caption_chars=None if median_chars is None else round(median_chars),
            captioned_videos=captioned,
            recent_videos=tuple(videos),
        )
        _write_profile(cache_path, profile)
        return profile
    except Exception:
        logger.warning("Channel profile failed unexpectedly for %s", url, exc_info=True)
        return None


def _url_key(url: str) -> str:
    return hashlib.sha256(url.casefold().encode("utf-8")).hexdigest()


def _write_profile(path: Path, profile: FormatProfile) -> None:
    try:
        atomic_write(path, json.dumps(asdict(profile), ensure_ascii=False, indent=2))
    except OSError as exc:
        logger.warning("Could not write channel profile cache for %s: %s", profile.channel_url, exc)


def _read_profile(path: Path, url: str) -> FormatProfile | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return FormatProfile(
            channel_url=str(data["channel_url"]),
            video_count=int(data["video_count"]),
            median_duration_seconds=_opt_float(data["median_duration_seconds"]),
            title_patterns=tuple((str(name), int(count)) for name, count in data["title_patterns"]),
            segment_count_estimate=_opt_int(data["segment_count_estimate"]),
            typical_caption_chars=_opt_int(data["typical_caption_chars"]),
            captioned_videos=int(data["captioned_videos"]),
            recent_videos=tuple(
                ChannelVideo(
                    video_id=str(item["video_id"]),
                    title=str(item["title"]),
                    duration_seconds=_opt_float(item["duration_seconds"]),
                )
                for item in data["recent_videos"]
            ),
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        logger.warning("Ignoring unreadable channel profile cache for %s: %s", url, exc)
        return None


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


# ==============================================================================
# Wikipedia fact cards (optional topic context)
# ==============================================================================


@dataclass(frozen=True)
class FactCard:
    """A factual summary for one topic, with the page it came from."""

    topic: str
    title: str
    summary: str
    source_url: str


class _NotFoundError(Exception):
    """Wikipedia answered HTTP 404 for the requested page."""


class _FetchError(Exception):
    """A request failed for a non-404 reason, after any retries."""


def _default_opener(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_RESPONSE_BYTES)
    return body.decode("utf-8", errors="replace")


def _fetch_text(url: str, opener: Opener, topic: str) -> str:
    """Return the response body, retrying transient failures a bounded number of times."""
    last_error: BaseException | None = None
    for attempt in range(MAX_RETRIES + 1):
        if attempt:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        try:
            response = opener(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise _NotFoundError(url) from exc
            if exc.code == 429 or exc.code >= 500:
                last_error = exc
                continue
            raise _FetchError(f"HTTP {exc.code} for topic {topic!r}") from exc
        except OSError as exc:
            # Covers URLError (no network, DNS failure) and socket timeouts.
            last_error = exc
            continue
        except Exception as exc:
            raise _FetchError(f"{type(exc).__name__} for topic {topic!r}") from exc

        if isinstance(response, bytes):
            return response.decode("utf-8", errors="replace")
        if not isinstance(response, str):
            raise _FetchError(f"opener returned {type(response).__name__} for topic {topic!r}")
        return response

    raise _FetchError(
        f"gave up after {MAX_RETRIES + 1} attempts for topic {topic!r} ({last_error!r})"
    )


def _quote_title(title: str) -> str:
    return quote(title.replace(" ", "_"), safe="")


def _page_url(payload: dict[str, Any], page_title: str) -> str:
    content_urls = payload.get("content_urls")
    if isinstance(content_urls, dict):
        desktop = content_urls.get("desktop")
        if isinstance(desktop, dict):
            page = desktop.get("page")
            if isinstance(page, str) and page.startswith("https://"):
                return page
    return PAGE_URL_TEMPLATE.format(title=quote(page_title.replace(" ", "_"), safe="_()"))


def _parse_summary(body: str, topic: str) -> FactCard | None:
    try:
        payload = json.loads(body)
    except ValueError:
        logger.warning("Wikipedia returned malformed JSON for topic %r", topic)
        return None
    if not isinstance(payload, dict):
        logger.warning("Wikipedia returned an unexpected payload for topic %r", topic)
        return None

    extract = payload.get("extract")
    if not isinstance(extract, str) or not extract.strip():
        logger.info("Wikipedia page for topic %r has no usable extract", topic)
        return None

    raw_title = payload.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) and raw_title.strip() else topic
    return FactCard(
        topic=topic,
        title=title,
        summary=" ".join(extract.split()),
        source_url=_page_url(payload, title),
    )


def _summary_card(page_title: str, topic: str, opener: Opener) -> FactCard | None:
    url = SUMMARY_URL_TEMPLATE.format(title=_quote_title(page_title))
    body = _fetch_text(url, opener, topic)
    return _parse_summary(body, topic)


def _search_title(topic: str, opener: Opener) -> str | None:
    query = urlencode({"action": "query", "list": "search", "srsearch": topic, "format": "json"})
    body = _fetch_text(f"{SEARCH_URL}?{query}", opener, topic)
    try:
        payload = json.loads(body)
    except ValueError:
        logger.warning("Wikipedia search returned malformed JSON for topic %r", topic)
        return None
    try:
        best_title = payload["query"]["search"][0]["title"]
    except (KeyError, IndexError, TypeError):
        logger.info("No Wikipedia search hit for topic %r", topic)
        return None
    if isinstance(best_title, str) and best_title.strip():
        return best_title.strip()
    return None


def _lookup(topic: str, opener: Opener) -> FactCard | None:
    """Fetch a card directly by title, falling back to the search endpoint on 404."""
    try:
        return _summary_card(topic, topic, opener)
    except _NotFoundError:
        logger.info("No Wikipedia page titled %r; trying search", topic)
    except _FetchError as exc:
        logger.warning("Wikipedia lookup failed: %s", exc)
        return None

    try:
        match = _search_title(topic, opener)
        if match is None:
            return None
        card = _summary_card(match, topic, opener)
    except (_NotFoundError, _FetchError) as exc:
        logger.warning("Wikipedia search fallback failed for topic %r: %s", topic, exc)
        return None

    if card is not None:
        logger.info("Matched topic %r to Wikipedia page %r via search", topic, match)
    return card


def _fact_cache_file(directory: Path, topic: str) -> Path:
    key = hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()
    return directory / f"{key}.json"


def _read_fact_cache(path: Path, topic: str) -> FactCard | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return FactCard(
            topic=topic,
            title=str(payload["title"]),
            summary=str(payload["summary"]),
            source_url=str(payload["source_url"]),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Ignoring unreadable Wikipedia cache entry for topic %r: %s", topic, exc)
        return None


def _write_fact_cache(path: Path, card: FactCard) -> None:
    try:
        atomic_write(path, json.dumps(asdict(card), ensure_ascii=False, indent=2))
    except OSError as exc:
        logger.warning("Could not write Wikipedia cache for topic %r: %s", card.topic, exc)


def fetch_fact_card(
    topic: str,
    *,
    cache_dir: Path | None = None,
    force_refresh: bool = False,
    opener: Opener | None = None,
) -> FactCard | None:
    """Return a Wikipedia FactCard for ``topic`` from cache or the network, or None.

    Args:
        topic: Topic to look up, e.g. "Hospital" or "Universal healthcare".
        cache_dir: Where to store cached JSON. Defaults to ``data/cache/wojak_fact_cards``.
        force_refresh: Skip the cache read and fetch again (successful results are re-cached).
        opener: Callable taking a URL and returning the body. Defaults to urllib with a
            6 second timeout. Tests inject a fake here.
    """
    if not isinstance(topic, str) or not topic.strip():
        logger.info("Skipping Wikipedia fact lookup: empty topic")
        return None
    clean_topic = topic.strip()

    try:
        directory = (
            Path(cache_dir)
            if cache_dir is not None
            else get_data_dir().joinpath(*FACT_CACHE_SUBDIR)
        )
        cache_path = _fact_cache_file(directory, clean_topic)
        if not force_refresh:
            cached = _read_fact_cache(cache_path, clean_topic)
            if cached is not None:
                return cached

        card = _lookup(clean_topic, opener or _default_opener)
        if card is None:
            return None
        _write_fact_cache(cache_path, card)
        return card
    except Exception:
        logger.warning(
            "Wikipedia fact lookup failed unexpectedly for topic %r", clean_topic, exc_info=True
        )
        return None


def fetch_fact_cards(topics: list[str], **kwargs: Any) -> list[FactCard]:
    """Fetch a card per topic, skipping failed lookups and keeping the input order."""
    cards: list[FactCard] = []
    for topic in topics:
        card = fetch_fact_card(topic, **kwargs)
        if card is not None:
            cards.append(card)
    return cards


def _first_sentence(text: str) -> str:
    """Return text up to the first sentence end, skipping common abbreviations and initials."""
    for match in re.finditer(r"[.!?](?=\s|$)", text):
        if match.group() == ".":
            words = text[: match.start()].split()
            token = words[-1].lower() if words else ""
            if token in _ABBREVIATIONS or "." in token or (len(token) == 1 and token.isalpha()):
                continue
        return text[: match.end()]
    return text


def _trim_to_word(text: str, max_chars: int) -> str:
    """Trim text to at most max_chars, cutting only at a whitespace boundary."""
    if len(text) <= max_chars:
        candidate = text
    elif text[max_chars].isspace():
        candidate = text[:max_chars]
    else:
        head = text[:max_chars]
        boundary = max((i for i, ch in enumerate(head) if ch.isspace()), default=-1)
        if boundary < 0:
            # A single word longer than the limit: no clean cut exists.
            return ""
        candidate = head[:boundary]
    return candidate.rstrip(_TRAILING_JUNK)


def summarize_for_caption(card: FactCard, max_chars: int = 120) -> str:
    """Return the first sentence of the card's summary, trimmed to ``max_chars``.

    The cut happens at a word boundary, so the result never ends mid-word or with a
    trailing space. Returns "" if the first word alone exceeds ``max_chars``.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    text = " ".join(card.summary.split())
    if not text:
        return ""
    return _trim_to_word(_first_sentence(text), max_chars)
