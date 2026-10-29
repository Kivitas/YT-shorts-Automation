"""Optional client for the hosted each::labs music API.

Off by default. Nothing here makes a network call unless the caller has checked
``is_enabled`` (config flag on AND EACHLABS_API_KEY present).

Endpoint and request shape follow the MIT-licensed genmedia skills docs
(third_party/genmedia_skills/music-generation/SKILL.md): an OpenAI-compatible
chat completion. The generated track is referenced by URL inside the assistant
message content; the docs do not document a separate response schema or a file
host, so the URL is extracted from the text and downloads are restricted to
eachlabs hosts.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from autoshorts.core import settings_manager

logger = logging.getLogger(__name__)

API_URL = "https://eachsense-agent.core.eachlabs.run/v1/chat/completions"
API_KEY_ENV = "EACHLABS_API_KEY"
DEFAULT_MODEL = "eachsense/beta"
DEFAULT_TIMEOUT_SEC = 120.0
ALLOWED_HOST_SUFFIXES = (".eachlabs.ai", ".eachlabs.run")
MAX_PROMPT_CHARS = 1000
MIN_DURATION_SEC = 5
MAX_DURATION_SEC = 300
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_URL_RE = re.compile(r"https://[^\s\"'<>()\[\]]+")
_AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".flac")
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi")


@dataclass
class EachLabsAsset:
    """Represent an AI-generated media asset from each::labs."""

    url: str
    media_type: str = "audio"
    prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.url


class EachLabsError(RuntimeError):
    """Any failure in the each::labs integration. Messages never contain the API key."""


def _api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        # Fallback: the app's .env reader. The value is returned only, never logged.
        try:
            key = settings_manager.read_env_file(None).get(API_KEY_ENV, "").strip()
        except Exception:
            logger.warning("Could not read %s fallback from .env", API_KEY_ENV)
            key = ""
    return key


def is_enabled(settings_flag: bool) -> bool:
    """True only when the config flag is on and an API key is available."""
    return bool(settings_flag) and bool(_api_key())


def _validate_prompt(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise EachLabsError("prompt must be a string")
    text = prompt.strip()
    if not text:
        raise EachLabsError("prompt must not be empty")
    if len(text) > MAX_PROMPT_CHARS:
        raise EachLabsError(f"prompt must be at most {MAX_PROMPT_CHARS} characters")
    return text


def _validate_duration(duration_sec: int) -> int:
    if isinstance(duration_sec, bool) or not isinstance(duration_sec, int):
        raise EachLabsError("duration_sec must be an integer")
    if not MIN_DURATION_SEC <= duration_sec <= MAX_DURATION_SEC:
        raise EachLabsError(
            f"duration_sec must be between {MIN_DURATION_SEC} and {MAX_DURATION_SEC}"
        )
    return duration_sec


def _extract_text(payload: Any) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise EachLabsError("response has no choices[0].message.content") from None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text") for p in content if isinstance(p, dict)]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def _pick_audio_url(text: str) -> str:
    urls = [u.rstrip(".,;:!?") for u in _URL_RE.findall(text)]
    for url in urls:
        if urlsplit(url).path.lower().endswith(_AUDIO_EXTENSIONS):
            return url
    if urls:
        return urls[0]
    raise EachLabsError("response contained no audio URL")


def generate_music(
    prompt: str,
    *,
    duration_sec: int = 45,
    timeout_sec: float = 120.0,
    client: httpx.Client | None = None,
) -> str:
    """Request an instrumental track and return the URL of the generated audio.

    Does not download the file; pass the URL to ``download_asset``.
    """
    text = _validate_prompt(prompt)
    duration = _validate_duration(duration_sec)
    key = _api_key()
    if not key:
        raise EachLabsError(f"{API_KEY_ENV} is not set")

    body = {
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Generate an instrumental music track, no vocals, {duration} seconds long. "
                    f"{text}"
                ),
            }
        ],
        "stream": False,
    }
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    timeout = httpx.Timeout(timeout_sec)
    try:
        if client is None:
            with httpx.Client(timeout=timeout) as own:
                resp = own.post(API_URL, json=body, headers=headers)
        else:
            resp = client.post(API_URL, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise EachLabsError(f"each::labs request failed: {type(exc).__name__}") from None

    if resp.status_code != 200:
        raise EachLabsError(f"each::labs returned HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError:
        raise EachLabsError("each::labs returned a non-JSON response") from None
    return _pick_audio_url(_extract_text(payload))


def _resolve_enabled(enabled: bool) -> bool:
    if enabled:
        return True
    return os.environ.get("AUTOSHORTS_EACHLABS_ENABLED", "").lower() in ("1", "true", "yes")


def _pick_video_url(text: str) -> str:
    urls = [u.rstrip(".,;:!?") for u in _URL_RE.findall(text)]
    for url in urls:
        if urlsplit(url).path.lower().endswith(_VIDEO_EXTENSIONS):
            return url
    if urls:
        return urls[0]
    raise EachLabsError("response contained no video URL")


def _chat_completion_request(
    messages: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    enabled: bool = False,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    client: httpx.Client | None = None,
) -> str:
    if not _resolve_enabled(enabled):
        raise EachLabsError("each::labs integration is disabled")

    key = (api_key or "").strip() or _api_key()
    if not key:
        raise EachLabsError(f"{API_KEY_ENV} is not set")

    body = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    req_timeout = httpx.Timeout(timeout)
    try:
        if client is None:
            with httpx.Client(timeout=req_timeout) as own:
                resp = own.post(API_URL, json=body, headers=headers)
        else:
            resp = client.post(API_URL, json=body, headers=headers, timeout=req_timeout)
    except httpx.HTTPError as exc:
        raise EachLabsError(f"each::labs request failed: {type(exc).__name__}") from None

    if resp.status_code != 200:
        raise EachLabsError(f"each::labs returned HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError:
        raise EachLabsError("each::labs returned a non-JSON response") from None

    return _extract_text(payload)


def generate_sound_effects(
    prompt: str,
    *,
    duration_seconds: int = 3,
    api_key: str | None = None,
    enabled: bool = False,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    client: httpx.Client | None = None,
) -> EachLabsAsset:
    """Generate custom sound effects using each::sense AI."""
    text = _validate_prompt(prompt)
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int) or duration_seconds < 1:
        raise EachLabsError("duration_seconds must be a positive integer")

    content = f"Generate a sound effect: {text}. {duration_seconds} seconds long."
    messages = [{"role": "user", "content": content}]
    raw_text = _chat_completion_request(
        messages,
        api_key=api_key,
        enabled=enabled,
        model=model,
        timeout=timeout,
        client=client,
    )
    url = _pick_audio_url(raw_text)
    return EachLabsAsset(
        url=url,
        media_type="audio",
        prompt=text,
        metadata={"duration_seconds": duration_seconds, "model": model},
    )


def generate_tts(
    text: str,
    *,
    voice: str = "Rachel",
    language: str = "en",
    api_key: str | None = None,
    enabled: bool = False,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    client: httpx.Client | None = None,
) -> EachLabsAsset:
    """Convert text to natural-sounding speech using each::sense elevenlabs-tts."""
    cleaned = _validate_prompt(text)
    content = f"Convert this text to speech in {language} with voice '{voice}': {cleaned}"
    messages = [{"role": "user", "content": content}]
    raw_text = _chat_completion_request(
        messages,
        api_key=api_key,
        enabled=enabled,
        model=model,
        timeout=timeout,
        client=client,
    )
    url = _pick_audio_url(raw_text)
    return EachLabsAsset(
        url=url,
        media_type="audio",
        prompt=cleaned,
        metadata={"voice": voice, "language": language, "model": model},
    )


def generate_song(
    prompt: str,
    *,
    lyrics: str | None = None,
    duration_seconds: int = 30,
    api_key: str | None = None,
    enabled: bool = False,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    client: httpx.Client | None = None,
) -> EachLabsAsset:
    """Generate complete song with vocals and instrumentals."""
    text = _validate_prompt(prompt)
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int) or duration_seconds < 5:
        raise EachLabsError("duration_seconds must be an integer >= 5")

    if lyrics and lyrics.strip():
        content = (
            f"Generate a song, {duration_seconds} seconds long: {text}\n\n"
            f"Lyrics:\n{lyrics.strip()}"
        )
    else:
        content = (
            f"Generate a song with vocals and instrumentals, {duration_seconds} seconds long: {text}"
        )

    messages = [{"role": "user", "content": content}]
    raw_text = _chat_completion_request(
        messages,
        api_key=api_key,
        enabled=enabled,
        model=model,
        timeout=timeout,
        client=client,
    )
    url = _pick_audio_url(raw_text)
    return EachLabsAsset(
        url=url,
        media_type="audio",
        prompt=text,
        metadata={"duration_seconds": duration_seconds, "lyrics": lyrics, "model": model},
    )


def edit_video(
    video_url: str,
    prompt: str,
    *,
    api_key: str | None = None,
    enabled: bool = False,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    client: httpx.Client | None = None,
) -> EachLabsAsset:
    """Edit or style transform existing video using each::sense multimodal pipeline."""
    if not isinstance(video_url, str) or not video_url.strip():
        raise EachLabsError("video_url must not be empty")
    text = _validate_prompt(prompt)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": video_url.strip()}},
            ],
        }
    ]
    raw_text = _chat_completion_request(
        messages,
        api_key=api_key,
        enabled=enabled,
        model=model,
        timeout=timeout,
        client=client,
    )
    url = _pick_video_url(raw_text)
    return EachLabsAsset(
        url=url,
        media_type="video",
        prompt=text,
        metadata={"video_url": video_url, "model": model},
    )


def _check_download_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise EachLabsError("download URL must use https")
    try:
        port = parts.port
    except ValueError:
        raise EachLabsError("download URL has an invalid port") from None
    if parts.username or parts.password or port not in (None, 443):
        raise EachLabsError("download URL must not carry credentials or a custom port")
    host = (parts.hostname or "").lower()
    if host not in ("eachlabs.ai", "eachlabs.run") and not any(host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES):
        raise EachLabsError(f"download host not allowed: {host or '<none>'}")


def _stream_to(http: httpx.Client, url: str, part: Path, timeout_sec: float) -> None:
    with http.stream("GET", url, timeout=httpx.Timeout(timeout_sec)) as resp:
        if resp.status_code != 200:
            raise EachLabsError(f"download returned HTTP {resp.status_code}")
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
            raise EachLabsError("download exceeds the 200 MB limit")
        total = 0
        with part.open("wb") as fh:
            for chunk in resp.iter_bytes(_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise EachLabsError("download exceeds the 200 MB limit")
                fh.write(chunk)


def download_asset(
    url: str,
    dest: Path,
    *,
    timeout_sec: float = 120.0,
    client: httpx.Client | None = None,
) -> Path:
    """Download an eachlabs-hosted asset to ``dest`` atomically (via a .part file)."""
    _check_download_url(url)
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    ok = False
    try:
        if client is None:
            with httpx.Client(timeout=httpx.Timeout(timeout_sec), follow_redirects=False) as own:
                _stream_to(own, url, part, timeout_sec)
        else:
            _stream_to(client, url, part, timeout_sec)
        os.replace(part, target)
        ok = True
    except httpx.HTTPError as exc:
        raise EachLabsError(f"download failed: {type(exc).__name__}") from None
    finally:
        if not ok:
            part.unlink(missing_ok=True)
    return target
