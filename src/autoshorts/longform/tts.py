"""Per-section narration audio for long-form videos (Edge neural TTS, then Kokoro)."""

from __future__ import annotations

import asyncio
import logging
import wave
from pathlib import Path
from typing import Any

from autoshorts.longform.types import LongformScript, SectionAudio

logger = logging.getLogger(__name__)

VOICES = ("en-US-ChristopherNeural", "en-US-AriaNeural")


def _edge(
    text: str, voice: str, audio: Path, words_path: Path
) -> tuple[list[dict[str, Any]], float]:
    from autoshorts.research_agent import moneyprinter

    _path, words, duration = asyncio.run(
        moneyprinter._async_synthesize_edge_tts(text, voice, audio, words_path)
    )
    if not audio.exists() or audio.stat().st_size == 0:
        raise RuntimeError("Edge TTS produced no audio")
    return list(words), float(duration)


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _kokoro(text: str, voice: str, wav_path: Path) -> tuple[list[dict[str, Any]], float]:
    from autoshorts.media.compositor import FasterWhisperTranscriber
    from autoshorts.media.kokoro_fallback import synthesize_kokoro

    out = Path(synthesize_kokoro(text, voice, wav_path))
    words = list(FasterWhisperTranscriber().align_words(out))
    try:
        duration = _wav_duration(out)
    except Exception:
        duration = float(words[-1]["end"]) if words else 0.0
    return words, duration


def synthesize_sections(
    script: LongformScript, work_dir: Path, voice: str | None = None
) -> list[SectionAudio]:
    """Synthesize every section. Never produces silent or tone audio."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    results: list[SectionAudio] = []
    for i, section in enumerate(script.sections):
        v = voice or VOICES[i % len(VOICES)]
        text = section.narration
        mp3 = work_dir / f"section_{i:02d}.mp3"
        words_json = work_dir / f"section_{i:02d}_words.json"
        try:
            words, duration = _edge(text, v, mp3, words_json)
            path = mp3
        except Exception as edge_err:
            logger.warning("Edge TTS failed for section %d (%s); trying Kokoro.", i, edge_err)
            wav = work_dir / f"section_{i:02d}.wav"
            try:
                words, duration = _kokoro(text, v, wav)
                path = wav
            except Exception as kokoro_err:
                raise RuntimeError(
                    f"No TTS available for section {i}: Edge TTS failed ({edge_err}); "
                    f"Kokoro failed ({kokoro_err}). Check your network connection or install kokoro."
                ) from kokoro_err
        if duration <= 0 and words:
            duration = float(words[-1].get("end", 0.0))
        results.append(
            SectionAudio(section=section, audio_path=path, words=words, duration=duration)
        )
    return results


def total_duration(sections: list[SectionAudio]) -> float:
    return float(sum(s.duration for s in sections))
