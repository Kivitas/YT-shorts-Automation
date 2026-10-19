"""Logging configuration with automatic secret redaction.

Ensures API keys, tokens, and sensitive credentials are never written to disk or console.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Set of known secrets to redact at runtime
_REGISTERED_SECRETS: set[str] = set()

# Regex patterns for common API keys, tokens, and auth headers
_SECRET_PATTERNS = [
    # General bearer tokens / auth headers
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9\-_\.~+/]+=*)"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*['\"]?)([A-Za-z0-9\-_]{16,})(['\"]?)"),
    re.compile(r"(?i)(token\s*[:=]\s*['\"]?)([A-Za-z0-9\-_]{16,})(['\"]?)"),
    re.compile(r"(?i)(client[_-]?secret\s*[:=]\s*['\"]?)([A-Za-z0-9\-_]{16,})(['\"]?)"),
    # Google AI / Gemini API keys (AIza...)
    re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b"),
    # OpenRouter API keys (sk-or-...)
    re.compile(r"\bsk-or-v1-[0-9a-fA-F]{64}\b"),
    # Generic sk-... tokens
    re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}\b"),
]


def register_secret(secret: str | None) -> None:
    """Register a secret string to be redacted from all future logs."""
    if secret and len(secret.strip()) >= 4:
        _REGISTERED_SECRETS.add(secret.strip())


def redact(val: Any) -> Any:
    """Recursively redact secrets from strings, dictionaries, lists, or objects.

    Replaces recognized API keys, bearer tokens, and registered secrets with '***REDACTED***'.
    """
    if isinstance(val, str):
        text = val
        # Check against registered exact secrets first
        for secret in _REGISTERED_SECRETS:
            if secret in text:
                text = text.replace(secret, "***REDACTED***")

        # Check against regex patterns
        for pattern in _SECRET_PATTERNS:
            # If pattern has groups (prefix, secret, optional suffix), preserve prefix/suffix
            if pattern.groups == 3:
                text = pattern.sub(r"\g<1>***REDACTED***\g<3>", text)
            elif pattern.groups == 2:
                text = pattern.sub(r"\g<1>***REDACTED***", text)
            else:
                text = pattern.sub("***REDACTED***", text)
        return text

    elif isinstance(val, Mapping):
        sensitive_keys = {
            "api_key",
            "secret",
            "token",
            "password",
            "client_secret",
            "refresh_token",
        }
        redacted_dict: dict[str, Any] = {}
        for k, v in val.items():
            if any(s in str(k).lower() for s in sensitive_keys) and isinstance(v, str):
                redacted_dict[k] = "***REDACTED***"
            else:
                redacted_dict[k] = redact(v)
        return redacted_dict

    elif isinstance(val, (list, tuple, set)):
        items = [redact(item) for item in val]
        return type(val)(items)

    return val


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from log messages and formatting arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(redact(arg) for arg in record.args)
        return True


class RedactingFormatter(logging.Formatter):
    """Logging formatter that ensures all formatted output is sanitized."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact(formatted)


class LogStreamHandler(logging.Handler):
    """Thread-safe in-memory ring-buffer logging handler with live queue subscribers."""

    def __init__(self, capacity: int = 1000) -> None:
        super().__init__()
        self.capacity = capacity
        self._buffer: deque[str] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[str]]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._lock:
                self._buffer.append(msg)
                active_subs = list(self._subscribers)

            for loop, q in active_subs:
                if loop.is_closed():
                    continue
                try:
                    loop.call_soon_threadsafe(self._push_to_queue, q, msg)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)

    @staticmethod
    def _push_to_queue(q: asyncio.Queue[str], msg: str) -> None:
        try:
            if q.full():
                try:
                    q.get_nowait()
                except Exception:
                    pass
            q.put_nowait(msg)
        except Exception:
            pass

    def subscribe(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str]) -> None:
        """Register an asyncio queue to receive live streaming log records."""
        with self._lock:
            self._subscribers.append((loop, queue))

    def unsubscribe(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str]) -> None:
        """Unregister an active subscriber queue."""
        with self._lock:
            self._subscribers = [
                (l, q) for (l, q) in self._subscribers if not (l is loop and q is queue)
            ]

    def get_history(self, limit: int | None = None) -> list[str]:
        """Retrieve recent buffered log records."""
        with self._lock:
            items = list(self._buffer)
            if limit is not None and limit < len(items):
                return items[-limit:]
            return items

    def clear(self) -> None:
        """Clear the in-memory ring-buffer."""
        with self._lock:
            self._buffer.clear()


_LOG_STREAM_HANDLER: LogStreamHandler | None = None


def get_log_stream_handler(capacity: int = 1000) -> LogStreamHandler:
    """Return the global in-memory ring-buffer LogStreamHandler instance."""
    global _LOG_STREAM_HANDLER
    if _LOG_STREAM_HANDLER is None:
        _LOG_STREAM_HANDLER = LogStreamHandler(capacity=capacity)
        fmt = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
        formatter = RedactingFormatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        _LOG_STREAM_HANDLER.setFormatter(formatter)
        _LOG_STREAM_HANDLER.addFilter(RedactingFilter())

        root_logger = logging.getLogger()
        if _LOG_STREAM_HANDLER not in root_logger.handlers:
            root_logger.addHandler(_LOG_STREAM_HANDLER)
        if root_logger.level > logging.INFO or root_logger.level == logging.NOTSET:
            root_logger.setLevel(logging.INFO)

        autoshorts_logger = logging.getLogger("autoshorts")
        if _LOG_STREAM_HANDLER not in autoshorts_logger.handlers:
            autoshorts_logger.addHandler(_LOG_STREAM_HANDLER)

    return _LOG_STREAM_HANDLER


class SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that ignores I/O errors on closed streams during test runner capture."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.stream is None or (hasattr(self.stream, "closed") and self.stream.closed):
                return
            msg = self.format(record)
            self.stream.write(msg + self.terminator)
            self.flush()
        except (ValueError, OSError):
            pass
        except Exception:
            self.handleError(record)


def setup_logging(
    level: str = "INFO",
    log_file: Path | str | None = None,
    attach_stream_handler: bool = True,
) -> logging.Logger:
    """Configure root and autoshorts logging with the redacting filter.

    Args:
        level: Logging level string ('DEBUG', 'INFO', 'WARNING', 'ERROR').
        log_file: Optional file destination for logs.
        attach_stream_handler: Whether to attach the live SSE ring-buffer handler.

    Returns:
        The configured 'autoshorts' logger instance.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Format: 2026-10-19 12:00:00 [INFO] [autoshorts.core]: Message
    fmt = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
    formatter = RedactingFormatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
    redacting_filter = RedactingFilter()

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clean existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = SafeStreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redacting_filter)
    root_logger.addHandler(console_handler)

    # Optional file handler
    if log_file:
        file_path = Path(log_file).resolve()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redacting_filter)
        root_logger.addHandler(file_handler)

    # Stream ring-buffer handler for real-time SSE
    if attach_stream_handler:
        stream_handler = get_log_stream_handler()
        if stream_handler not in root_logger.handlers:
            root_logger.addHandler(stream_handler)

    # Dedicated autoshorts logger
    logger = logging.getLogger("autoshorts")
    logger.setLevel(numeric_level)
    return logger
