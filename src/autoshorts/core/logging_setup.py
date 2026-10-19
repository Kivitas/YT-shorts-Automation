"""Logging configuration with automatic secret redaction.

Ensures API keys, tokens, and sensitive credentials are never written to disk or console.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

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
        sensitive_keys = {"api_key", "secret", "token", "password", "client_secret", "refresh_token"}
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


def setup_logging(
    level: str = "INFO",
    log_file: Path | str | None = None,
) -> logging.Logger:
    """Configure root and autoshorts logging with the redacting filter.

    Args:
        level: Logging level string ('DEBUG', 'INFO', 'WARNING', 'ERROR').
        log_file: Optional file destination for logs.

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
    console_handler = logging.StreamHandler()
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

    # Dedicated autoshorts logger
    logger = logging.getLogger("autoshorts")
    logger.setLevel(numeric_level)
    return logger
