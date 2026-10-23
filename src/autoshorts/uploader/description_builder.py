"""Description and attribution builder for YouTube Shorts uploads.

Renders YouTube video titles and descriptions adhering to platform constraints:
- Title length <= 100 characters with guaranteed #Shorts tag.
- Description length <= 5000 UTF-8 bytes.
- Angle brackets (<, >) sanitized to prevent YouTube API 400 rejection.
- Mandatory source attribution and copyright disclaimer injection.
- Template interpolation for {title}, {channel}, {channel_url}, {video_url}, {start}, {end}, {hook}, {hashtags}.
"""

from __future__ import annotations

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
    "#Shorts #Podcasts #Education {hashtags}"
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

        rendered = raw_tpl.format(
            title=clean_title,
            channel=clean_channel,
            channel_url=clean_channel_url,
            video_url=clean_video_url,
            hook=clean_hook,
            start=formatted_start,
            end=formatted_end,
            hashtags=formatted_hashtags,
        ).strip()

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
    "build_short_title",
    "format_timestamp_display",
    "normalize_hashtags",
    "sanitize_youtube_text",
]
