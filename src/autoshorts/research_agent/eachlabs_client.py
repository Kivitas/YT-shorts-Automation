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
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from autoshorts.core import settings_manager

logger = logging.getLogger(__name__)

API_URL = "https://eachsense-agent.core.eachlabs.run/v1/chat/completions"
API_KEY_ENV = "EACHLABS_API_KEY"
ALLOWED_HOST_SUFFIXES = (".eachlabs.ai", ".eachlabs.run")
MAX_PROMPT_CHARS = 1000
MIN_DURATION_SEC = 5
MAX_DURATION_SEC = 300
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_URL_RE = re.compile(r"https://[^\s\"'<>()\[\]]+")
_AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".flac")


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
    if not any(host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES):
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
