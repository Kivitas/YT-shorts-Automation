"""Kokoro TTS fallback (nazdridoy/kokoro-tts CLI) used when Edge neural TTS is unavailable."""

from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path

from autoshorts.core.paths import get_data_dir, get_work_dir

logger = logging.getLogger(__name__)

MODEL_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
_UV_TOOL_PATH = Path.home() / ".local" / "bin" / "kokoro-tts.exe"
_TIMEOUT_SEC = 300

_VOICE_MAP = {
    "en-us-christopherneural": "am_michael",
    "en-us-guyneural": "am_adam",
    "en-us-arianeural": "af_sarah",
}
_DEFAULT_VOICE = "af_sarah"


def _model_dir() -> Path:
    return get_data_dir() / "models" / "kokoro"


def _find_binary() -> str | None:
    found = shutil.which("kokoro-tts")
    if found:
        return found
    if _UV_TOOL_PATH.is_file():
        return str(_UV_TOOL_PATH)
    return None


def map_voice(edge_voice: str) -> str:
    """Map an Edge voice name to a Kokoro voice id (Kokoro ids pass through)."""
    key = (edge_voice or "").strip()
    if key.lower() in _VOICE_MAP:
        return _VOICE_MAP[key.lower()]
    if "_" in key and "-" not in key:
        return key
    return _DEFAULT_VOICE


def kokoro_available() -> bool:
    """True when the CLI binary and both model files are present."""
    if _find_binary() is None:
        return False
    model_dir = _model_dir()
    return all((model_dir / name).is_file() for name in MODEL_FILES)


def synthesize_kokoro(text: str, voice: str, output_wav: Path, speed: float = 1.0) -> Path:
    """Synthesize ``text`` to ``output_wav`` with the Kokoro CLI."""
    binary = _find_binary()
    if binary is None:
        raise RuntimeError("kokoro-tts binary not found")
    output_wav = Path(output_wav)
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    work = get_work_dir()
    work.mkdir(parents=True, exist_ok=True)
    txt = work / f"kokoro_{uuid.uuid4().hex[:8]}.txt"
    txt.write_text(text, encoding="utf-8")
    cmd = [
        binary,
        str(txt),
        str(output_wav.resolve()),
        "--voice",
        map_voice(voice),
        "--speed",
        str(speed),
        "--format",
        "wav",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_model_dir()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"kokoro-tts timed out after {_TIMEOUT_SEC}s") from e
    finally:
        txt.unlink(missing_ok=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-500:]
        raise RuntimeError(f"kokoro-tts failed (exit {proc.returncode}): {tail}")
    if not output_wav.is_file() or output_wav.stat().st_size == 0:
        raise RuntimeError("kokoro-tts produced no output file")
    return output_wav
