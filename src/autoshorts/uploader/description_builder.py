"""Description and attribution builder for YouTube Shorts uploads.

Renders YouTube video titles and descriptions adhering to platform constraints:
- Title length <= 100 characters with guaranteed #Shorts tag.
- Description length <= 5000 UTF-8 bytes.
- Angle brackets (<, >) sanitized to prevent YouTube API 400 rejection.
- Mandatory source attribution and copyright disclaimer injection.
- Template interpolation for {title}, {channel}, {channel_url}, {video_url}, {start}, {end}, {hook}, {hashtags}.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from autoshorts.core.config import Settings, UploadConfig, load_config

MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_BYTES = 5000

DEFAULT_ATTRIBUTION_DISCLAIMER = (
    "📌 ORIGINAL SOURCE ATTRIBUTION:\n"
    'This video is a clip from "{title}". All rights belong to {channel}. Original video: {video_url}\n'
    "Channel Link: {channel_url}\n\n"
    "All rights and intellectual property belong to {channel}. If you are the copyright owner "
    "and wish for this clip to be removed or modified, please contact the channel owner."
)

DEFAULT_TITLE_TEMPLATE = "{hook} | {channel} #Shorts"

DEFAULT_DESCRIPTION_TEMPLATE = (
    "{hook}\n\n"
    "---\n"
    "📌 ORIGINAL SOURCE ATTRIBUTION:\n"
    'This video is a clip from "{title}". All rights belong to {channel}. Original video: {video_url}\n'
    "Channel Link: {channel_url}\n\n"
    "All rights and intellectual property belong to {channel}. If you are the copyright owner "
    "and wish for this clip to be removed or modified, please contact the channel owner.\n\n"
    "#Podcasts #Education {hashtags}"
)


def _cut_at_whitespace(text: str) -> str:
    """Drop a trailing partial word, keeping at least the back half of the text."""
    cut = max(text.rfind(" "), text.rfind("\n"))
    if cut >= len(text) // 2:
        return text[:cut]
    return text


def truncate_on_word(text: str, max_len: int) -> str:
    """Cut text to at most max_len characters, preferring a word boundary.

    Falls back to a hard cut only when no whitespace exists in the back half of the
    allowed window (so a single very long token is still bounded).
    """
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    if text[max_len].isspace():
        # The window ends exactly on a word boundary; keep the whole window.
        return text[:max_len].rstrip()
    return _cut_at_whitespace(text[:max_len]).rstrip()


def sanitize_youtube_text(text: str) -> str:
    """Remove or sanitize characters forbidden by YouTube Data API (< and >).

    The YouTube API rejects any title or description containing raw angle brackets
    to prevent HTML injection and XSS.
    """
    if not text:
        return ""
    # Strip '<' and '>'
    cleaned = text.replace("<", "").replace(">", "")
    return cleaned.strip()


def format_timestamp_display(sec: float | str) -> str:
    """Format seconds into MM:SS (or HH:MM:SS) string."""
    try:
        val = float(sec)
    except (ValueError, TypeError):
        return str(sec)

    s = max(0.0, val)
    if s >= 3600:
        h, rem = divmod(int(s), 3600)
        m, sec_part = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec_part:02d}"
    m, sec_part = divmod(int(s), 60)
    return f"{m:02d}:{sec_part:02d}"


def normalize_hashtags(hashtags: str | Sequence[str] | None) -> str:
    """Normalize a string or list of hashtags, ensuring #Shorts is present."""
    tag_list: list[str] = []

    if isinstance(hashtags, str):
        # Split tokens
        raw_tokens = re.split(r"[\s,]+", hashtags.strip())
        for token in raw_tokens:
            t = token.strip()
            if t:
                if not t.startswith("#"):
                    t = f"#{t}"
                tag_list.append(t)
    elif isinstance(hashtags, (list, tuple, set)):
        for item in hashtags:
            t = str(item).strip()
            if t:
                if not t.startswith("#"):
                    t = f"#{t}"
                tag_list.append(t)

    # Check case-insensitively for #Shorts
    has_shorts = any(t.lower() == "#shorts" for t in tag_list)
    if not has_shorts:
        tag_list.insert(0, "#Shorts")

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tag_list:
        lower_t = t.lower()
        if lower_t not in seen:
            seen.add(lower_t)
            deduped.append(t)

    return " ".join(deduped)


# YouTube tag limits used by the upload layer: longform keeps up to 15, Shorts up to 10.
MAX_TAGS_LONGFORM = 15
MAX_TAGS_SHORTS = 10
MAX_TAGS_TOTAL_CHARS = 500  # YouTube rejects tag lists whose combined length exceeds 500.
MIN_CHAPTER_GAP_SECONDS = 10.0
CHAPTERS_MARKER = "Chapters:"
SOURCES_MARKER = "Sources and disclaimer:"


def dedupe_tags(tags: Sequence[str] | None, limit: int) -> list[str]:
    """Strip, deduplicate case-insensitively (first spelling wins), and cap a plain tag list."""
    out: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in tags or ():
        tag = re.sub(r"\s+", " ", str(raw)).strip().lstrip("#").strip()
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        if len(out) >= limit or total + len(tag) + 1 > MAX_TAGS_TOTAL_CHARS:
            break
        seen.add(key)
        out.append(tag)
        total += len(tag) + 1
    return out


def _nbytes(text: str) -> int:
    return len(text.encode("utf-8"))


def build_chapter_lines(
    sections: Sequence[tuple[float | str, str]] | None,
    min_gap: float = MIN_CHAPTER_GAP_SECONDS,
) -> list[str]:
    """Turn (start_seconds, heading) pairs into sorted "MM:SS Heading" chapter lines.

    The earliest section is always pinned to 00:00. A section that starts less than
    ``min_gap`` seconds after the previously kept one is dropped.
    """
    points: list[tuple[float, str]] = []
    for start, heading in sections or ():
        try:
            t = float(start)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(t):
            continue
        label = re.sub(r"\s+", " ", sanitize_youtube_text(str(heading))).strip()
        if not label:
            continue
        points.append((max(0.0, t), truncate_on_word(label, 80)))
    if not points:
        return []
    points.sort(key=lambda p: p[0])
    points[0] = (0.0, points[0][1])

    kept: list[tuple[float, str]] = []
    for t, label in points:
        if kept and t - kept[-1][0] < min_gap:
            continue
        kept.append((t, label))
    return [f"{format_timestamp_display(t)} {label}" for t, label in kept]


def build_chapters_block(
    sections: Sequence[tuple[float | str, str]] | None,
    min_gap: float = MIN_CHAPTER_GAP_SECONDS,
) -> str:
    """Return the chapters block ("Chapters:" plus lines), or "" when there are none."""
    lines = build_chapter_lines(sections, min_gap)
    if not lines:
        return ""
    return f"{CHAPTERS_MARKER}\n" + "\n".join(lines)


def build_sources_disclaimer(safety_note: str | None) -> str:
    """Return the "Sources and disclaimer" footer for a topic safety note, or ""."""
    note = re.sub(r"\s+", " ", sanitize_youtube_text(safety_note or "")).strip()
    if not note:
        return ""
    return f"{SOURCES_MARKER}\n{note}"


def _compose(pre: str, chapter_lines: list[str], footer: str) -> str:
    head = pre
    if chapter_lines:
        block = f"{CHAPTERS_MARKER}\n" + "\n".join(chapter_lines)
        head = f"{pre}\n\n{block}" if pre else block
    if head and footer:
        return f"{head}\n\n{footer}"
    return head or footer


def _truncate_body(text: str, budget: int) -> str:
    """Shorten body text to at most ``budget`` UTF-8 bytes, adding an ellipsis when cut."""
    if budget <= 0:
        return ""
    if _nbytes(text) <= budget:
        return text
    room = budget - 3
    if room <= 0:
        return ""
    cut = text.encode("utf-8")[:room].decode("utf-8", "ignore")
    return f"{_cut_at_whitespace(cut).rstrip()}..."


def fit_longform_description(
    description: str,
    max_bytes: int = MAX_DESCRIPTION_BYTES,
    *,
    protected: str = "",
) -> str:
    """Fit a longform description under ``max_bytes`` without touching the disclaimer.

    The chapters block is trimmed first (last chapter lines removed). Only when no
    chapters are left is the body above the footer shortened. The footer is the
    "Sources and disclaimer" block, or the text starting at ``protected`` when that
    substring is present, and it is never truncated.
    """
    text = (description or "").strip()
    if _nbytes(text) <= max_bytes:
        return text

    footer_at = -1
    marker_idx = f"\n{SOURCES_MARKER}\n"
    found = text.find(marker_idx)
    if found >= 0:
        footer_at = found + 1
    elif protected and protected in text:
        footer_at = text.find(protected)
    if footer_at < 0:
        footer_at = len(text)
    head = text[:footer_at].rstrip()
    footer = text[footer_at:].strip()

    pre = head
    lines: list[str] = []
    chapter_at = f"\n{CHAPTERS_MARKER}\n"
    padded = f"\n{head}"
    cidx = padded.find(chapter_at)
    if cidx >= 0:
        pre = padded[1:cidx].rstrip()
        lines = [ln for ln in padded[cidx + len(chapter_at) :].split("\n") if ln.strip()]

    while lines and _nbytes(_compose(pre, lines, footer)) > max_bytes:
        lines.pop()
    out = _compose(pre, lines, footer)
    if _nbytes(out) <= max_bytes:
        return out

    sep = 2 if pre and footer else 0
    budget = max_bytes - _nbytes(footer) - sep
    return _compose(_truncate_body(pre, budget), [], footer)


def build_longform_description(
    body: str,
    *,
    sections: Sequence[tuple[float | str, str]] | None = None,
    safety_note: str | None = None,
    max_bytes: int = MAX_DESCRIPTION_BYTES,
) -> str:
    """Assemble a longform description: body, optional chapters, optional sources footer.

    The footer is added only when ``safety_note`` is given and not already in ``body``.
    The result always respects ``max_bytes`` (chapters are cut first).
    """
    clean_body = sanitize_youtube_text(body or "")
    chapters = build_chapters_block(sections)
    footer = ""
    note = (safety_note or "").strip()
    if note and note not in clean_body:
        footer = build_sources_disclaimer(note)

    head = clean_body
    if chapters:
        head = f"{head}\n\n{chapters}" if head else chapters
    full = f"{head}\n\n{footer}" if head and footer else (head or footer)
    return fit_longform_description(full, max_bytes, protected=note)


class DescriptionBuilder:
    """Generates sanitized, compliant titles and descriptions for YouTube Shorts."""

    def __init__(self, config: UploadConfig | Settings | None = None) -> None:
        """Initialize DescriptionBuilder with template configuration."""
        if config is None:
            settings = load_config()
            self.upload_config = settings.upload
        elif isinstance(config, Settings):
            self.upload_config = config.upload
        elif isinstance(config, UploadConfig):
            self.upload_config = config
        else:
            self.upload_config = UploadConfig()

    def build_title(
        self,
        hook: str,
        channel: str,
        title: str = "",
        template: str | None = None,
    ) -> str:
        """Render a YouTube Shorts title adhering to <=100 chars and ensuring #Shorts.

        Args:
            hook: Primary hook text or question.
            channel: Author channel name.
            title: Original source video title (optional).
            template: Optional title template override.

        Returns:
            Sanitized title string guaranteed <= 100 characters.
        """
        raw_tpl = template or self.upload_config.title_template or DEFAULT_TITLE_TEMPLATE

        clean_hook = sanitize_youtube_text(hook)
        clean_channel = sanitize_youtube_text(channel)
        clean_title = sanitize_youtube_text(title)

        try:
            rendered = raw_tpl.format(
                hook=clean_hook,
                channel=clean_channel,
                title=clean_title,
            ).strip()
        except (KeyError, IndexError, ValueError):
            # A malformed user template must not crash the upload; use the default.
            rendered = DEFAULT_TITLE_TEMPLATE.format(
                hook=clean_hook, channel=clean_channel, title=clean_title
            ).strip()

        # Ensure #Shorts is present
        if "#shorts" not in rendered.lower():
            rendered = f"{rendered} #Shorts"

        rendered = sanitize_youtube_text(rendered)
        rendered = re.sub(r"[\x00-\x1f\x7f]+", " ", rendered).strip()
        has_words = re.search(r"[^\W_]", re.sub(r"(?i)#shorts", "", rendered))
        if not has_words:
            # Empty hook/title fallback: never emit a blank or tag-only title.
            base = clean_title or clean_channel or "Short"
            rendered = f"{base} #Shorts"

        # Enforce 100-character ceiling
        if len(rendered) <= MAX_TITLE_LENGTH:
            return rendered

        # Truncation logic: preserve the suffix (e.g. ' | Channel #Shorts' or ' #Shorts')
        tag = "#Shorts"
        suffix = f" | {clean_channel} {tag}" if clean_channel else f" {tag}"
        if len(suffix) >= MAX_TITLE_LENGTH - 10:
            suffix = f" {tag}"

        available_for_hook = max(10, MAX_TITLE_LENGTH - len(suffix) - 3)
        head = truncate_on_word(clean_hook or clean_title or "Short", available_for_hook)
        final_title = f"{head}...{suffix}"

        if len(final_title) > MAX_TITLE_LENGTH:
            # Degenerate suffix/window combination: keep the tag, cut the head.
            final_title = f"{final_title[: MAX_TITLE_LENGTH - len(suffix) - 3]}...{suffix}"
        return final_title[:MAX_TITLE_LENGTH]

    def build_description(
        self,
        title: str,
        channel: str,
        channel_url: str,
        video_url: str,
        hook: str,
        start: float | str = 0.0,
        end: float | str = 0.0,
        hashtags: str | Sequence[str] = "",
        template: str | None = None,
    ) -> str:
        """Render attribution description with required disclaimer and size bounds.

        Args:
            title: Source video title.
            channel: Creator channel name.
            channel_url: Link to creator's channel.
            video_url: Canonical link to source YouTube video.
            hook: Short hook / description opening.
            start: Section start timestamp or seconds.
            end: Section end timestamp or seconds.
            hashtags: Optional hashtags.
            template: Optional description template override.

        Returns:
            Sanitized description guaranteed <= 5000 UTF-8 bytes.
        """
        raw_tpl = (
            template or self.upload_config.description_template or DEFAULT_DESCRIPTION_TEMPLATE
        )

        clean_title = sanitize_youtube_text(title)
        clean_channel = sanitize_youtube_text(channel)
        clean_channel_url = sanitize_youtube_text(channel_url)
        clean_video_url = sanitize_youtube_text(video_url)
        clean_hook = sanitize_youtube_text(hook)
        formatted_start = format_timestamp_display(start)
        formatted_end = format_timestamp_display(end)
        formatted_hashtags = normalize_hashtags(hashtags)

        fields = {
            "title": clean_title,
            "channel": clean_channel,
            "channel_url": clean_channel_url,
            "video_url": clean_video_url,
            "hook": clean_hook,
            "start": formatted_start,
            "end": formatted_end,
            "hashtags": formatted_hashtags,
        }
        try:
            rendered = raw_tpl.format(**fields).strip()
        except (KeyError, IndexError, ValueError, AttributeError):
            # A malformed user template (unknown {field}, stray brace) must not crash the upload.
            rendered = DEFAULT_DESCRIPTION_TEMPLATE.format(**fields).strip()

        # Ensure mandatory disclaimer phrase is present
        disclaimer_key_phrase = "All rights belong to"
        original_clip_phrase = "This video is a clip from"
        if (
            disclaimer_key_phrase.lower() not in rendered.lower()
            or original_clip_phrase.lower() not in rendered.lower()
        ):
            # Inject mandatory disclaimer
            disclaimer_block = (
                f"\n\n---\n"
                f"📌 ORIGINAL SOURCE ATTRIBUTION:\n"
                f'This video is a clip from "{clean_title}". All rights belong to {clean_channel}. Original video: {clean_video_url}\n'
                f"Channel Link: {clean_channel_url}\n\n"
                f"All rights and intellectual property belong to {clean_channel}. If you are the copyright owner "
                f"and wish for this clip to be removed or modified, please contact the channel owner."
            )
            rendered = f"{rendered}{disclaimer_block}"

        # Ensure #Shorts is present in the description
        if "#shorts" not in rendered.lower():
            rendered = f"{rendered}\n\n#Shorts"

        rendered = sanitize_youtube_text(rendered)

        # Enforce 5000 byte limit
        rendered_bytes = rendered.encode("utf-8")
        if len(rendered_bytes) <= MAX_DESCRIPTION_BYTES:
            return rendered

        # Truncate cleanly while preserving UTF-8 boundaries
        # Keep the disclaimer footer intact if possible by shortening the top hook
        attribution_marker = "📌 ORIGINAL SOURCE ATTRIBUTION:"
        if attribution_marker in rendered:
            parts = rendered.split(attribution_marker, 1)
            header_part = parts[0]
            footer_part = attribution_marker + parts[1]
            footer_bytes_len = len(footer_part.encode("utf-8"))

            available_header_bytes = MAX_DESCRIPTION_BYTES - footer_bytes_len - 20
            if available_header_bytes > 50:
                header_truncated = _cut_at_whitespace(
                    header_part.encode("utf-8")[:available_header_bytes].decode("utf-8", "ignore")
                )
                rendered = f"{header_truncated.rstrip()}...\n\n{footer_part}"
                if len(rendered.encode("utf-8")) <= MAX_DESCRIPTION_BYTES:
                    return rendered

        # Fallback byte truncation (UTF-8 safe), cut at a word boundary when possible
        truncated = rendered_bytes[:MAX_DESCRIPTION_BYTES].decode("utf-8", "ignore")
        return _cut_at_whitespace(truncated).rstrip()


# Broad discovery tags that make a Short easier to surface; they are shown above the title.
VIRAL_BASE_HASHTAGS = ("#Shorts", "#Viral", "#Trending", "#Explore", "#FYP")
_HASHTAG_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "their",
        "there",
        "these",
        "those",
        "which",
        "while",
        "would",
        "could",
        "should",
        "where",
        "being",
        "great",
        "video",
        "shorts",
    }
)
MAX_HASHTAGS = 15


def build_viral_hashtags(title: str, hook: str = "", max_tags: int = MAX_HASHTAGS) -> list[str]:
    """Return a viral tag set: broad discovery tags plus keywords taken from the clip itself."""
    import re

    tags: list[str] = list(VIRAL_BASE_HASHTAGS)
    words = re.findall(r"[A-Za-z]{5,}", f"{title} {hook}")
    for word in words:
        lowered = word.lower()
        if lowered in _HASHTAG_STOPWORDS:
            continue
        tag = f"#{word.capitalize()}"
        if tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
        if len(tags) >= max_tags:
            break
    return tags[:max_tags]


def build_short_title(
    hook: str,
    channel: str,
    title: str = "",
    template: str | None = None,
) -> str:
    """Functional convenience helper to build a validated Short title."""
    builder = DescriptionBuilder()
    return builder.build_title(hook=hook, channel=channel, title=title, template=template)


def build_short_description(
    title: str,
    channel: str,
    channel_url: str,
    video_url: str,
    hook: str,
    start: float | str = 0.0,
    end: float | str = 0.0,
    hashtags: str | Sequence[str] = "",
    template: str | None = None,
) -> str:
    """Functional convenience helper to build a validated Short description with attribution."""
    builder = DescriptionBuilder()
    return builder.build_description(
        title=title,
        channel=channel,
        channel_url=channel_url,
        video_url=video_url,
        hook=hook,
        start=start,
        end=end,
        hashtags=hashtags,
        template=template,
    )


__all__ = [
    "DEFAULT_ATTRIBUTION_DISCLAIMER",
    "DEFAULT_DESCRIPTION_TEMPLATE",
    "DEFAULT_TITLE_TEMPLATE",
    "MAX_DESCRIPTION_BYTES",
    "MAX_TITLE_LENGTH",
    "DescriptionBuilder",
    "build_short_description",
    "build_chapters_block",
    "build_longform_description",
    "build_short_title",
    "build_sources_disclaimer",
    "dedupe_tags",
    "fit_longform_description",
    "format_timestamp_display",
    "normalize_hashtags",
    "sanitize_youtube_text",
    "MAX_TAGS_LONGFORM",
    "MAX_TAGS_SHORTS",
]
